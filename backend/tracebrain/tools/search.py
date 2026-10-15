from __future__ import annotations

import re

from .common import (fulltext_exact, stem, 
    FULLTEXT_LABELS,
    ORG_LABELS,
    Tracer,
    ToolError,
    from_fnode,
    fulltext_query,
    looks_like_key,
    terms,
)

_TEXT_FIELDS = ("title", "name", "login", "path", "summary", "text")


def _haystack(node: dict) -> str:
    p = node["props"]
    parts = [node.get("title") or "", node.get("id") or ""] + [str(p.get(f) or "") for f in _TEXT_FIELDS]
    return " ".join(parts).lower()


_NON_CODE_LANGS = {"HTML", "JSON", "CSV"}
_BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|svg|ico|rdb|pdf|zip|gz|lock|csv|html?)$", re.IGNORECASE)


def _file_penalty(node: dict) -> float:
    """Generated data/assets (example HTML dumps, JSON fixtures, images) rank below source files."""
    if node["label"] != "File":
        return 1.0
    p = node["props"]
    if p.get("language") in _NON_CODE_LANGS or _BINARY_EXT.search(str(p.get("path") or "")):
        return 0.6
    return 1.0


def _coverage(node: dict, ts: list[str]) -> float:
    if not ts:
        return 0.0
    h = _haystack(node)
    return sum(1 for t in ts if stem(t) in h) / len(ts)


def _merge_hits(a: list, b: list) -> list:
    """Union of two hit lists, each score normalized to its own max; keeps the best per node."""
    out: dict[str, tuple[dict, float]] = {}
    for hits in (a, b):
        top = max((s for _, s in hits), default=0) or 1.0
        for d, s in hits:
            if d["id"] not in out or out[d["id"]][1] < s / top:
                out[d["id"]] = (d, s / top)
    return list(out.values())


def _ft_label(t: Tracer, label: str, ftq: str, limit: int) -> list[tuple[dict, float]] | None:
    rows = t.try_q(
        "CALL db.idx.fulltext.queryNodes($label, $q) YIELD node, score "
        "RETURN node, score ORDER BY score DESC LIMIT $lim",
        {"label": label, "q": ftq, "lim": limit},
    )
    if rows is None:
        return None
    out = []
    for n, s in rows:
        d = from_fnode(n)
        if d:
            out.append((d, float(s or 0)))
    return out


def _contains_label(t: Tracer, label: str, ts: list[str], limit: int) -> list[tuple[dict, float]]:
    # Label names come from a fixed allow-list, never from user input.
    if label not in ORG_LABELS + ["Fact", "Episode"]:
        return []
    rows = t.q(
        f"MATCH (n:{label}) "
        "WITH n, toLower(coalesce(n.title,'') + ' ' + coalesce(n.name,'') + ' ' + coalesce(n.path,'') + ' ' "
        "+ coalesce(n.login,'') + ' ' + coalesce(n.summary,'') + ' ' + n.key) AS h "
        "WITH n, size([x IN $terms WHERE h CONTAINS x]) AS hits WHERE hits > 0 "
        "RETURN n, hits ORDER BY hits DESC LIMIT $lim",
        {"terms": ts, "lim": limit},
    )
    out = []
    for n, s in rows:
        d = from_fnode(n)
        if d:
            out.append((d, float(s)))
    return out


def search_entities(graph, query: str, labels: list[str] | None = None, limit: int = 10) -> dict:
    """Find org entities by key, full-text index, or substring match. Ranked by a blend of
    the per-label full-text score (normalized) and query-term coverage of the node's title."""
    t = Tracer(graph)
    query = (query or "").strip()
    if not query:
        raise ToolError("query is required")
    limit = max(1, min(int(limit or 10), 50))
    labels = [l for l in (labels or ORG_LABELS) if l in ORG_LABELS + ["Fact", "Episode"]] or ORG_LABELS

    if looks_like_key(query):
        rows = t.q("MATCH (n {key: $key}) RETURN n LIMIT 1", {"key": query})
        if rows:
            d = from_fnode(rows[0][0])
            t.add_node(d)
            return t.result({"results": [{"key": d["id"], "label": d["label"], "title": d["title"], "score": 1.0}]})

    ts = terms(query)
    if not ts:
        ts = [query.lower()]
    ftq = fulltext_query(query)
    scored: dict[str, tuple[dict, float, dict]] = {}
    for label in labels:
        hits = None
        if label in FULLTEXT_LABELS and ftq:
            hits = _ft_label(t, label, ftq, limit * 2)
            exact = fulltext_exact(query)
            if hits is not None and exact and len(terms(query)) >= 4:  # long, sentence-like queries
                hits = _merge_hits(hits, _ft_label(t, label, exact, limit * 2) or [])
        if not hits:
            hits = _contains_label(t, label, ts, limit * 2)
        if not hits:
            continue
        top = max(s for _, s in hits) or 1.0
        for d, s in hits:
            cov = _coverage(d, ts)
            score = (0.4 * (s / top) + 0.6 * cov) * _file_penalty(d)
            # Prefer entities whose title actually mentions the terms.
            if d["id"] not in scored or scored[d["id"]][1] < score:
                scored[d["id"]] = (d, score, {"index": round(s / top, 3), "coverage": round(cov, 3)})
    ranked = sorted(scored.values(), key=lambda x: -x[1])[:limit]
    results = []
    for d, score, why in ranked:
        t.add_node(d)
        results.append({"key": d["id"], "label": d["label"], "title": d["title"], "url": d["url"],
                        "score": round(score, 3), "why": why})
    return t.result({"query": query, "results": results})


def _doc_terms(props: dict) -> set[str]:
    return set(terms(f"{props.get('title') or ''} {(props.get('body') or '')[:2000]}", max_terms=200))


def find_related_work(graph, text: str, limit: int = 8) -> dict:
    """Similar issues/PRs (duplicate candidates) for a piece of text or an Issue/PR key.
    similarity = 0.5 * normalized full-text score + 0.5 * Jaccard overlap of terms."""
    t = Tracer(graph)
    text = (text or "").strip()
    if not text:
        raise ToolError("text is required")
    limit = max(1, min(int(limit or 8), 30))
    exclude = None
    if looks_like_key(text):
        rows = t.q("MATCH (n {key: $key}) RETURN n.title, n.body LIMIT 1", {"key": text})
        if not rows:
            raise ToolError(f"no node with key {text}")
        exclude = text
        text = f"{rows[0][0] or ''} {(rows[0][1] or '')[:1500]}"
    qterms = set(terms(text, max_terms=40))
    ftq = fulltext_query(" ".join(list(dict.fromkeys(terms(text, max_terms=14)))))
    cands: dict[str, tuple[dict, float]] = {}
    for label in ("Issue", "PullRequest"):
        hits = _ft_label(t, label, ftq, limit * 3) if ftq else None
        if hits is None:
            hits = _contains_label(t, label, list(qterms)[:12], limit * 3)
        if not hits:
            continue
        top = max(s for _, s in hits) or 1.0
        for d, s in hits:
            if d["id"] != exclude:
                cands[d["id"]] = (d, s / top)
    out = []
    for key, (d, ft) in cands.items():
        dt = _doc_terms({"title": d["title"], "body": d["props"].get("body")})
        inter = qterms & dt
        jac = len(inter) / max(1, len(qterms | dt))
        sim = 0.5 * ft + 0.5 * min(1.0, jac * 3)  # Jaccard on long docs is small; scale it
        out.append((sim, d, sorted(inter)[:10], ft, jac))
    out.sort(key=lambda x: -x[0])
    out = out[:limit]
    keys = [d["id"] for _, d, *_ in out]
    resolved: dict[str, list[str]] = {}
    if keys:
        for pr, iss in t.q(
            "MATCH (pr:PullRequest)-[:CLOSES]->(i:Issue) WHERE i.key IN $keys OR pr.key IN $keys "
            "RETURN pr.key, i.key", {"keys": keys}
        ):
            resolved.setdefault(iss, []).append(pr)
            t.add_edge(pr, iss, "CLOSES")
    results = []
    for sim, d, shared, ft, jac in out:
        t.add_node(d)
        results.append({
            "key": d["id"], "label": d["label"], "title": d["title"], "url": d["url"],
            "state": d["props"].get("state"), "similarity": round(sim, 3),
            "score_breakdown": {"fulltext": round(ft, 3), "jaccard": round(jac, 3)},
            "shared_terms": shared, "resolved_by": resolved.get(d["id"], []),
        })
    return t.result({"candidates": results})
