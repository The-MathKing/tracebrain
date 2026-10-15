from __future__ import annotations

from .common import Tracer, ToolError, from_fnode, looks_like_key
from .search import find_related_work, search_entities


def trace_decision(graph, query: str, limit: int = 4) -> dict:
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
        # rerank a wider candidate pool (full-sentence "why" questions score unevenly in full-text search)
        sr = search_entities(graph, query, labels=["Decision"], limit=max(limit * 3, 10))
        t.cypher += sr["trace"]["cypher"]
        status = {n["id"]: n["props"].get("status") for n in sr["trace"]["nodes"]}
        # superseded decisions are often exactly the historical "why"; demote them only slightly
        boost = {"accepted": 1.1, "proposed": 1.0, "superseded": 0.95, "rejected": 0.9}
        ranked = sorted(sr["data"]["results"], key=lambda r: -(r["score"] * boost.get(status.get(r["key"]), 1.0)))
        dkeys = [r["key"] for r in ranked][:limit]
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
    # Decisions reached only through a SUPERSEDES chain: give their summary/status/source/people too, so the
    # agent never has to guess (or wrongly claim) where an older decision came from.
    seen = {d["key"] for d in decisions}
    chain = [k for d in decisions for k in d["supersedes"] + d["superseded_by"] if k not in seen]
    linked = []
    # siblings: other decisions replaced by the same newer decision (one level)
    for ck in list(dict.fromkeys(chain)):
        for (sib,) in t.q("MATCH (:Decision {key: $k})-[:SUPERSEDES]->(o:Decision) RETURN o.key", {"k": ck}):
            if sib not in seen and sib not in chain:
                chain.append(sib)
                t.add_edge(ck, sib, "SUPERSEDES")
    for ck in list(dict.fromkeys(chain))[:6]:
        rows = t.q("MATCH (d:Decision {key: $k}) OPTIONAL MATCH (d)-[:DECIDED_IN]->(s) "
                   "OPTIONAL MATCH (p:Person)-[m:MADE]->(d) RETURN d, collect(DISTINCT s.key), "
                   "collect(DISTINCT [p.key, m.role])", {"k": ck})
        if not rows:
            continue
        d, srcs, ppl = rows[0]
        t.add_fnode(d)
        for sk in srcs:
            t.add_edge(ck, sk, "DECIDED_IN")
        for pk, _ in ppl:
            if pk:
                t.add_edge(pk, ck, "MADE")
        linked.append({"key": ck, "summary": d.properties.get("summary"), "status": d.properties.get("status"),
                       "decided_at": d.properties.get("decided_at"), "sources": [x for x in srcs if x],
                       "people": [{"key": pk, "role": r} for pk, r in ppl if pk]})
    data = {"query": query, "decisions": decisions}
    if linked:
        data["linked_decisions"] = linked
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
