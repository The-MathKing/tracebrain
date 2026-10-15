import pytest

from fixtures_b import R, fk, graph, tenant  # noqa: F401
from tracebrain import memory
from tracebrain.tools import ToolError
from tracebrain.tools.registry import dispatch


def test_remember_recall_and_isolation(graph):
    sid = memory.start_session(graph, "u1")
    memory.remember(graph, "u1", "Works on the vector index in widgets", "semantic", [fk("core/vector_index.py")])
    memory.remember(graph, "u1", "Prefers bullet-point answers", "preference")
    memory.remember(graph, "u2", "Works on routes docs", "semantic")
    ek = memory.add_episode(graph, "u1", sid, "who owns core?", "[[person:carol]] owns it", "navigator",
                            ["person:carol", "bogus:key"])
    rec = memory.recall(graph, "u1", "what changed in core/vector_index.py recently?")
    d = rec["data"]
    texts = {f["text"]: f for f in d["facts"]}
    assert "Works on the vector index in widgets" in texts
    assert any(w.startswith("about ") or w == "text match" for w in texts["Works on the vector index in widgets"]["why"])
    assert "Prefers bullet-point answers" in texts  # preferences always recalled
    assert "Works on routes docs" not in texts      # other user's fact is invisible
    assert d["episodes"][0]["key"] == ek
    cited = graph.query("MATCH (:Episode {key:$e})-[:CITED]->(x) RETURN x.key", {"e": ek}).result_set
    assert cited == [["person:carol"]]
    # u2 sees only its own
    assert [f["text"] for f in memory.recall(graph, "u2", "routes")["data"]["facts"]] == ["Works on routes docs"]


def test_supersede_and_dedupe(graph):
    old = memory.remember(graph, "u1", "Prefers answers in Python", "preference")["data"]["fact"]["key"]
    dup = memory.remember(graph, "u1", "prefers answers in python", "preference")["data"]
    assert dup["created"] is False and dup["fact"]["key"] == old
    new = memory.remember(graph, "u1", "Prefers answers in Rust", "preference", supersedes=[old])["data"]
    assert new["superseded"] == [old]
    m = memory.get_memory(graph, "u1")
    by = {f["key"]: f for f in m["facts"]}
    assert by[old]["valid"] is False and by[new["fact"]["key"]]["supersedes"] == [old]
    texts = [f["text"] for f in memory.recall(graph, "u1", "answers language")["data"]["facts"]]
    assert "Prefers answers in Rust" in texts and "Prefers answers in Python" not in texts
    # another user can't supersede/delete u1's fact
    memory.remember(graph, "u2", "x fact", supersedes=[new["fact"]["key"]])
    assert {f["key"]: f for f in memory.get_memory(graph, "u1")["facts"]}[new["fact"]["key"]]["valid"] is True
    assert memory.delete_fact(graph, "u2", new["fact"]["key"]) is False
    assert memory.delete_fact(graph, "u1", new["fact"]["key"]) is True


def test_episode_next_chain_and_sessions(graph):
    s1 = memory.start_session(graph, "u1")
    e1 = memory.add_episode(graph, "u1", s1, "q1", "a1", "navigator", [])
    s2 = memory.start_session(graph, "u1")
    e2 = memory.add_episode(graph, "u1", s2, "q2", "a2", "navigator", [])
    assert graph.query("MATCH (:Episode {key:$a})-[:NEXT]->(b:Episode) RETURN b.key", {"a": e1}).result_set == [[e2]]
    m = memory.get_memory(graph, "u1")
    assert len(m["sessions"]) == 2 and [e["question"] for e in m["episodes"]] == ["q2", "q1"]
    with pytest.raises(ToolError):
        memory.start_session(graph, "u2", s1)  # someone else's session


def test_recall_via_registry_uses_context_user(graph):
    memory.remember(graph, "u1", "Owns triage on Mondays", "procedural")
    res = dispatch(graph, "recall", {"query": "triage", "user_id": "u2"}, {"user_id": "u1"})
    assert [f["text"] for f in res["data"]["facts"]] == ["Owns triage on Mondays"]
    with pytest.raises(ToolError):
        memory.recall(graph, "bad user!", "x")
