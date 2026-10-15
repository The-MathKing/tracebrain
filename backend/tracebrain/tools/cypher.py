from __future__ import annotations

import re

from .common import Tracer, ToolError, from_fnode

_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b|\bCALL\s+(db|dbms|algo\.\w*write)\b",
    re.IGNORECASE,
)
_STRING_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
MAX_ROWS = 200


def _jsonable(v, t: Tracer):
    if hasattr(v, "labels") and hasattr(v, "properties"):
        d = from_fnode(v)
        if d:
            t.add_node(d)
            return {"node": d["id"], "label": d["label"], "title": d["title"]}
        return {"labels": v.labels, "properties": v.properties}
    if hasattr(v, "relation") and hasattr(v, "src_node"):
        return {"type": v.relation, "properties": v.properties}
    if hasattr(v, "nodes") and hasattr(v, "edges") and callable(v.nodes):
        return {"path": t.add_path(v)}
    if isinstance(v, list):
        return [_jsonable(x, t) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x, t) for k, x in v.items()}
    return v


def run_cypher(graph, query: str, params: dict | None = None) -> dict:
    """Run a read-only Cypher query. Writes (CREATE/MERGE/SET/DELETE/REMOVE/DROP) and CALL db.* are
    rejected, and the query is executed with GRAPH.RO_QUERY so the server enforces it too."""
    t = Tracer(graph)
    query = (query or "").strip()
    if not query:
        raise ToolError("query is required")
    stripped = _STRING_RE.sub("''", query)  # keywords inside string literals are fine
    m = _WRITE_RE.search(stripped)
    if m:
        raise ToolError(f"run_cypher is read-only; rejected keyword {m.group(0)!r}")
    if params is not None and not isinstance(params, dict):
        raise ToolError("params must be an object")
    try:
        res = graph.ro_query(query, params or {}, timeout=10000)
    except Exception as e:
        raise ToolError(f"cypher error: {e}") from e
    t.cypher.append(" ".join(query.split()))
    header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    rows = [[_jsonable(v, t) for v in row] for row in res.result_set[:MAX_ROWS]]
    return t.result({"columns": header, "rows": rows, "row_count": len(res.result_set),
                     "truncated": len(res.result_set) > MAX_ROWS})
