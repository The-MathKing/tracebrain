"""find_experts: explainable expertise scoring over graph evidence paths.

score(person) = sum over evidence of weight(type) * recency_decay(date)  (OWNS is not decayed)
Weights: OWNS 5, MODIFIED (authored a PR touching the files) 3, DECIDED 3, REVIEWED 2,
AUTHORED (issue/PR in scope) 2, COMMENTED 0.5 per comment (max 5). Half-life 180 days.
"""

from __future__ import annotations

import time

from .common import Tracer, ToolError, from_fnode, get_node, looks_like_key, recency_weight
from .search import search_entities

WEIGHTS = {"owns": 5.0, "modified": 3.0, "decided": 3.0, "reviewed": 2.0, "authored": 2.0, "commented": 0.5}
MAX_FILES = 400


def _scope_from_anchor(t: Tracer, node: dict, scope: dict) -> None:
    key, label = node["id"], node["label"]
    if label == "File":
        scope["files"].add(key)
        for (m,) in t.q("MATCH (:File {key: $k})-[:IN_MODULE]->(m:Module) RETURN m.key", {"k": key}):
            scope["modules"].add(m)
    elif label == "Module":
        scope["modules"].add(key)
        for (f,) in t.q("MATCH (f:File)-[:IN_MODULE]->(:Module {key: $k}) RETURN f.key LIMIT $lim",
                        {"k": key, "lim": MAX_FILES}):
            scope["files"].add(f)
    elif label == "Repo":
        for (m,) in t.q("MATCH (m:Module)-[:IN_REPO]->(:Repo {key: $k}) RETURN m.key", {"k": key}):
            scope["modules"].add(m)
        for (i,) in t.q("MATCH (i)-[:IN_REPO]->(:Repo {key: $k}) WHERE i:Issue OR i:PullRequest "
                        "RETURN i.key LIMIT 300", {"k": key}):
            scope["items"].add(i)
    elif label == "PullRequest":
        scope["items"].add(key)
        for (f,) in t.q("MATCH (:PullRequest {key: $k})-[:MODIFIES]->(f:File) RETURN f.key LIMIT $lim",
                        {"k": key, "lim": MAX_FILES}):
            scope["files"].add(f)
    elif label == "Issue":
        scope["items"].add(key)
        for pr, f in t.q("MATCH (pr:PullRequest)-[:CLOSES]->(:Issue {key: $k}) "
                         "OPTIONAL MATCH (pr)-[:MODIFIES]->(f:File) RETURN pr.key, f.key LIMIT $lim",
                         {"k": key, "lim": MAX_FILES}):
            scope["items"].add(pr)
            if f:
                scope["files"].add(f)
    elif label == "Topic":
        for (i,) in t.q("MATCH (i)-[:ABOUT]->(:Topic {key: $k}) WHERE i:Issue OR i:PullRequest RETURN i.key LIMIT 200",
                        {"k": key}):
            scope["items"].add(i)
        for (d,) in t.q("MATCH (d:Decision)-[:ABOUT]->(:Topic {key: $k}) RETURN d.key LIMIT 100", {"k": key}):
            scope["decisions"].add(d)
    elif label == "Decision":
        scope["decisions"].add(key)
        for x, lbl in t.q("MATCH (:Decision {key: $k})-[:AFFECTS|DECIDED_IN]->(x) RETURN x.key, labels(x)[0]",
                          {"k": key}):
            {"File": scope["files"], "Module": scope["modules"]}.get(lbl, scope["items"]).add(x)
    elif label == "Label":
        for (i,) in t.q("MATCH (i)-[:HAS_LABEL]->(:Label {key: $k}) RETURN i.key LIMIT 200", {"k": key}):
            scope["items"].add(i)


def find_experts(graph, target: str, limit: int = 5) -> dict:
    """Who knows `target` (a node key or free text)? Returns ranked people with a score breakdown
    per evidence type and the evidence paths behind it."""
    t = Tracer(graph)
    target = (target or "").strip()
    if not target:
        raise ToolError("target is required")
    limit = max(1, min(int(limit or 5), 20))

    anchors: list[dict] = []
    if looks_like_key(target):
        n = get_node(t, target)
        if not n:
            raise ToolError(f"no node with key {target}")
        anchors = [n]
    else:
        # Balance code anchors and discussion anchors so neither crowds the other out.
        for labels, k in ((["File", "Module"], 4), (["Issue", "PullRequest", "Topic", "Decision"], 4)):
            sr = search_entities(graph, target, labels=labels, limit=k)
            t.cypher += sr["trace"]["cypher"]
            top = {r["key"] for r in sr["data"]["results"] if r["score"] >= 0.3}
            anchors += [n for n in sr["trace"]["nodes"] if n["id"] in top]
    scope = {"files": set(), "modules": set(), "items": set(), "decisions": set()}
    for a in anchors:
        t.add_node(a)
        _scope_from_anchor(t, a, scope)
    # Modules of the files in scope count for CODEOWNERS ownership.
    files = list(scope["files"])[:MAX_FILES]
    if files:
        for (m,) in t.q("MATCH (f:File)-[:IN_MODULE]->(m:Module) WHERE f.key IN $files RETURN DISTINCT m.key",
                        {"files": files}):
            scope["modules"].add(m)
    modules, items, decisions = list(scope["modules"]), list(scope["items"]), list(scope["decisions"])

    now = time.time()
    people: dict[str, dict] = {}
    pnodes: dict[str, dict] = {}

    def add(pn, etype: str, via: str, at=None, extra: float = 1.0, path: list | None = None):
        pd = from_fnode(pn)
        if not pd:
            return
        login = pd["props"].get("login") or ""
        if pd["props"].get("bot") is True or login.endswith("[bot]") or login.endswith("-bot"):
            return
        pk = pd["id"]
        pnodes[pk] = pd
        decay = 1.0 if etype == "owns" else recency_weight(at, now)
        w = WEIGHTS[etype] * extra * decay
        e = people.setdefault(pk, {"breakdown": {k: 0.0 for k in WEIGHTS}, "evidence": []})
        e["breakdown"][etype] += w
        e["evidence"].append({"type": etype, "via": via, "at": at, "weight": round(w, 3), "path": path or []})

    if files:
        for pn, pr, at, fs in t.q(
            "MATCH (p:Person)-[:AUTHORED]->(pr:PullRequest)-[:MODIFIES]->(f:File) WHERE f.key IN $files "
            "RETURN p, pr.key, coalesce(pr.merged_at, pr.created_at), collect(f.key)[..3]", {"files": files}
        ):
            add(pn, "modified", pr, at, path=[pn.properties["key"], "AUTHORED", pr, "MODIFIES", fs[0]])
        for pn, pr, at, fs in t.q(
            "MATCH (p:Person)-[r:REVIEWED]->(pr:PullRequest)-[:MODIFIES]->(f:File) WHERE f.key IN $files "
            "RETURN p, pr.key, coalesce(r.at, pr.created_at), collect(f.key)[..3]", {"files": files}
        ):
            add(pn, "reviewed", pr, at, path=[pn.properties["key"], "REVIEWED", pr, "MODIFIES", fs[0]])
    if files or modules:
        for pn, x in t.q(
            "MATCH (p:Person)-[:OWNS]->(x) WHERE x.key IN $files OR x.key IN $modules RETURN p, x.key",
            {"files": files, "modules": modules},
        ):
            add(pn, "owns", x, path=[pn.properties["key"], "OWNS", x])
    if items:
        for pn, it, at in t.q(
            "MATCH (p:Person)-[:AUTHORED]->(i) WHERE i.key IN $items RETURN p, i.key, i.created_at", {"items": items}
        ):
            add(pn, "authored", it, at, path=[pn.properties["key"], "AUTHORED", it])
        for pn, it, at in t.q(
            "MATCH (p:Person)-[r:REVIEWED]->(i) WHERE i.key IN $items RETURN p, i.key, coalesce(r.at, i.created_at)",
            {"items": items},
        ):
            add(pn, "reviewed", it, at, path=[pn.properties["key"], "REVIEWED", it])
        for pn, it, at, cnt in t.q(
            "MATCH (p:Person)-[c:COMMENTED_ON]->(i) WHERE i.key IN $items RETURN p, i.key, c.last_at, c.count",
            {"items": items},
        ):
            add(pn, "commented", it, at, extra=float(min(int(cnt or 1), 5)),
                path=[pn.properties["key"], "COMMENTED_ON", it])
    if decisions:
        for pn, d, at, role in t.q(
            "MATCH (p:Person)-[m:MADE]->(d:Decision) WHERE d.key IN $decisions RETURN p, d.key, d.decided_at, m.role",
            {"decisions": decisions},
        ):
            add(pn, "decided", d, at, path=[pn.properties["key"], "MADE", d])

    ranked = sorted(people.items(), key=lambda kv: -sum(kv[1]["breakdown"].values()))[:limit]
    experts = []
    for pk, e in ranked:
        pd = pnodes[pk]
        t.add_node(pd)
        ev = sorted(e["evidence"], key=lambda x: -x["weight"])
        for x in ev[:6]:
            p = x["path"]
            for i in range(0, len(p) - 2, 2):
                t.add_edge(p[i], p[i + 2], p[i + 1])
        experts.append({
            "key": pk, "login": pd["props"].get("login"), "name": pd["props"].get("name") or pd["props"].get("login"),
            "score": round(sum(e["breakdown"].values()), 3),
            "breakdown": {k: round(v, 3) for k, v in e["breakdown"].items() if v},
            "evidence_count": len(ev), "top_evidence": [{k: v for k, v in x.items()} for x in ev[:5]],
        })
    # Add the scope nodes that appear in evidence paths so the trace graph is connected.
    path_keys = {k for e in experts for x in e["top_evidence"] for k in x["path"][::2]}
    if path_keys - set(t.nodes):
        for (n,) in t.q("UNWIND $keys AS k MATCH (n {key: k}) RETURN n", {"keys": list(path_keys - set(t.nodes))}):
            t.add_fnode(n)
    return t.result({
        "target": target, "anchors": [a["id"] for a in anchors],
        "scope": {"files": len(files), "modules": len(modules), "items": len(items), "decisions": len(decisions)},
        "method": "sum(weight[type] * 0.5^(age_days/180)); weights " + ", ".join(f"{k}={v}" for k, v in WEIGHTS.items()),
        "experts": experts,
    })
