from __future__ import annotations

from .common import Tracer, ToolError, get_node


def blast_radius(graph, key: str, depth: int = 3, limit: int = 100) -> dict:
    """What breaks if `key` changes? Reverse IMPORTS traversal (files that import it, transitively,
    up to `depth`), affected modules, CODEOWNERS owners of affected files/modules, recent modifiers
    of affected files and Decisions that affect them. `key` may be a File, Module or PullRequest."""
    t = Tracer(graph)
    root = get_node(t, key)
    if not root:
        raise ToolError(f"no node with key {key}")
    depth = max(1, min(int(depth or 3), 6))
    if root["label"] == "File":
        seeds = [key]
    elif root["label"] == "Module":
        seeds = [r[0] for r in t.q("MATCH (f:File)-[:IN_MODULE]->(:Module {key: $k}) RETURN f.key LIMIT 200", {"k": key})]
    elif root["label"] == "PullRequest":
        seeds = [r[0] for r in t.q("MATCH (:PullRequest {key: $k})-[:MODIFIES]->(f:File) RETURN f.key LIMIT 200", {"k": key})]
    else:
        raise ToolError(f"blast_radius needs a File, Module or PullRequest key, got {root['label']}")
    for s in seeds:
        if s != key:
            t.add_edge(key, s, "MODIFIES" if root["label"] == "PullRequest" else "CONTAINS")

    dist: dict[str, int] = {s: 0 for s in seeds}
    via: dict[str, str] = {}
    frontier = list(seeds)
    for d in range(1, depth + 1):
        if not frontier:
            break
        rows = t.q(
            "MATCH (dep:File)-[:IMPORTS]->(f:File) WHERE f.key IN $frontier RETURN dep.key, f.key",
            {"frontier": frontier},
        )
        nxt = []
        for dep, f in rows:
            t.add_edge(dep, f, "IMPORTS")
            if dep not in dist:
                dist[dep] = d
                via[dep] = f
                nxt.append(dep)
        frontier = nxt
        if len(dist) > limit * 3:
            break
    all_files = list(dist)
    for (n,) in t.q("UNWIND $keys AS k MATCH (n:File {key: k}) RETURN n", {"keys": all_files[: limit * 3]}):
        t.add_fnode(n)
    affected = sorted(
        ({"key": k, "path": (t.nodes.get(k) or {}).get("title"), "distance": d, "via": via.get(k)}
         for k, d in dist.items() if d > 0),
        key=lambda x: (x["distance"], x["key"]),
    )[:limit]
    scope = seeds + [a["key"] for a in affected]

    modules = []
    for f, m in t.q("MATCH (f:File)-[:IN_MODULE]->(m:Module) WHERE f.key IN $files RETURN f.key, m", {"files": scope}):
        mk = t.add_fnode(m)
        t.add_edge(f, mk, "IN_MODULE")
        if mk not in modules:
            modules.append(mk)

    owners: dict[str, dict] = {}
    for p, x in t.q(
        "MATCH (p:Person)-[:OWNS]->(x) WHERE x.key IN $files OR x.key IN $modules RETURN p, x.key",
        {"files": scope, "modules": modules},
    ):
        pk = t.add_fnode(p)
        t.add_edge(pk, x, "OWNS")
        owners.setdefault(pk, {"key": pk, "login": p.properties.get("login"), "owns": []})["owns"].append(x)

    modifiers: dict[str, dict] = {}
    for p, pr, at, f in t.q(
        "MATCH (p:Person)-[:AUTHORED]->(pr:PullRequest)-[:MODIFIES]->(f:File) WHERE f.key IN $files "
        "RETURN p, pr.key, coalesce(pr.merged_at, pr.created_at) AS at, f.key ORDER BY at DESC LIMIT 200",
        {"files": scope},
    ):
        login = p.properties.get("login") or ""
        if login.endswith("[bot]") or p.properties.get("bot") is True:
            continue
        pk = p.properties["key"]
        m = modifiers.get(pk)
        if m is None:
            if len(modifiers) >= 8:
                continue
            t.add_fnode(p)
            m = modifiers[pk] = {"key": pk, "login": login, "last_at": at, "prs": [], "files": []}
            t.add_edge(pk, pr, "AUTHORED")
            t.add_edge(pr, f, "MODIFIES")
        if pr not in m["prs"] and len(m["prs"]) < 5:
            m["prs"].append(pr)
        if f not in m["files"] and len(m["files"]) < 5:
            m["files"].append(f)
    if modifiers:
        prk = list({pr for m in modifiers.values() for pr in m["prs"][:1]})
        for (n,) in t.q("UNWIND $keys AS k MATCH (n:PullRequest {key: k}) RETURN n", {"keys": prk}):
            t.add_fnode(n)

    decisions = []
    for d, x in t.q(
        "MATCH (d:Decision)-[:AFFECTS]->(x) WHERE x.key IN $files OR x.key IN $modules RETURN d, x.key LIMIT 20",
        {"files": scope, "modules": modules},
    ):
        dk = t.add_fnode(d)
        t.add_edge(dk, x, "AFFECTS")
        decisions.append({"key": dk, "summary": d.properties.get("summary"), "affects": x})

    return t.result({
        "root": key, "seeds": seeds, "depth": depth,
        "affected_files": affected, "affected_count": len(affected), "modules": modules,
        "owners": list(owners.values()), "recent_modifiers": list(modifiers.values()), "decisions": decisions,
    })
