"""TraceBrain MCP server (stdio). Exposes the same graph tools as the agents.

Run:  TRACEBRAIN_TENANT=falkordb TRACEBRAIN_USER=me tracebrain-mcp     (or python -m tracebrain.mcp_server)
Claude Code:  claude mcp add tracebrain -- /path/.venv/bin/python -m tracebrain.mcp_server
"""

from __future__ import annotations

import os
from typing import Literal

from mcp.server.mcpserver import MCPServer

from .db import get_graph
from .tools import registry

TENANT = os.getenv("TRACEBRAIN_TENANT") or None
DEFAULT_USER = os.getenv("TRACEBRAIN_USER", "mcp")

server = MCPServer(
    "tracebrain",
    instructions="TraceBrain: an engineering org's company brain in FalkorDB (people, repos, files, imports, issues, "
                 "PRs, reviews, CODEOWNERS, decisions). Start with search_entities to resolve names to keys. Every "
                 "result has `data` and a `trace` (Cypher run + nodes/edges) so answers are explainable.",
)

Label = Literal["Person", "Repo", "Module", "File", "Issue", "PullRequest", "Label", "Topic", "Decision"]


def _call(name: str, args: dict, user_id: str | None = None) -> dict:
    args = {k: v for k, v in args.items() if v is not None}
    ctx = {"user_id": user_id or DEFAULT_USER}
    return registry.dispatch(get_graph(TENANT), name, args, ctx)


def _desc(name: str) -> str:
    return registry.BY_NAME[name].description


@server.tool(description=_desc("search_entities"))
def search_entities(query: str, labels: list[Label] | None = None, limit: int = 10) -> dict:
    return _call("search_entities", {"query": query, "labels": labels, "limit": limit})


@server.tool(description=_desc("get_neighbors"))
def get_neighbors(key: str, rel_types: list[str] | None = None, depth: int = 1, limit: int = 50) -> dict:
    return _call("get_neighbors", {"key": key, "rel_types": rel_types, "depth": depth, "limit": limit})


@server.tool(description=_desc("find_experts"))
def find_experts(target: str, limit: int = 5) -> dict:
    return _call("find_experts", {"target": target, "limit": limit})


@server.tool(description=_desc("trace_decision"))
def trace_decision(query: str) -> dict:
    return _call("trace_decision", {"query": query})


@server.tool(description=_desc("blast_radius"))
def blast_radius(key: str, depth: int = 3) -> dict:
    return _call("blast_radius", {"key": key, "depth": depth})


@server.tool(description=_desc("find_related_work"))
def find_related_work(text: str, limit: int = 8) -> dict:
    return _call("find_related_work", {"text": text, "limit": limit})


@server.tool(description=_desc("shortest_path"))
def shortest_path(a: str, b: str) -> dict:
    return _call("shortest_path", {"a": a, "b": b})


@server.tool(description=_desc("centrality"))
def centrality(label: Label = "Person", algo: Literal["pagerank", "betweenness", "degree"] = "pagerank",
               limit: int = 10) -> dict:
    return _call("centrality", {"label": label, "algo": algo, "limit": limit})


@server.tool(description=_desc("run_cypher"))
def run_cypher(query: str, params: dict | None = None) -> dict:
    return _call("run_cypher", {"query": query, "params": params})


@server.tool(description=_desc("recall") + " user_id defaults to $TRACEBRAIN_USER.")
def recall(query: str, user_id: str | None = None, limit: int = 8) -> dict:
    return _call("recall", {"query": query, "limit": limit}, user_id)


@server.tool(description=_desc("remember") + " user_id defaults to $TRACEBRAIN_USER.")
def remember(text: str, kind: Literal["semantic", "preference", "procedural"] = "semantic",
             about_keys: list[str] | None = None, user_id: str | None = None) -> dict:
    return _call("remember", {"text": text, "kind": kind, "about_keys": about_keys}, user_id)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
