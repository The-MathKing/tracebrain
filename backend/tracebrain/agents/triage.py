"""Triage (T1): plan for a new issue (GitHub URL or raw text): related files, blast radius, duplicates,
assignee + 2 reviewers with reasons, labels and a draft comment. It NEVER posts to GitHub; see
`post_comment`, which the API gates behind TRACEBRAIN_ALLOW_GITHUB_WRITE=1."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from typing import Iterator

import httpx

from .. import config
from ..tools import registry
from ..tools.common import ToolError
from .llm import LLM, AgentRun, run_agent

ISSUE_URL_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)")

TOOLS = ["search_entities", "find_related_work", "find_experts", "blast_radius", "get_neighbors", "trace_decision",
         "shortest_path"]

_who = {"type": "object", "properties": {"key": {"type": "string"}, "login": {"type": "string"},
                                         "reason": {"type": "string"}},
        "required": ["key", "login", "reason"], "additionalProperties": False}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences: what the issue is and where it lives"},
        "related_files": {"type": "array", "items": {
            "type": "object", "properties": {"key": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["key", "reason"], "additionalProperties": False}},
        "modules": {"type": "array", "items": {"type": "string"}, "description": "Module keys"},
        "blast_radius": {"type": "array", "items": {
            "type": "object", "properties": {"key": {"type": "string"}, "distance": {"type": "integer"}},
            "required": ["key", "distance"], "additionalProperties": False},
            "description": "Files that import the affected files (from blast_radius)"},
        "duplicates": {"type": "array", "items": {
            "type": "object", "properties": {"key": {"type": "string"}, "similarity": {"type": "number"},
                                             "reason": {"type": "string"}},
            "required": ["key", "similarity", "reason"], "additionalProperties": False}},
        "assignee": _who,
        "reviewers": {"type": "array", "items": _who, "description": "Exactly 2 reviewers, not the assignee"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "draft_comment": {"type": "string", "description": "Markdown comment to post on the issue (not posted)"},
    },
    "required": ["summary", "related_files", "modules", "blast_radius", "duplicates", "assignee", "reviewers",
                 "labels", "priority", "draft_comment"],
    "additionalProperties": False,
}
SUBMIT_TOOL = {
    "name": "submit_triage_plan",
    "description": "Submit the final triage plan. Call this exactly once, at the end, after gathering evidence.",
    "input_schema": PLAN_SCHEMA,
    "strict": True,
}

SYSTEM = """You are the Triage agent of TraceBrain, working over an engineering org graph in FalkorDB.
Given a new issue, produce a triage plan grounded ONLY in graph evidence:
1. find_related_work on the issue text -> duplicate candidates and prior fixes (similarity > 0.5 is a likely duplicate).
2. search_entities (labels File/Module) on the key technical terms -> the files/modules involved.
3. blast_radius on the most relevant file(s) -> what else could break; note code owners.
4. find_experts on the most relevant file or module -> pick an assignee and exactly 2 different reviewers,
   each with a concrete reason from the evidence (owns module, authored N recent PRs touching file, reviewed ...).
   Prefer people active recently; never pick bots.
5. Suggest labels (reuse existing label names where you saw them) and a priority.
6. Write a short, friendly draft GitHub comment (Markdown) summarizing the plan and linking duplicates by #number.
Call independent tools in parallel. Then you MUST call submit_triage_plan exactly once with the plan; use only keys
that appeared in tool results. You never post to GitHub yourself.
"""


@lru_cache(maxsize=1)
def github_token() -> str | None:
    if config.GITHUB_TOKEN:
        return config.GITHUB_TOKEN
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            return out.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tracebrain"}
    tok = github_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def fetch_issue(url: str, http: httpx.Client | None = None) -> dict:
    """Read-only fetch of a GitHub issue."""
    m = ISSUE_URL_RE.search(url)
    if not m:
        raise ToolError(f"not a GitHub issue URL: {url}")
    owner, repo, n = m.group(1), m.group(2), int(m.group(3))
    client = http or httpx.Client(timeout=15)
    try:
        r = client.get(f"https://api.github.com/repos/{owner}/{repo}/issues/{n}", headers=_gh_headers())
    finally:
        if http is None:
            client.close()
    if r.status_code != 200:
        raise ToolError(f"GitHub returned {r.status_code} for {owner}/{repo}#{n}")
    j = r.json()
    return {"key": f"issue:{owner}/{repo}#{n}", "repo": f"{owner}/{repo}", "number": n, "url": j.get("html_url", url),
            "title": j.get("title") or "", "body": (j.get("body") or "")[:6000], "state": j.get("state"),
            "labels": [l["name"] if isinstance(l, dict) else str(l) for l in j.get("labels") or []],
            "author": (j.get("user") or {}).get("login")}


def post_comment(issue_url: str, body: str, http: httpx.Client | None = None) -> dict:
    """Write path. Only called by the explicit API action, which checks config.ALLOW_GITHUB_WRITE first."""
    if not config.ALLOW_GITHUB_WRITE:
        raise PermissionError("GitHub writes are disabled (set TRACEBRAIN_ALLOW_GITHUB_WRITE=1)")
    m = ISSUE_URL_RE.search(issue_url or "")
    if not m:
        raise ToolError("task has no GitHub issue URL")
    client = http or httpx.Client(timeout=15)
    try:
        r = client.post(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/issues/{m.group(3)}/comments",
                        headers=_gh_headers(), json={"body": body})
    finally:
        if http is None:
            client.close()
    if r.status_code not in (200, 201):
        raise ToolError(f"GitHub returned {r.status_code}: {r.text[:200]}")
    return {"url": r.json().get("html_url")}


def parse_input(message: str) -> tuple[str | None, str]:
    """-> (issue_url | None, remaining text)"""
    m = ISSUE_URL_RE.search(message or "")
    text = re.sub(r"^\s*triage\s*:?\s*", "", message or "", flags=re.IGNORECASE)
    return (m.group(0) if m else None), text.strip()


def _normalize_plan(plan: dict, issue: dict) -> dict:
    plan = dict(plan or {})
    for k in ("related_files", "modules", "blast_radius", "duplicates", "reviewers", "labels"):
        plan[k] = list(plan.get(k) or [])
    assignee = plan.get("assignee") or {}
    plan["reviewers"] = [r for r in plan["reviewers"] if r.get("key") != assignee.get("key")][:2]
    plan.setdefault("priority", "medium")
    plan.setdefault("summary", "")
    plan.setdefault("draft_comment", "")
    plan["issue"] = {k: issue.get(k) for k in ("key", "url", "title", "number", "repo")}
    plan["posted"] = False
    return plan


def fallback_plan(graph, issue: dict) -> dict:
    """Deterministic plan straight from the tools (used when the model never submits one)."""
    text = f"{issue.get('title', '')} {issue.get('body', '')}"
    dups = registry.dispatch(graph, "find_related_work", {"text": text, "limit": 5})["data"]["candidates"]
    files = registry.dispatch(graph, "search_entities", {"query": text[:300], "labels": ["File"], "limit": 5})["data"]["results"]
    target = files[0]["key"] if files else text[:200]
    experts = registry.dispatch(graph, "find_experts", {"target": target, "limit": 3})["data"]["experts"]
    br = registry.dispatch(graph, "blast_radius", {"key": files[0]["key"]})["data"] if files else {}

    def who(e):
        return {"key": e["key"], "login": e["login"] or "", "reason": f"expertise score {e['score']} ({e['breakdown']})"}

    return {
        "summary": f"Auto-generated plan for: {issue.get('title') or text[:80]}",
        "related_files": [{"key": f["key"], "reason": "matches issue terms"} for f in files],
        "modules": br.get("modules", []),
        "blast_radius": [{"key": a["key"], "distance": a["distance"]} for a in br.get("affected_files", [])[:15]],
        "duplicates": [{"key": d["key"], "similarity": d["similarity"], "reason": "similar text"} for d in dups
                       if d["key"] != issue.get("key")][:3],
        "assignee": who(experts[0]) if experts else {"key": "", "login": "", "reason": "no evidence"},
        "reviewers": [who(e) for e in experts[1:3]],
        "labels": issue.get("labels") or [],
        "priority": "medium",
        "draft_comment": "Thanks for the report! (auto-generated triage plan; please review)",
    }


def plan_markdown(plan: dict) -> str:
    lines = [plan.get("summary", "")]
    a = plan.get("assignee") or {}
    if a.get("key"):
        lines.append(f"\n**Assignee:** [[{a['key']}]] - {a.get('reason', '')}")
    if plan.get("reviewers"):
        lines.append("**Reviewers:** " + "; ".join(f"[[{r['key']}]] - {r.get('reason', '')}" for r in plan["reviewers"]))
    if plan.get("related_files"):
        lines.append("**Related files:** " + ", ".join(f"[[{f['key']}]]" for f in plan["related_files"][:6]))
    if plan.get("blast_radius"):
        lines.append(f"**Blast radius:** {len(plan['blast_radius'])} importing files, e.g. "
                     + ", ".join(f"[[{b['key']}]]" for b in plan["blast_radius"][:4]))
    if plan.get("duplicates"):
        lines.append("**Possible duplicates:** " + ", ".join(
            f"[[{d['key']}]] ({d.get('similarity', 0):.2f})" for d in plan["duplicates"][:4]))
    if plan.get("labels"):
        lines.append(f"**Labels:** {', '.join(plan['labels'])} · **Priority:** {plan.get('priority')}")
    return "\n".join(lines).strip()


def run(llm: LLM, graph, message: str, *, context: dict, recalled: dict | None = None,
        agent_run: AgentRun | None = None, http: httpx.Client | None = None) -> Iterator[dict]:
    """Yields events; sets agent_run.final_text (markdown) and agent_run.plan."""
    from .navigator import memory_block

    run_ = agent_run or AgentRun(agent="triage")
    url, text = parse_input(message)
    issue: dict = {"key": None, "url": url, "title": "", "body": text, "labels": []}
    if url:
        try:
            issue = fetch_issue(url, http=http)
            yield {"type": "step", "agent": "triage", "tool": "fetch_github_issue", "input": {"url": url},
                   "summary": f"fetched {issue['repo']}#{issue['number']}: {issue['title'][:80]}"}
        except (ToolError, httpx.HTTPError) as e:
            yield {"type": "step", "agent": "triage", "tool": "fetch_github_issue", "input": {"url": url},
                   "summary": f"could not fetch issue ({e}); triaging from the message text"}
            issue = {"key": None, "url": url, "title": "", "body": text, "labels": []}
    captured: dict = {}

    def on_extra(name, args):
        captured["plan"] = args
        return "Plan recorded.", True

    issue_text = (f"Issue: {issue.get('title')}\nURL: {issue.get('url')}\nKey: {issue.get('key')}\n"
                  f"Existing labels: {issue.get('labels')}\n\n{issue.get('body') or text}")
    messages = [{"role": "user", "content": memory_block(recalled) + "Triage this issue.\n\n" + issue_text}]
    yield from run_agent(llm, agent="triage", system=SYSTEM, messages=messages, graph=graph, tool_names=TOOLS,
                         context=context, extra_tools=[SUBMIT_TOOL], on_extra=on_extra, run=run_)
    if "plan" not in captured and not run_.error:
        messages.append({"role": "user", "content": "Now call submit_triage_plan with your final plan."})
        yield from run_agent(llm, agent="triage", system=SYSTEM, messages=messages, graph=graph, tool_names=TOOLS,
                             context=context, extra_tools=[SUBMIT_TOOL], on_extra=on_extra, run=run_, max_iters=2)
    if "plan" in captured:
        plan = _normalize_plan(captured["plan"], issue)
    else:
        yield {"type": "step", "agent": "triage", "tool": "fallback_plan", "input": {},
               "summary": "model did not submit a plan; built one deterministically from graph tools"}
        plan = _normalize_plan(fallback_plan(graph, issue), issue)
        run_.error = None
    run_.plan = plan  # type: ignore[attr-defined]
    run_.final_text = plan_markdown(plan)
    yield {"type": "triage", "plan": plan}
