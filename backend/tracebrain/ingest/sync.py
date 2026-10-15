"""Orchestrates one repo sync into a tenant graph (incremental via SyncState)."""

import asyncio
from datetime import datetime, timezone

from .. import config
from ..db import get_graph
from ..schema import ensure_schema
from .code import fetch_tree, ingest_code
from .extract import extract_decisions
from .github import GitHub, ingest_codeowners, ingest_issues
from .graphio import merge_nodes, sync_key


def graph_counts(graph) -> dict:
    nodes = {r[0]: r[1] for r in graph.query("MATCH (n) RETURN labels(n)[0], count(n)").result_set}
    edges = {r[0]: r[1] for r in graph.query("MATCH ()-[e]->() RETURN type(e), count(e)").result_set}
    return {"nodes": nodes, "edges": edges}


def last_synced(graph, repo: str) -> str | None:
    rows = graph.query("MATCH (s:SyncState {key: $k}) RETURN s.last_synced_at", {"k": sync_key(repo)}).result_set
    return rows[0][0] if rows else None


async def aingest_repo(repo: str, tenant: str | None = None, since: str | None = None, max_items: int = 300,
                       with_code: bool = True, with_decisions: bool = True, full: bool = False, log=print,
                       gh: GitHub | None = None, llm=None, graph=None) -> dict:
    graph = graph if graph is not None else get_graph(tenant)
    ensure_schema(graph)
    if since is None and not full:
        since = last_synced(graph, repo)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"sync {repo} -> {graph.name} ({'since ' + since if since else 'full'})")

    own = gh is None
    gh = gh or GitHub(log=log)
    try:
        counts = {"repo": repo, "since": since}
        counts["github"] = await ingest_issues(gh, graph, repo, since=since, max_items=max_items, log=log)
        if with_code:
            counts["code"], paths = await ingest_code(gh, graph, repo, log=log)
        else:
            _, paths = await fetch_tree(gh, repo)
        counts["owns"] = await ingest_codeowners(gh, graph, repo, paths)
        if with_decisions and (llm is not None or config.llm_configured()):
            counts["decisions"] = await extract_decisions(gh, graph, repo, llm=llm, log=log)
        elif with_decisions:
            log("no LLM key configured: skipping decision extraction")
        merge_nodes(graph, "SyncState", [{"key": sync_key(repo), "props": {
            "repo": repo, "title": f"sync {repo}", "last_synced_at": started,
            "last_ts": int(datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp())}}])
        counts["requests"], counts["rate_remaining"] = gh.requests, gh.remaining
    finally:
        if own:
            await gh.http.aclose()
    counts["graph"] = graph_counts(graph)
    log(f"done {repo}: {counts['github']}")
    return counts


def ingest_repo(repo: str, tenant: str | None = None, since: str | None = None, max_items: int = 300,
                with_code: bool = True, with_decisions: bool = True, log=print, **kw) -> dict:
    """Sync wrapper (call from a thread if you're already inside an event loop)."""
    return asyncio.run(aingest_repo(repo, tenant, since, max_items, with_code, with_decisions, log=log, **kw))
