from __future__ import annotations

from .common import ORG_LABELS, Tracer, ToolError, from_fnode

# For PageRank we project the subgraph where rank is meaningful for the label.
_PR_REL = {"File": "IMPORTS", "Issue": "REFERENCES", "PullRequest": "REFERENCES"}


def _degree(t: Tracer, label: str, limit: int) -> list[tuple]:
    if label == "Person":
        # Activity degree: authored + reviewed + commented + owns.
        return t.q(
            "MATCH (n:Person)-[r:AUTHORED|REVIEWED|COMMENTED_ON|OWNS|MADE]->() "
            "WHERE NOT n.login ENDS WITH '[bot]' AND coalesce(n.bot, false) <> true "
            "RETURN n, count(r) AS score ORDER BY score DESC LIMIT $lim", {"lim": limit})
    return t.q(f"MATCH (n:{label})-[r]-() RETURN n, count(r) AS score ORDER BY score DESC LIMIT $lim", {"lim": limit})


def centrality(graph, label: str = "Person", algo: str = "pagerank", limit: int = 10) -> dict:
    """Most central nodes of a label. algo: pagerank (FalkorDB algo.pageRank on a label-appropriate
    projection: File->IMPORTS, Issue/PR->REFERENCES), betweenness (algo.betweenness), or degree.
    Falls back to degree centrality when the algorithm is unavailable or uninformative."""
    t = Tracer(graph)
    if label not in ORG_LABELS:
        raise ToolError(f"label must be one of {ORG_LABELS}")
    algo = (algo or "pagerank").lower()
    limit = max(1, min(int(limit or 10), 50))
    rows, method = None, algo
    if algo == "pagerank":
        if label in _PR_REL:
            rows = t.try_q(
                "CALL algo.pageRank($label, $rel) YIELD node, score RETURN node, score ORDER BY score DESC LIMIT $lim",
                {"label": label, "rel": _PR_REL[label], "lim": limit})
            method = f"pagerank({label}, {_PR_REL[label]})"
        else:
            # People/modules are mostly edge sources; rank on the whole graph, then filter by label.
            rows = t.try_q(
                "CALL algo.pageRank(NULL, NULL) YIELD node, score WITH node, score WHERE $label IN labels(node) "
                "RETURN node, score ORDER BY score DESC LIMIT $lim", {"label": label, "lim": limit})
            method = "pagerank(full graph)"
    elif algo == "betweenness":
        if label in _PR_REL:
            rows = t.try_q(
                "CALL algo.betweenness({nodeLabels: [$label], relationshipTypes: [$rel]}) YIELD node, score "
                "RETURN node, score ORDER BY score DESC LIMIT $lim", {"label": label, "rel": _PR_REL[label], "lim": limit},
                timeout_ms=20000)
            method = f"betweenness({label}, {_PR_REL[label]})"
        else:
            rows = t.try_q(
                "CALL algo.betweenness({}) YIELD node, score WITH node, score WHERE $label IN labels(node) "
                "RETURN node, score ORDER BY score DESC LIMIT $lim", {"label": label, "lim": limit}, timeout_ms=20000)
            method = "betweenness(full graph)"
    elif algo != "degree":
        raise ToolError("algo must be pagerank, betweenness or degree")
    scores = [float(r[1] or 0) for r in rows or []]
    if not rows or len(set(round(s, 9) for s in scores)) <= 1:
        rows = _degree(t, label, limit)
        method = "degree" if algo == "degree" else f"degree (fallback from {algo})"
    out = []
    for n, s in rows:
        d = from_fnode(n)
        if d:
            t.add_node(d)
            out.append({"key": d["id"], "title": d["title"], "score": round(float(s or 0), 6)})
    return t.result({"label": label, "method": method, "ranking": out})
