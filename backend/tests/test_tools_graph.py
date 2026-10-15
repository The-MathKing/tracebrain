import pytest

from fixtures_b import R, fk, graph, tenant  # noqa: F401
from tracebrain.tools import (
    ToolError,
    blast_radius,
    centrality,
    find_experts,
    find_related_work,
    get_neighbors,
    run_cypher,
    search_entities,
    shortest_path,
    trace_decision,
)
from tracebrain.tools.registry import TOOLS, anthropic_tools, dispatch


def _check_shape(res):
    assert set(res) == {"data", "trace"}
    tr = res["trace"]
    assert set(tr) == {"cypher", "nodes", "edges"}
    assert tr["cypher"] and all(isinstance(c, str) for c in tr["cypher"])
    keys = {n["id"] for n in tr["nodes"]}
    for n in tr["nodes"]:
        assert set(n) == {"id", "label", "title", "url", "props"}
        assert n["id"] and n["label"] and isinstance(n["title"], str)
    for e in tr["edges"]:
        assert set(e) == {"source", "target", "type"}
    return keys


def test_search_entities(graph):
    res = search_entities(graph, "vector index crash")
    _check_shape(res)
    keys = [r["key"] for r in res["data"]["results"]]
    assert f"issue:{R}#1" in keys[:4] and f"issue:{R}#2" in keys[:4]
    # exact key lookup
    res = search_entities(graph, fk("core/graph.py"))
    assert res["data"]["results"][0]["key"] == fk("core/graph.py")
    # label filter
    res = search_entities(graph, "alice", labels=["Person"])
    assert res["data"]["results"][0]["key"] == "person:alice"


def test_get_neighbors(graph):
    res = get_neighbors(graph, fk("core/graph.py"))
    keys = _check_shape(res)
    nb = {n["key"]: n for n in res["data"]["neighbors"]}
    assert nb[fk("api/server.py")]["via"] == "IMPORTS" and nb[fk("api/server.py")]["direction"] == "in"
    assert f"pr:{R}#11" in nb and fk("core/graph.py") in keys
    res = get_neighbors(graph, fk("core/graph.py"), rel_types=["IMPORTS"], depth=2)
    assert {n["key"] for n in res["data"]["neighbors"]} == {fk("api/server.py"), fk("api/routes.py"),
                                                            fk("core/vector_index.py"), fk("core/util.py")}
    with pytest.raises(ToolError):
        get_neighbors(graph, fk("core/graph.py"), rel_types=["X) DELETE n //"])


def test_find_experts_breakdown(graph):
    res = find_experts(graph, fk("core/vector_index.py"))
    _check_shape(res)
    ex = res["data"]["experts"]
    logins = [e["login"] for e in ex]
    assert "dependabot[bot]" not in logins
    # carol: recent PR #13 + OWNS core; alice: old PR #10 + recent review of #13
    assert logins[0] == "carol"
    carol = ex[0]
    assert carol["breakdown"]["owns"] == 5.0 and carol["breakdown"]["modified"] > 2.5
    alice = next(e for e in ex if e["login"] == "alice")
    assert alice["breakdown"]["modified"] < 1.0  # 290 days old, decayed
    assert alice["breakdown"]["reviewed"] > 1.5
    assert any(x["path"] for x in carol["top_evidence"])
    edges = {(e["source"], e["type"], e["target"]) for e in res["trace"]["edges"]}
    assert ("person:carol", "AUTHORED", f"pr:{R}#13") in edges


def test_find_experts_free_text(graph):
    res = find_experts(graph, "vector index")
    assert res["data"]["experts"] and res["data"]["anchors"]


def test_trace_decision(graph):
    res = trace_decision(graph, "why quantized HNSW vector index")
    _check_shape(res)
    d = res["data"]["decisions"][0]
    assert d["key"] == "decision:bbbbbbbbbbbb"
    assert d["supersedes"] == ["decision:aaaaaaaaaaaa"]
    assert d["sources"][0]["key"] == f"pr:{R}#13"
    assert d["people"][0]["login"] == "carol"
    old = trace_decision(graph, "decision:aaaaaaaaaaaa")["data"]["decisions"][0]
    assert old["superseded_by"] == ["decision:bbbbbbbbbbbb"] and old["affects"] == [f"module:{R}:core"]
    assert {p["role"] for p in old["people"]} == {"proposer", "approver"}


def test_trace_decision_fallback(graph):
    res = trace_decision(graph, "routes documentation")
    assert res["data"]["decisions"] == [] and res["data"]["discussions"]


def test_blast_radius(graph):
    res = blast_radius(graph, fk("core/vector_index.py"), depth=3)
    _check_shape(res)
    d = res["data"]
    dist = {a["key"]: a["distance"] for a in d["affected_files"]}
    assert dist == {fk("core/graph.py"): 1, fk("api/server.py"): 2, fk("api/routes.py"): 3}
    assert fk("core/util.py") not in dist  # forward import, not affected
    assert [o["login"] for o in d["owners"]] == ["carol"]
    assert {m["login"] for m in d["recent_modifiers"]} >= {"alice", "bob", "carol"}
    assert f"module:{R}:api" in d["modules"]
    assert any(x["key"] == "decision:aaaaaaaaaaaa" for x in d["decisions"])
    assert blast_radius(graph, fk("core/vector_index.py"), depth=1)["data"]["affected_count"] == 1
    # PR key -> modified files as seeds
    assert blast_radius(graph, f"pr:{R}#11")["data"]["seeds"] == [fk("core/graph.py")]


def test_find_related_work(graph):
    res = find_related_work(graph, f"issue:{R}#2")
    _check_shape(res)
    c = res["data"]["candidates"]
    assert c[0]["key"] in (f"issue:{R}#1", f"pr:{R}#10")
    one = next(x for x in c if x["key"] == f"issue:{R}#1")
    assert one["resolved_by"] == [f"pr:{R}#10"] and "vector" in one["shared_terms"]
    assert all(x["key"] != f"issue:{R}#2" for x in c)


def test_shortest_path(graph):
    res = shortest_path(graph, "person:bob", f"issue:{R}#1")
    _check_shape(res)
    d = res["data"]
    assert d["found"] and d["path"][0] == "person:bob" and d["path"][-1] == f"issue:{R}#1"
    assert d["length"] == 2 and d["rels"] == ["REVIEWED", "CLOSES"]
def test_centrality(graph):
    res = centrality(graph, "File", "pagerank")
    _check_shape(res)
    assert res["data"]["method"].startswith("pagerank")
    assert res["data"]["ranking"][0]["key"] == fk("core/util.py")  # most depended-upon (transitively)
    res = centrality(graph, "Person", "degree")
    assert res["data"]["ranking"][0]["key"] in ("person:alice", "person:bob", "person:carol")
    assert all("bot" not in r["key"] for r in res["data"]["ranking"])


def test_run_cypher_read_only(graph):
    res = run_cypher(graph, "MATCH (p:Person {login: $l}) RETURN p, p.login", {"l": "alice"})
    assert res["data"]["rows"][0][1] == "alice"
    assert res["trace"]["nodes"][0]["id"] == "person:alice"
    for q in ["CREATE (n:X)", "MATCH (n) DETACH DELETE n", "MATCH (n) SET n.x = 1", "MERGE (n:X {key:'a'})",
              "CALL db.idx.fulltext.drop('Issue')", "MATCH (n) REMOVE n.key", "DROP INDEX ON :Person(key)"]:
        with pytest.raises(ToolError):
            run_cypher(graph, q)
    # keyword inside a string literal is fine
    assert run_cypher(graph, "MATCH (i:Issue) WHERE i.title CONTAINS 'SET' RETURN count(i)")["data"]["rows"] == [[0]]


def test_registry(graph):
    defs = anthropic_tools()
    assert [d["name"] for d in defs] == [t.name for t in TOOLS]
    for d in defs:
        if d.get("strict"):
            stack = [d["input_schema"]]
            while stack:
                s = stack.pop()
                if s.get("type") == "object":
                    assert s.get("additionalProperties") is False
                    stack += list(s.get("properties", {}).values())
                if s.get("type") == "array":
                    stack.append(s["items"])
    assert "user_id" not in next(d for d in defs if d["name"] == "recall")["input_schema"]["properties"]
    res = dispatch(graph, "search_entities", {"query": "alice", "bogus": 1})
    assert res["data"]["results"]
    with pytest.raises(ToolError):
        dispatch(graph, "recall", {"query": "x"})  # no user in context
    with pytest.raises(ToolError):
        dispatch(graph, "nope", {})
