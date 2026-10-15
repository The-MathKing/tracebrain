"""GitHub REST ingest: repo, issues, PRs, reviews, comments, PR files, labels, CODEOWNERS. Read-only."""

import asyncio
import re
import subprocess
import time
from collections import defaultdict
from fnmatch import fnmatch

import httpx

from .. import config
from .graphio import (
    file_key, file_node, is_bot, issue_key, label_key, link_files_to_modules, merge_edges, merge_nodes,
    module_key, module_of, person_key, pr_key, repo_key, slug, topic_key, ts,
)

API = "https://api.github.com"
MAX_BODY = 20_000
# Labels that say nothing about the subject matter; they don't become Topics.
NON_TOPIC_LABELS = {"good first issue", "help wanted", "duplicate", "invalid", "wontfix", "question",
                    "stale", "dependencies", "triage", "needs triage"}


def get_token() -> str | None:
    if config.GITHUB_TOKEN:
        return config.GITHUB_TOKEN
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class GitHub:
    """Small async GitHub REST client: retries, rate-limit sleeps, pagination, bounded concurrency."""

    def __init__(self, token: str | None = None, concurrency: int = 8, log=print, max_wait: int = 900):
        token = token if token is not None else get_token()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "tracebrain"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.AsyncClient(base_url=API, headers=headers, timeout=30, follow_redirects=True)
        self.sem = asyncio.Semaphore(concurrency)
        self.log, self.max_wait, self.requests, self.remaining = log, max_wait, 0, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()

    async def get(self, path: str, params=None, raw: bool = False) -> httpx.Response:
        headers = {"Accept": "application/vnd.github.raw"} if raw else None
        for attempt in range(6):
            async with self.sem:
                r = await self.http.get(path, params=params, headers=headers)
                self.requests += 1
            if (rem := r.headers.get("x-ratelimit-remaining")) is not None and rem.isdigit():
                if self.remaining is None or int(rem) // 250 < self.remaining // 250:
                    self.log(f"github rate limit: {rem} requests remaining")
                self.remaining = int(rem)
            limited = r.status_code in (403, 429) and (
                "retry-after" in r.headers or r.headers.get("x-ratelimit-remaining") == "0")
            if limited:
                if "retry-after" in r.headers:
                    wait = int(r.headers["retry-after"])
                else:
                    wait = int(r.headers.get("x-ratelimit-reset", time.time() + 60)) - int(time.time()) + 1
                wait = min(max(wait, 1), self.max_wait)
                self.log(f"rate limited on {path}; sleeping {wait}s")
                await asyncio.sleep(wait)
            elif r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
            else:
                return r
        r.raise_for_status()
        return r

    async def json(self, path: str, params=None):
        r = await self.get(path, params)
        r.raise_for_status()
        return r.json()

    async def paginate(self, path: str, params=None, limit: int | None = None) -> list:
        params, out, url = {"per_page": 100, **(params or {})}, [], path
        while url:
            r = await self.get(url, params)
            r.raise_for_status()
            out.extend(r.json())
            if limit and len(out) >= limit:
                return out[:limit]
            url, params = r.links.get("next", {}).get("url"), None  # next URL already carries the query
        return out

    async def gather(self, coros):
        return await asyncio.gather(*coros)


# ---------------------------------------------------------------- reference parsing

REF_RE = re.compile(
    r"(?<![\w/#&])(?:([\w.-]+/[\w.-]+))?#(\d+)\b|https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
CLOSE_KW = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*$", re.I)


def parse_refs(text: str | None, repo: str) -> tuple[set[int], set[int]]:
    """(closes, references) issue/PR numbers in the same repo mentioned in `text`."""
    closes, refs = set(), set()
    for m in REF_RE.finditer(text or ""):
        target = m.group(1) or m.group(3)
        if target and target.lower() != repo.lower():
            continue
        n = int(m.group(2) or m.group(4))
        (closes if CLOSE_KW.search(text[max(0, m.start() - 20):m.start()]) else refs).add(n)
    return closes, refs - closes


# ---------------------------------------------------------------- CODEOWNERS

def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        logins = [o[1:] for o in owners if o.startswith("@") and "/" not in o]  # skip teams and emails
        if logins:
            rules.append((pattern, logins))
    return rules


def _matches(pattern: str, path: str) -> bool:
    p = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in p
    if p in ("*", "**"):
        return True
    cands = [path] if anchored else [path] + [path.split("/", i)[-1] for i in range(1, path.count("/") + 1)]
    return any(fnmatch(c, p) or fnmatch(c, p + "/*") or c.startswith(p + "/") for c in cands)


def codeowners_rows(rules, repo: str, paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Map rules to (Person-OWNS->Module rows, Person-OWNS->File rows). A rule naming a whole top-level
    dir (or `*`) owns the Module; anything narrower owns the matching Files."""
    modules = {module_of(p) for p in paths}
    mod_rows, file_rows = [], []
    for pattern, logins in rules:
        p = pattern.strip("/").removesuffix("/**").removesuffix("/*")
        targets_mod = sorted(modules) if p in ("*", "**") else [p] if p in modules else []
        for login in logins:
            if targets_mod:
                mod_rows += [{"src": person_key(login), "dst": module_key(repo, m), "props": {"pattern": pattern}}
                             for m in targets_mod]
            else:
                file_rows += [{"src": person_key(login), "dst": file_key(repo, f), "props": {"pattern": pattern}}
                              for f in paths if _matches(pattern, f)]
    return mod_rows, file_rows


async def fetch_codeowners(gh: GitHub, repo: str) -> str | None:
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        r = await gh.get(f"/repos/{repo}/contents/{path}", raw=True)
        if r.status_code == 200:
            return r.text
    return None


async def ingest_codeowners(gh: GitHub, graph, repo: str, paths: list[str]) -> int:
    text = await fetch_codeowners(gh, repo)
    if not text:
        return 0
    rules = parse_codeowners(text)
    logins = sorted({l for _, ls in rules for l in ls})
    merge_nodes(graph, "Person", [{"key": person_key(l), "props": {"login": l, "title": l, "bot": is_bot(l),
                                   "url": f"https://github.com/{l}"}} for l in logins])
    mod_rows, file_rows = codeowners_rows(rules, repo, paths)
    return merge_edges(graph, "OWNS", "Person", "Module", mod_rows) + \
        merge_edges(graph, "OWNS", "Person", "File", file_rows[:5000])


# ---------------------------------------------------------------- issues / PRs

async def fetch_repo(gh: GitHub, repo: str) -> dict:
    return await gh.json(f"/repos/{repo}")


async def _details(gh: GitHub, repo: str, it: dict) -> dict:
    n, is_pr = it["number"], "pull_request" in it
    async def reviews_and_inline():  # inline comments always belong to a review: skip the call when none
        reviews = await gh.paginate(f"/repos/{repo}/pulls/{n}/reviews")
        return reviews, (await gh.paginate(f"/repos/{repo}/pulls/{n}/comments") if reviews else [])

    jobs = {"comments": gh.paginate(f"/repos/{repo}/issues/{n}/comments") if it.get("comments") else None}
    if is_pr:
        jobs |= {"files": gh.paginate(f"/repos/{repo}/pulls/{n}/files", limit=1000), "_rv": reviews_and_inline()}
    keys = [k for k, v in jobs.items() if v is not None]
    out = dict(zip(keys, await asyncio.gather(*(jobs[k] for k in keys))))
    if "_rv" in out:
        out["reviews"], out["review_comments"] = out.pop("_rv")
    return out


def _person(u: dict) -> dict:
    login = u["login"]
    return {"key": person_key(login), "props": {
        "login": login, "title": login, "avatar_url": u.get("avatar_url"),
        "url": u.get("html_url") or f"https://github.com/{login}", "bot": is_bot(login, u.get("type"))}}


async def ingest_issues(gh: GitHub, graph, repo: str, since: str | None = None, max_items: int = 300,
                        log=print) -> dict:
    meta = await fetch_repo(gh, repo)
    merge_nodes(graph, "Repo", [{"key": repo_key(repo), "props": {
        "name": repo, "title": repo, "description": meta.get("description"), "url": meta.get("html_url"),
        "default_branch": meta.get("default_branch"), "stars": meta.get("stargazers_count"),
        "language": meta.get("language")}}])

    params = {"state": "all", "sort": "updated", "direction": "desc"}
    if since:
        params["since"] = since
    items = await gh.paginate(f"/repos/{repo}/issues", params, limit=max_items)
    log(f"{repo}: {len(items)} issues/PRs updated{' since ' + since if since else ''}; fetching details")
    details = await gh.gather(_details(gh, repo, it) for it in items)

    people, issues, prs, labels, topics, files = {}, [], [], {}, {}, {}
    e = defaultdict(list)  # (rel, src_label, dst_label) -> rows
    stats = defaultdict(int)
    for it, d in zip(items, details):
        is_pr, n = "pull_request" in it, it["number"]
        lbl = "PullRequest" if is_pr else "Issue"
        key = pr_key(repo, n) if is_pr else issue_key(repo, n)
        names = [l["name"] for l in it.get("labels", [])]
        props = {"number": n, "title": it["title"], "body": (it.get("body") or "")[:MAX_BODY],
                 "state": it["state"], "labels": names, "url": it["html_url"], "repo": repo,
                 "created_at": it["created_at"], "updated_at": it["updated_at"], "closed_at": it.get("closed_at"),
                 "created_ts": ts(it["created_at"]), "updated_ts": ts(it["updated_at"]),
                 "closed_ts": ts(it.get("closed_at")), "comments": it.get("comments", 0),
                 "assignees": [a["login"] for a in it.get("assignees") or []]}
        if is_pr:
            merged_at = it["pull_request"].get("merged_at")
            props |= {"merged": merged_at is not None, "merged_at": merged_at, "merged_ts": ts(merged_at),
                      "draft": it.get("draft", False)}
            prs.append({"key": key, "props": props})
            stats["prs"] += 1
        else:
            issues.append({"key": key, "props": props})
            stats["issues"] += 1
        e[("IN_REPO", lbl, "Repo")].append({"src": key, "dst": repo_key(repo)})

        if it.get("user"):
            people[it["user"]["login"]] = _person(it["user"])
            e[("AUTHORED", "Person", lbl)].append({"src": person_key(it["user"]["login"]), "dst": key})
        for l in it.get("labels", []):
            labels[l["name"]] = {"key": label_key(repo, l["name"]), "props": {
                "name": l["name"], "title": l["name"], "color": l.get("color"), "repo": repo,
                "description": l.get("description")}}
            e[("HAS_LABEL", lbl, "Label")].append({"src": key, "dst": label_key(repo, l["name"])})
            if l["name"].lower() not in NON_TOPIC_LABELS and slug(l["name"]):
                topics[topic_key(l["name"])] = {"key": topic_key(l["name"]), "props": {
                    "name": l["name"].lower(), "title": l["name"].lower(), "source": "label"}}
                e[("ABOUT", lbl, "Topic")].append({"src": key, "dst": topic_key(l["name"])})

        # COMMENTED_ON: issue comments + inline review comments, aggregated per person
        agg = {}
        for c in (d.get("comments") or []) + (d.get("review_comments") or []):
            if not c.get("user"):
                continue
            login = c["user"]["login"]
            people.setdefault(login, _person(c["user"]))
            cnt, last = agg.get(login, (0, ""))
            agg[login] = (cnt + 1, max(last, c.get("created_at") or ""))
            stats["comments"] += 1
        for login, (cnt, last) in agg.items():
            e[("COMMENTED_ON", "Person", lbl)].append(
                {"src": person_key(login), "dst": key, "props": {"count": cnt, "last_at": last, "last_ts": ts(last)}})

        if is_pr:
            author = (it.get("user") or {}).get("login")
            by = defaultdict(list)
            for r in d.get("reviews") or []:
                if r.get("user") and r.get("submitted_at") and r["user"]["login"] != author:
                    people.setdefault(r["user"]["login"], _person(r["user"]))
                    by[r["user"]["login"]].append(r)
            for login, rs in by.items():
                rs.sort(key=lambda r: r["submitted_at"])
                decisive = [r["state"] for r in rs if r["state"] != "COMMENTED"]
                e[("REVIEWED", "Person", "PullRequest")].append({"src": person_key(login), "dst": key, "props": {
                    "state": (decisive or ["COMMENTED"])[-1], "at": rs[-1]["submitted_at"],
                    "at_ts": ts(rs[-1]["submitted_at"]), "count": len(rs)}})
                stats["reviews"] += len(rs)
            for f in d.get("files") or []:
                files[f["filename"]] = file_node(repo, f["filename"])
                e[("MODIFIES", "PullRequest", "File")].append({"src": key, "dst": file_key(repo, f["filename"]),
                    "props": {"additions": f.get("additions", 0), "deletions": f.get("deletions", 0),
                              "status": f.get("status")}})
                stats["pr_files"] += 1

        closes, refs = parse_refs(f"{it['title']}\n{it.get('body') or ''}", repo)
        for num in closes - {n}:
            if is_pr:
                e[("CLOSES", "PullRequest", "Issue")].append({"src": key, "dst": issue_key(repo, num)})
                e[("REFERENCES", lbl, "PullRequest")].append({"src": key, "dst": pr_key(repo, num)})  # "fixes #<pr>"
            else:
                refs.add(num)
        for num in refs - {n}:
            e[("REFERENCES", lbl, "Issue")].append({"src": key, "dst": issue_key(repo, num)})
            e[("REFERENCES", lbl, "PullRequest")].append({"src": key, "dst": pr_key(repo, num)})

    await _enrich_people(gh, graph, people)
    merge_nodes(graph, "Person", list(people.values()))
    merge_nodes(graph, "Issue", issues)
    merge_nodes(graph, "PullRequest", prs)
    merge_nodes(graph, "Label", list(labels.values()))
    merge_nodes(graph, "Topic", list(topics.values()))
    merge_nodes(graph, "File", list(files.values()))
    link_files_to_modules(graph, repo, files)
    for (rel, s, t), rows in e.items():
        n_edges = merge_edges(graph, rel, s, t, rows)
        if rel in ("CLOSES", "REFERENCES"):
            stats[rel.lower()] += n_edges
    stats["people"] = len(people)
    return dict(stats)


async def _enrich_people(gh: GitHub, graph, people: dict) -> None:
    """Fill `name` from /users/<login> for humans not yet named in the graph."""
    keys = [k for k, p in people.items() if not p["props"]["bot"]]
    known = {r[0] for r in graph.query("MATCH (p:Person) WHERE p.login IN $l AND p.name IS NOT NULL RETURN p.login",
                                       {"l": keys}).result_set}

    async def one(login):
        r = await gh.get(f"/users/{login}")
        if r.status_code == 200:
            people[login]["props"]["name"] = r.json().get("name") or login

    await gh.gather(one(l) for l in keys if l not in known)
