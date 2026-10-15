from __future__ import annotations

from .common import Tracer, ToolError, from_fnode, looks_like_key
from .search import find_related_work, search_entities


def trace_decision(graph, query: str, limit: int = 3) -> dict:
    """Why was X decided? Returns matching Decisions with their source issue/PR, the people who made
    them (role), the SUPERSEDES chain, affected modules/files and topics. If no Decision matches,
    falls back to the closest discussions (issues/PRs) so the agent can still cite sources."""
    t = Tracer(graph)
    query = (query or "").strip()
    if not query:
        raise ToolError("query is required")
    limit = max(1, min(int(limit or 3), 10))
    if looks_like_key(query) and query.startswith("decision:"):
        dkeys = [query]
    else:
        sr = search_entities(graph, query, labels=["Decision"], limit=limit)
        t.cypher += sr["trace"]["cypher"]
        status = {n["id"]: n["props"].get("status") for n in sr["trace"]["nodes"]}
        boost = {"accepted": 0.2, "proposed": 0.0, "superseded": -0.15, "rejected": -0.15}
        ranked = sorted(sr["data"]["results"], key=lambda r: -(r["score"] + boost.get(status.get(r["key"]), 0)))
        dkeys = [r["key"] for r in ranked]
    decisions = []
    for dk in dkeys:
        rows = t.q("MATCH (d:Decision {key: $k}) RETURN d", {"k": dk})
        if not rows:
            continue
        d = from_fnode(rows[0][0])
        t.add_node(d)
        item = {"key": dk, "summary": d["props"].get("summary"), "rationale": d["props"].get("rationale"),
                "status": d["props"].get("status"), "decided_at": d["props"].get("decided_at"),
                "quote": d["props"].get("quote"), "source_url": d["url"],
                "sources": [], "people": [], "affects": [], "topics": [], "supersedes": [], "superseded_by": []}
        for (s,) in t.q("MATCH (:Decision {key: $k})-[:DECIDED_IN]->(s) RETURN s", {"k": dk}):
            sk = t.add_fnode(s)
            t.add_edge(dk, sk, "DECIDED_IN")
            item["sources"].append({"key": sk, "title": t.nodes[sk]["title"], "url": t.nodes[sk]["url"]})
        for p, role in t.q("MATCH (p:Person)-[m:MADE]->(:Decision {key: $k}) RETURN p, m.role", {"k": dk}):
            pk = t.add_fnode(p)
            t.add_edge(pk, dk, "MADE")
            item["people"].append({"key": pk, "login": p.properties.get("login"), "role": role})
        for x, rel in t.q("MATCH (:Decision {key: $k})-[r:AFFECTS|ABOUT]->(x) RETURN x, type(r)", {"k": dk}):
            xk = t.add_fnode(x)
            t.add_edge(dk, xk, rel)
            (item["affects"] if rel == "AFFECTS" else item["topics"]).append(xk)
        for p in [r[0] for r in t.q("MATCH p=(:Decision {key: $k})-[:SUPERSEDES*1..5]->(:Decision) RETURN p", {"k": dk})]:
            keys = t.add_path(p)
            item["supersedes"] = max(item["supersedes"], keys[1:], key=len)
        for p in [r[0] for r in t.q("MATCH p=(:Decision)-[:SUPERSEDES*1..5]->(:Decision {key: $k}) RETURN p", {"k": dk})]:
            keys = t.add_path(p)
            item["superseded_by"] = max(item["superseded_by"], keys[:-1][::-1], key=len)
        decisions.append(item)
    data = {"query": query, "decisions": decisions}
    if not decisions:
        rw = find_related_work(graph, query, limit=5)
        t.cypher += rw["trace"]["cypher"]
        for n in rw["trace"]["nodes"]:
            t.add_node(n)
        for e in rw["trace"]["edges"]:
            t.add_edge(e["source"], e["target"], e["type"])
        data["fallback"] = "No extracted Decision matched; closest discussions are listed instead."
        data["discussions"] = rw["data"]["candidates"]
    return t.result(data)
