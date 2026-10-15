"""Tool registry shared by the agents (Anthropic tool definitions) and the MCP server."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import memory
from .algos import centrality
from .common import ORG_LABELS, ToolError
from .cypher import run_cypher
from .decisions import trace_decision
from .experts import find_experts
from .impact import blast_radius
from .neighbors import get_neighbors, shortest_path
from .search import find_related_work, search_entities

_KEY = {"type": "string", "description": "A node key, e.g. file:FalkorDB/falkordb-py:falkordb/graph.py, person:<login>, issue:<owner>/<repo>#12"}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., dict]
    strict: bool = True
    # Args injected from the caller's context (e.g. user_id) and hidden from the LLM schema.
    context_args: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    def anthropic_def(self) -> dict:
        d = {"name": self.name, "description": self.description, "input_schema": self.input_schema}
        if self.strict:
            d["strict"] = True
        return d

    def mcp_schema(self) -> dict:
        """MCP schema: same as the LLM one plus context args as explicit parameters."""
        s = json.loads(json.dumps(self.input_schema))
        for a in self.context_args:
            s["properties"][a] = {"type": "string", "description": f"{a} (memory is isolated per user)"}
        return s


def _recall(graph, user_id: str, query: str = "", limit: int = 8):
    return memory.recall(graph, user_id, query, limit=limit)


def _remember(graph, user_id: str, text: str, kind: str = "semantic", about_keys: list[str] | None = None):
    return memory.remember(graph, user_id, text, kind, about_keys)


TOOLS: list[ToolSpec] = [
    ToolSpec(
        "search_entities",
        "Find org entities (people, repos, modules, files, issues, PRs, labels, topics, decisions) by name, "
        "path, title or key. Call this first to turn names in the question into node keys.",
        _obj({"query": {"type": "string", "description": "Free text or an exact key"},
              "labels": {"type": "array", "items": {"type": "string", "enum": ORG_LABELS},
                         "description": "Restrict to these labels"},
              "limit": {"type": "integer", "description": "Max results (default 10)"}}, ["query"]),
        search_entities),
    ToolSpec(
        "get_neighbors",
        "Expand a node: everything within `depth` hops (default 1), optionally only via certain relationship "
        "types (e.g. AUTHORED, REVIEWED, MODIFIES, IMPORTS, OWNS, CLOSES, REFERENCES, DECIDED_IN, ABOUT).",
        _obj({"key": _KEY, "rel_types": {"type": "array", "items": {"type": "string"}},
              "depth": {"type": "integer", "description": "1-3"},
              "limit": {"type": "integer", "description": "Max paths (default 50)"}}, ["key"]),
        get_neighbors),
    ToolSpec(
        "find_experts",
        "Who knows about X? Ranks people by authored/reviewed PRs touching the relevant files, CODEOWNERS "
        "ownership, issue activity and decisions, with recency decay. Returns a score breakdown and evidence "
        "paths. Use for 'who knows', 'who should review', 'who owns'.",
        _obj({"target": {"type": "string", "description": "A node key (file/module/issue/PR/topic) or free text"},
              "limit": {"type": "integer", "description": "Max people (default 5)"}}, ["target"]),
        find_experts),
    ToolSpec(
        "trace_decision",
        "Why was X decided? Finds Decision nodes with their source issue/PR, who proposed/approved them, "
        "the SUPERSEDES chain and affected modules. Falls back to the closest discussions if none match.",
        _obj({"query": {"type": "string", "description": "What the decision is about, or a decision key"}}, ["query"]),
        trace_decision),
    ToolSpec(
        "blast_radius",
        "What breaks if a file/module/PR changes? Reverse import traversal (who imports it, transitively), "
        "affected modules, code owners, recent modifiers and related decisions.",
        _obj({"key": _KEY, "depth": {"type": "integer", "description": "Import hops, 1-6 (default 3)"}}, ["key"]),
        blast_radius),
    ToolSpec(
        "find_related_work",
        "Find similar issues and PRs (duplicate candidates, prior fixes) for a piece of text or an issue/PR key.",
        _obj({"text": {"type": "string", "description": "Issue text or an issue/PR key"},
              "limit": {"type": "integer", "description": "Max candidates (default 8)"}}, ["text"]),
        find_related_work),
    ToolSpec(
        "shortest_path",
        "How are two entities connected? Shortest path (up to 6 hops, any direction) between two keys.",
        _obj({"a": _KEY, "b": _KEY}, ["a", "b"]),
        shortest_path),
    ToolSpec(
        "centrality",
        "Most central nodes of a label: pagerank (FalkorDB algo.pageRank), betweenness or degree. "
        "E.g. most depended-upon files (File + pagerank) or most active people (Person).",
        _obj({"label": {"type": "string", "enum": ORG_LABELS},
              "algo": {"type": "string", "enum": ["pagerank", "betweenness", "degree"]},
              "limit": {"type": "integer"}}, []),
        centrality),
    ToolSpec(
        "run_cypher",
        "Run a read-only openCypher query against the org graph when no other tool fits. Labels: Person, Repo, "
        "Module, File, Issue, PullRequest, Label, Topic, Decision; every node has `key` and `title`. Use $params.",
        {"type": "object", "properties": {"query": {"type": "string"},
                                          "params": {"type": "object", "description": "Query parameters"}},
         "required": ["query"]},
        run_cypher, strict=False),
    ToolSpec(
        "recall",
        "Recall what you know about the current user (preferences, what they work on, procedures) and recent "
        "conversation episodes relevant to a query.",
        _obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
        _recall, context_args=("user_id",)),
    ToolSpec(
        "remember",
        "Store a durable fact about the current user (kind: semantic | preference | procedural), optionally "
        "about some node keys.",
        _obj({"text": {"type": "string"}, "kind": {"type": "string", "enum": ["semantic", "preference", "procedural"]},
              "about_keys": {"type": "array", "items": {"type": "string"}}}, ["text", "kind"]),
        _remember, context_args=("user_id",)),
]

BY_NAME = {t.name: t for t in TOOLS}


def anthropic_tools(names: list[str] | None = None) -> list[dict]:
    """Tool definitions in a deterministic order (stable for prompt caching)."""
    specs = [t for t in TOOLS if names is None or t.name in names]
    return [t.anthropic_def() for t in specs]


def dispatch(graph, name: str, args: dict | None, context: dict | None = None) -> dict:
    """Run a tool. Raises ToolError for bad input / unknown tool."""
    spec = BY_NAME.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name}")
    args = dict(args or {})
    props = spec.input_schema.get("properties", {})
    unknown = [k for k in args if k not in props and k not in spec.context_args]
    for k in unknown:
        args.pop(k)
    for a in spec.context_args:
        if context and context.get(a) is not None:
            args[a] = context[a]  # context wins: the LLM can't pick another user's memory
        elif a not in args:
            raise ToolError(f"{name} needs {a}")
    missing = [k for k in spec.input_schema.get("required", []) if args.get(k) in (None, "")]
    if missing:
        raise ToolError(f"missing required argument(s): {missing}")
    return spec.fn(graph, **args)


def summarize(name: str, args: dict, result: dict | None = None, error: str | None = None) -> str:
    """One-line human summary of a tool step for the UI timeline."""
    a = args or {}
    main = a.get("query") or a.get("target") or a.get("key") or a.get("text") or (
        f"{a.get('a')} -> {a.get('b')}" if "a" in a else a.get("label") or "")
    main = str(main)[:80]
    if error:
        return f"{name}({main}) failed: {error}"
    d = (result or {}).get("data") or {}
    n = len((result or {}).get("trace", {}).get("nodes", []))
    detail = ""
    for k in ("results", "experts", "candidates", "decisions", "neighbors", "ranking", "affected_files", "rows", "facts"):
        if isinstance(d.get(k), list):
            detail = f"{len(d[k])} {k.replace('_', ' ')}"
            break
    if name == "shortest_path":
        detail = f"path length {d.get('length')}" if d.get("found") else "no path"
    return f"{name}({main}): {detail or 'ok'}, {n} nodes"
