"""LLM decision extraction from the most-discussed issues/PRs. Only runs when an LLM is configured.

Works on any Anthropic-compatible endpoint: structured output comes from a `record_decisions` tool
call (tool_choice auto), not from output_config. The client is injectable (`llm=`) for tests.
"""

import asyncio
import hashlib
import json
import time

from .. import config
from .github import GitHub, is_bot
from .graphio import file_key, merge_edges, merge_nodes, module_key, person_key, slug, topic_key, ts

TOP_ITEMS = 40
MAX_COMMENT_CHARS = 1500
MAX_PROMPT_CHARS = 14_000

DECISION_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what was decided."},
            "rationale": {"type": "string", "description": "Why, in 1-3 sentences."},
            "status": {"type": "string", "enum": ["proposed", "accepted", "superseded", "rejected"]},
            "quote": {"type": "string", "description": "Short verbatim quote from the thread supporting it."},
            "proposer_login": {"type": "string", "description": "GitHub login of the proposer, or empty."},
            "approver_logins": {"type": "array", "items": {"type": "string"}},
            "affected_paths": {"type": "array", "items": {"type": "string"},
                               "description": "Repo file paths or top-level dirs affected."},
            "topics": {"type": "array", "items": {"type": "string"},
                       "description": "1-3 short lowercase topic names, e.g. 'query caching'."},
        },
        "required": ["summary", "rationale", "status", "quote", "proposer_login", "approver_logins",
                     "affected_paths", "topics"],
    }}},
    "required": ["decisions"],
}
DECISION_TOOL = {"name": "record_decisions", "input_schema": DECISION_SCHEMA,
                 "description": "Record the engineering decisions found in the thread (empty list if none)."}

SUPERSEDE_TOOL = {
    "name": "record_supersessions",
    "description": "Record which newer decisions contradict/replace older ones on the same topic.",
    "input_schema": {"type": "object", "required": ["pairs"], "properties": {"pairs": {"type": "array", "items": {
        "type": "object", "required": ["newer", "older"],
        "properties": {"newer": {"type": "string"}, "older": {"type": "string"}}}}}},
}

SYSTEM = (
    "You extract durable engineering decisions (architecture, API design, dependencies, conventions, "
    "scope: what was chosen or rejected and why) from GitHub issue/PR threads. Ignore routine bug fixes "
    "with no design choice, thanks, and CI chatter. Use only logins that appear in the thread. "
    "Always call the provided tool exactly once; pass an empty list when there are no decisions."
)


def decision_key(source_key: str, summary: str) -> str:
    return "decision:" + hashlib.sha1((source_key + summary).encode()).hexdigest()[:12]


def call_tool(llm, tool: dict, prompt: str, max_tokens: int = 4000) -> dict | None:
    resp = llm.messages.create(model=config.EXTRACT_MODEL, max_tokens=max_tokens, system=SYSTEM,
                               tools=[tool], messages=[{"role": "user", "content": prompt}])
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
            return block.input
    return None


def pick_comments(comments: list[dict]) -> list[dict]:
    """First 2 + last 3 + the 5 most reacted/longest, chronological, bots dropped."""
    cs = [c for c in comments if c.get("user") and not is_bot(c["user"]["login"], c["user"].get("type"))
          and (c.get("body") or "").strip()]
    cs.sort(key=lambda c: c.get("created_at") or c.get("submitted_at") or "")
    score = lambda c: (c.get("reactions") or {}).get("total_count", 0) * 3 + len(c["body"]) / 500
    chosen = {id(c): c for c in cs[:2] + cs[-3:] + sorted(cs, key=score, reverse=True)[:5]}
    return [c for c in cs if id(c) in chosen]


def build_prompt(item: dict, comments: list[dict]) -> str:
    head = (f"{item['kind']} #{item['number']} in {item['repo']} ({item['state']}) by @{item['author']}\n"
            f"Title: {item['title']}\n\n{(item['body'] or '')[:4000]}\n\n--- discussion ---\n")
    parts = []
    for c in comments:
        when = c.get("created_at") or c.get("submitted_at") or ""
        parts.append(f"@{c['user']['login']} ({when[:10]}): {c['body'][:MAX_COMMENT_CHARS]}")
    body = "\n\n".join(parts)[:MAX_PROMPT_CHARS - len(head)]
    return head + body + "\n\nCall record_decisions with the decisions made in this thread."


def _top_items(graph, repo: str, top: int) -> list[dict]:
    q = ("MATCH (n) WHERE (n:Issue OR n:PullRequest) AND n.repo = $repo "
         "AND (n.extracted_ts IS NULL OR n.extracted_ts < n.updated_ts) "
         "OPTIONAL MATCH (p:Person)-[c:COMMENTED_ON]->(n) "
         "WITH n, sum(coalesce(c.count, 0)) AS disc WHERE disc > 0 "
         "OPTIONAL MATCH (a:Person)-[:AUTHORED]->(n) "
         "RETURN n.key, labels(n)[0], n.number, n.title, n.body, n.state, n.url, "
         "coalesce(n.merged_at, n.closed_at, n.updated_at), a.login, disc ORDER BY disc DESC LIMIT $top")
    cols = ["key", "kind", "number", "title", "body", "state", "url", "decided_at", "author", "disc"]
    return [dict(zip(cols, r), repo=repo) for r in graph.query(q, {"repo": repo, "top": top}).result_set]


async def _thread(gh: GitHub, item: dict) -> list[dict]:
    base = f"/repos/{item['repo']}"
    jobs = [gh.paginate(f"{base}/issues/{item['number']}/comments")]
    if item["kind"] == "PullRequest":
        jobs += [gh.paginate(f"{base}/pulls/{item['number']}/reviews"),
                 gh.paginate(f"{base}/pulls/{item['number']}/comments")]
    return [c for batch in await asyncio.gather(*jobs) for c in batch]


def write_decisions(graph, repo: str, item: dict, decisions: list[dict], participants: set[str]) -> int:
    nodes, made, decided, affects_f, affects_m, about, topics = [], [], [], [], [], [], {}
    for d in decisions:
        if not d.get("summary"):
            continue
        key = decision_key(item["key"], d["summary"])
        status = d.get("status") if d.get("status") in ("proposed", "accepted", "superseded", "rejected") \
            else "proposed"
        nodes.append({"key": key, "props": {
            "summary": d["summary"], "title": d["summary"][:140], "rationale": d.get("rationale", ""),
            "status": status, "quote": d.get("quote", ""), "decided_at": item["decided_at"],
            "decided_ts": ts(item["decided_at"]), "source_url": item["url"], "url": item["url"],
            "source_key": item["key"], "repo": repo, "topics": [t.lower() for t in d.get("topics", [])]}})
        decided.append({"src": key, "dst": item["key"]})
        roles = [(d.get("proposer_login") or "", "proposer")] + [(l, "approver") for l in d.get("approver_logins", [])]
        made += [{"src": person_key(l.lstrip("@")), "dst": key, "props": {"role": r}}
                 for l, r in roles if l.lstrip("@") in participants]
        for p in d.get("affected_paths", []):
            p = p.strip().strip("/")
            affects_f.append({"src": key, "dst": file_key(repo, p)})
            affects_m.append({"src": key, "dst": module_key(repo, p)})
        for t in d.get("topics", [])[:3]:
            if slug(t):
                topics[topic_key(t)] = {"key": topic_key(t), "props": {"name": t.lower(), "title": t.lower()}}
                about.append({"src": key, "dst": topic_key(t)})
    merge_nodes(graph, "Decision", nodes)
    merge_nodes(graph, "Topic", list(topics.values()))
    merge_edges(graph, "DECIDED_IN", "Decision", item["kind"], decided)
    merge_edges(graph, "MADE", "Person", "Decision", made)
    merge_edges(graph, "AFFECTS", "Decision", "File", affects_f)
    merge_edges(graph, "AFFECTS", "Decision", "Module", affects_m)
    merge_edges(graph, "ABOUT", "Decision", "Topic", about)
    merge_edges(graph, "ABOUT", item["kind"], "Topic", [{"src": item["key"], "dst": t} for t in topics])
    return len(nodes)


def link_supersedes(graph, repo: str, llm, log=print) -> int:
    rows = graph.query(
        "MATCH (d:Decision)-[:ABOUT]->(t:Topic) WHERE d.repo = $repo "
        "WITH t, collect({key: d.key, summary: d.summary, decided_at: d.decided_at}) AS ds "
        "WHERE size(ds) > 1 RETURN t.name, ds LIMIT 25", {"repo": repo}).result_set
    if not rows:
        return 0
    groups = {name: ds for name, ds in rows}
    prompt = ("Below are engineering decisions grouped by topic. List pairs where a NEWER decision "
              "contradicts or replaces an OLDER one in the same group. Only clear supersessions.\n\n"
              + json.dumps(groups, indent=1)[:MAX_PROMPT_CHARS]
              + "\n\nCall record_supersessions exactly once (empty list if none).")
    out = call_tool(llm, SUPERSEDE_TOOL, prompt, max_tokens=2000) or {}
    dates = {d["key"]: d["decided_at"] or "" for ds in groups.values() for d in ds}
    pairs = [{"src": p["newer"], "dst": p["older"]} for p in out.get("pairs", [])
             if p.get("newer") in dates and p.get("older") in dates and p["newer"] != p["older"]
             and dates[p["newer"]] >= dates[p["older"]]]
    n = merge_edges(graph, "SUPERSEDES", "Decision", "Decision", pairs)
    if pairs:
        graph.query("UNWIND $k AS k MATCH (d:Decision {key: k}) SET d.status = 'superseded'",
                    {"k": [p["dst"] for p in pairs]})
    log(f"{repo}: {n} SUPERSEDES links")
    return n


async def extract_decisions(gh: GitHub, graph, repo: str, llm=None, top: int = TOP_ITEMS, log=print,
                            concurrency: int = 4) -> dict:
    llm = llm or config.make_anthropic_client()
    items = _top_items(graph, repo, top)
    log(f"{repo}: extracting decisions from {len(items)} threads")
    sem = asyncio.Semaphore(concurrency)

    async def one(item):
        comments = await _thread(gh, item)
        participants = {c["user"]["login"] for c in comments if c.get("user")} | {item["author"]}
        prompt = build_prompt(item, pick_comments(comments))
        async with sem:
            try:
                out = await asyncio.to_thread(call_tool, llm, DECISION_TOOL, prompt)
            except Exception as e:  # one bad thread shouldn't kill the sync
                log(f"  {item['key']}: LLM error {e}")
                return 0
        n = write_decisions(graph, repo, item, (out or {}).get("decisions", []), participants)
        graph.query("MATCH (n {key: $k}) WHERE n:Issue OR n:PullRequest SET n.extracted_ts = $t",
                    {"k": item["key"], "t": int(time.time())})
        return n

    counts = await asyncio.gather(*(one(i) for i in items))
    sup = link_supersedes(graph, repo, llm, log) if sum(counts) else 0
    return {"threads": len(items), "decisions": sum(counts), "supersedes": sup}
