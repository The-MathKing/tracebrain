"""Shared helpers for graph tools: Node/Edge conversion and trace recording.

Every tool returns ``{"data": ..., "trace": {"cypher": [...], "nodes": [Node], "edges": [Edge]}}``
with ``Node = {id, label, title, url, props}`` and ``Edge = {source, target, type}`` (keys).
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable

DEFAULT_TIMEOUT_MS = 8000
MAX_PROP_CHARS = 400

# Labels that carry a full-text index (per SPEC) and the fields indexed.
FULLTEXT_LABELS: dict[str, tuple[str, ...]] = {
    "Issue": ("title", "body"),
    "PullRequest": ("title", "body"),
    "Decision": ("summary", "rationale"),
    "File": ("path",),
    "Topic": ("name",),
    "Person": ("login", "name"),
    "Fact": ("text",),
}
# Searchable org labels (memory/coordination labels are excluded from general search).
ORG_LABELS = ["Person", "Repo", "Module", "File", "Issue", "PullRequest", "Label", "Topic", "Decision"]
# Relationship types of the org graph (memory/coordination edges such as CITED, TOUCHED, KNOWS are excluded so
# exploration and path tools never route through Episodes, Facts or Tasks).
ORG_RELS = ["AUTHORED", "REVIEWED", "COMMENTED_ON", "MODIFIES", "IN_MODULE", "IN_REPO", "IMPORTS", "CLOSES",
            "REFERENCES", "HAS_LABEL", "OWNS", "DECIDED_IN", "MADE", "AFFECTS", "ABOUT", "SUPERSEDES"]
# Cypher predicate keeping a path inside the org graph (Fact-[:ABOUT]-> and Fact-[:SUPERSEDES]-> share names).
ORG_PATH_WHERE = "all(z IN nodes(p) WHERE " + " OR ".join(f"z:{l}" for l in ORG_LABELS) + ")"

STOPWORDS = set(
    """a an and are as at be by for from has have how i in is it its of on or that the this to was
    were what when where which who whom why will with does do did can could should would about into
    me my we our you your they them their there these those any all some not no yes if then than so
    know knows knew changed change changes decide decided instead own ship ships rather""".split()
)


class ToolError(Exception):
    """A tool-level error that should be shown to the model / caller as a message."""


def _title_for(label: str, props: dict) -> str:
    for field in ("title",):
        if props.get(field):
            return str(props[field])
    if label == "Person":
        return str(props.get("name") or props.get("login") or props.get("key"))
    if label in ("File", "Module"):
        return str(props.get("path") or props.get("key"))
    if label == "Decision":
        return str(props.get("summary") or props.get("key"))
    if label == "Fact":
        return str(props.get("text") or props.get("key"))
    if label == "Episode":
        return str(props.get("question") or props.get("key"))
    if label in ("Issue", "PullRequest") and props.get("number") is not None:
        return f"#{props['number']}"
    if label in ("Repo", "Label", "Topic") and props.get("name"):
        return str(props["name"])
    if label == "Task":
        return f"{props.get('kind', 'task')} ({props.get('status', '?')})"
    return str(props.get("key"))


def _trim(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_PROP_CHARS:
        return v[:MAX_PROP_CHARS] + "…"
    if isinstance(v, list) and len(v) > 30:
        return v[:30]
    return v


def node_dict(label: str, props: dict) -> dict:
    props = dict(props or {})
    key = props.get("key")
    return {
        "id": key,
        "label": label,
        "title": _title_for(label, props),
        "url": props.get("url") or props.get("source_url"),
        "props": {k: _trim(v) for k, v in props.items() if k not in ("key", "title", "url", "embedding")},
    }


def from_fnode(n) -> dict | None:
    """Convert a falkordb.node.Node to a SPEC Node (None if it has no key)."""
    props = getattr(n, "properties", None) or {}
    if not props.get("key"):
        return None
    labels = getattr(n, "labels", None) or []
    return node_dict(labels[0] if labels else "Node", props)


def edge_dict(source: str, target: str, type_: str) -> dict:
    return {"source": source, "target": target, "type": type_}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(v: Any) -> float | None:
    """ISO string or epoch int -> epoch seconds."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


HALF_LIFE_DAYS = 180.0


def recency_weight(ts: Any, now: float | None = None, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """Exponential decay with a half-life; unknown dates get a neutral 0.5."""
    t = parse_ts(ts)
    if t is None:
        return 0.5
    age_days = max(0.0, ((now or time.time()) - t) / 86400.0)
    return math.pow(0.5, age_days / half_life_days)


def terms(text: str, max_terms: int = 12) -> list[str]:
    out: list[str] = []
    for t in re.findall(r"[A-Za-z0-9_]+", text or ""):
        tl = t.lower()
        if len(tl) < 2 or tl in STOPWORDS or tl in out:
            continue
        out.append(tl)
        if len(out) >= max_terms:
            break
    return out


_SUFFIXES = ("encies", "ency", "ings", "ing", "ies", "ed", "es", "s", "y")


def stem(t: str) -> str:
    """Crude suffix strip so prefix search matches word variants ("depending"/"dependency" -> "depend")."""
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


def fulltext_query(text: str) -> str | None:
    """Build a RediSearch query string (OR of terms, prefix match for longer terms)."""
    ts = terms(text)
    if not ts:
        return None
    parts = [f"{t}*" if len(t) >= 4 else t for t in ts]
    return "|".join(parts)


def fulltext_exact(text: str) -> str | None:
    """OR of exact terms. Prefix expansions (e.g. "embedd*" -> embedding) can bury precise matches in long
    sentences, so search merges this pool with the prefix one."""
    ts = terms(text)
    return "|".join(ts) if ts else None


def looks_like_key(s: str) -> bool:
    return bool(re.match(r"^(person|repo|module|file|issue|pr|label|topic|decision|user|session|episode|fact|agent|task|sync):\S", s or ""))


class Tracer:
    """Runs parameterized Cypher, records it, and accumulates trace nodes/edges."""

    def __init__(self, graph, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.graph = graph
        self.timeout_ms = timeout_ms
        self.cypher: list[str] = []
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}

    def q(self, cypher: str, params: dict | None = None, *, ro: bool = True, timeout_ms: int | None = None) -> list[list]:
        cy = " ".join(cypher.split())
        self.cypher.append(cy)
        fn = self.graph.ro_query if ro else self.graph.query
        res = fn(cy, params or {}, timeout=timeout_ms or self.timeout_ms)
        return res.result_set

    def try_q(self, cypher: str, params: dict | None = None, **kw) -> list[list] | None:
        try:
            return self.q(cypher, params, **kw)
        except Exception:  # missing index / unsupported procedure
            return None

    def add_node(self, node: dict | None) -> str | None:
        if not node or not node.get("id"):
            return None
        self.nodes.setdefault(node["id"], node)
        return node["id"]

    def add_fnode(self, n) -> str | None:
        return self.add_node(from_fnode(n))

    def add_edge(self, source: str | None, target: str | None, type_: str) -> None:
        if source and target:
            self.edges.setdefault((source, target, type_), edge_dict(source, target, type_))

    def add_path(self, path) -> list[str]:
        """Add a falkordb Path; returns node keys in order."""
        id2key: dict[int, str] = {}
        keys: list[str] = []
        for n in path.nodes():
            k = self.add_fnode(n)
            if k:
                id2key[n.id] = k
                keys.append(k)
        for e in path.edges():
            self.add_edge(id2key.get(e.src_node), id2key.get(e.dest_node), e.relation)
        return keys

    def result(self, data: Any) -> dict:
        return {
            "data": data,
            "trace": {"cypher": self.cypher, "nodes": list(self.nodes.values()), "edges": list(self.edges.values())},
        }


def get_node(t: Tracer, key: str) -> dict | None:
    rows = t.q("MATCH (n {key: $key}) RETURN n LIMIT 1", {"key": key})
    if not rows:
        return None
    node = from_fnode(rows[0][0])
    t.add_node(node)
    return node


def nodes_by_keys(graph, keys: Iterable[str]) -> list[dict]:
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return []
    res = graph.ro_query("UNWIND $keys AS k MATCH (n {key: k}) RETURN n", {"keys": keys}, timeout=DEFAULT_TIMEOUT_MS)
    by_key = {}
    for (n,) in res.result_set:
        d = from_fnode(n)
        if d:
            by_key[d["id"]] = d
    return [by_key[k] for k in keys if k in by_key]
