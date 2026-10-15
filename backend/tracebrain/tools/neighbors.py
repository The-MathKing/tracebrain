from __future__ import annotations

import re

from .common import ORG_LABELS, ORG_PATH_WHERE, ORG_RELS, Tracer, ToolError, get_node

_REL_RE = re.compile(r"^[A-Z][A-Z_]{0,40}$")


def _rel_pattern(rel_types: list[str] | None) -> str:
    rels = [r.upper() for r in (rel_types or []) if r]
    bad = [r for r in rels if not _REL_RE.match(r)]
    if bad:
        raise ToolError(f"invalid relationship type(s): {bad}")
    return ":" + "|".join(rels or ORG_RELS)


def get_neighbors(graph, key: str, rel_types: list[str] | None = None, depth: int = 1, limit: int = 50) -> dict:
    """Nodes within `depth` hops of `key` (any direction), optionally restricted to rel types."""
    t = Tracer(graph)
    center = get_node(t, key)
    if not center:
        raise ToolError(f"no node with key {key}")
    depth = max(1, min(int(depth or 1), 3))
    limit = max(1, min(int(limit or 50), 300))
    rel = _rel_pattern(rel_types)
    # depth and rel types are validated above; values are passed as parameters.
    rows = t.q(
        f"MATCH p=(n {{key: $key}})-[{rel}*1..{depth}]-(m) WHERE m.key IS NOT NULL AND m <> n AND {ORG_PATH_WHERE} "
        "RETURN p LIMIT $lim",
        {"key": key, "lim": limit},
    )
    neighbors: dict[str, dict] = {}
    for (p,) in rows:
        keys = t.add_path(p)
        if len(keys) < 2:
            continue
        last = keys[-1]
        if last == key or (last in neighbors and neighbors[last]["distance"] <= len(keys) - 1):
            continue
        n = t.nodes[last]
        # First relationship of the path tells how it connects to the center.
        e = p.edges()[0] if p.edges() else None
        direction = None
        if e is not None:
            direction = "out" if e.src_node == p.first_node().id else "in"
        neighbors[last] = {"key": last, "label": n["label"], "title": n["title"], "distance": len(keys) - 1,
                           "via": e.relation if e is not None else None, "direction": direction}
    return t.result({"center": {"key": key, "label": center["label"], "title": center["title"]},
                     "neighbors": sorted(neighbors.values(), key=lambda x: (x["distance"], x["label"]))})


def shortest_path(graph, a: str, b: str, max_depth: int = 6) -> dict:
    """Shortest undirected path between two keys (up to 6 hops).

    FalkorDB's shortestPath() is directed-only, so we try both directed shortestPath()s first and
    then confirm minimality with bounded undirected iterative deepening (LIMIT 1 per depth)."""
    t = Tracer(graph)
    na, nb = get_node(t, a), get_node(t, b)
    if not na or not nb:
        raise ToolError(f"unknown key(s): {[k for k, n in ((a, na), (b, nb)) if not n]}")
    if a == b:
        return t.result({"found": True, "length": 0, "path": [a], "rels": []})
    max_depth = max(1, min(int(max_depth or 6), 6))
    org = set(ORG_LABELS)
    # Prefer informative paths: hops through the Repo/Label hubs ("both are in the same repo") only as a fallback.
    for rels in (PATH_RELS, ORG_RELS):
        best = _shortest(t, a, b, max_depth, _rel_pattern(rels), org)
        if best is not None:
            break
    if best is None:
        return t.result({"found": False, "length": None, "path": [], "rels": []})
    # Path may be oriented b->a for the reversed directed search; normalize to start at a.
    keys = t.add_path(best)
    id2key = {n.id: k for n, k in zip(best.nodes(), keys)}
    edges = [(id2key.get(e.src_node), e.relation, id2key.get(e.dest_node)) for e in best.edges()]
    rels = [e[1] for e in edges]
    if keys and keys[0] != a:
        keys, rels, edges = keys[::-1], rels[::-1], edges[::-1]
    # `steps` follow the path order; `fact` states each hop in the edge's real direction.
    steps = [{"from": keys[i], "rel": rels[i], "to": keys[i + 1],
              "fact": f"{edges[i][0]} -[{edges[i][1]}]-> {edges[i][2]}"} for i in range(len(rels))]
    return t.result({"found": True, "length": len(rels), "path": keys, "rels": rels, "steps": steps})


PATH_RELS = [r for r in ORG_RELS if r not in ("IN_REPO", "HAS_LABEL")]


def _shortest(t: Tracer, a: str, b: str, max_depth: int, rel: str, org: set):
    best = None
    for src, dst in ((a, b), (b, a)):
        rows = t.try_q(
            f"MATCH (x {{key: $src}}), (y {{key: $dst}}) WITH shortestPath((x)-[{rel}*..{max_depth}]->(y)) AS p "
            "WHERE p IS NOT NULL RETURN p", {"src": src, "dst": dst}, timeout_ms=4000,
        )
        if rows and rows[0][0] is not None:
            p = rows[0][0]
            if not all(set(n.labels) & org for n in p.nodes()):
                continue
            if best is None or p.edge_count() < best.edge_count():
                best = p
    upper = best.edge_count() - 1 if best is not None else max_depth
    for d in range(1, upper + 1):
        rows = t.try_q(
            f"MATCH p=(x {{key: $a}})-[{rel}*{d}]-(y {{key: $b}}) WHERE {ORG_PATH_WHERE} RETURN p LIMIT 1", {"a": a, "b": b}, timeout_ms=4000,
        )
        if rows:
            best = rows[0][0]
            break
    return best
