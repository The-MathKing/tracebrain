"""Demo-QA hardening: compact tool results, usage accounting, org-only paths, triage fallbacks, memory recall."""

import httpx

from fixtures_b import FakeAnthropic, R, fk, graph, message, tenant, text_block, tool_block  # noqa: F401
from tracebrain import memory
from tracebrain.agents import historian, triage
from tracebrain.agents.coordinator import recall_for, run_chat
from tracebrain.agents.llm import LLM, compact_for_llm, highlight_keys
from tracebrain.tools import blast_radius, get_neighbors, shortest_path


def _hist(facts=()):
    return message(tool_block("record_memory", {"facts": list(facts)}))


def test_compact_for_llm_drops_ui_fields_and_caps_lists():
    data = {"experts": [{"key": f"person:p{i}", "url": "https://x", "score_breakdown": {"a": 1},
                         "top_evidence": [{"type": "modified", "via": "pr:x#1", "path": ["a", "b", "c"]}] * 9}
                        for i in range(40)]}
    out = compact_for_llm("find_experts", data)
    assert len(out["experts"]) == 26 and "more not shown" in out["experts"][-1]
    first = out["experts"][0]
    assert "url" not in first and "score_breakdown" not in first
    assert len(first["top_evidence"]) == 6 and "path" not in first["top_evidence"][0]


def test_compact_blast_radius_aggregates():
    files = [{"key": f"file:{R}:pkg/sub/f{i}.py", "path": f"pkg/sub/f{i}.py", "distance": 1 + i % 2,
              "via": "file:x"} for i in range(50)]
    out = compact_for_llm("blast_radius", {"affected_files": files, "affected_count": 50})
    assert out["affected_by_directory"] == {"pkg/sub": 50}
    assert len(out["affected_files"]) == 25 and "counts are complete" in out["affected_files"][-1] and set(out["affected_files"][0]) == {"key", "distance"}
    assert out["affected_count"] == 50


def test_blast_radius_reports_true_total(graph):
    d = blast_radius(graph, fk("core/vector_index.py"), depth=3, limit=1)["data"]
    assert len(d["affected_files"]) == 1 and d["affected_count"] == 3 and d["truncated"] is True
    assert d["affected_by_distance"] == {"1": 1, "2": 1, "3": 1}


def _add_memory_shortcut(graph):
    """An Episode citing two otherwise-distant nodes would create a 2-hop shortcut through memory."""
    memory.add_episode(graph, "u1", memory.start_session(graph, "u1"), "q", "a", "navigator",
                       ["person:carol", f"issue:{R}#3"])


def test_paths_and_neighbors_stay_in_org_graph(graph):
    _add_memory_shortcut(graph)
    d = shortest_path(graph, "person:carol", f"issue:{R}#3")["data"]
    assert d["found"] and not any(k.startswith(("episode:", "session:", "user:", "task:")) for k in d["path"])
    assert "CITED" not in d["rels"]
    # each step states the edge in its real direction
    assert all(s["fact"].count("-[") == 1 for s in d["steps"])
    nb = get_neighbors(graph, "person:carol", depth=2, limit=300)["data"]["neighbors"]
    assert not any(n["key"].startswith(("episode:", "session:", "user:")) for n in nb)


def test_shortest_path_prefers_non_hub_route(graph):
    d = shortest_path(graph, "person:bob", f"issue:{R}#1")["data"]
    assert d["rels"] == ["REVIEWED", "CLOSES"]
    assert d["steps"][0]["fact"] == f"person:bob -[REVIEWED]-> pr:{R}#10"


def test_highlight_experts_includes_evidence_path():
    data = {"experts": [{"key": "person:a", "top_evidence": [{"path": ["person:a", "AUTHORED", "pr:x#1", "MODIFIES",
                                                                       "file:x:y.py"]}]}]}
    keys = highlight_keys("find_experts", {"target": "file:x:y.py"}, data)
    assert keys == ["file:x:y.py", "person:a", "pr:x#1"]


def test_usage_reported_on_answer(graph):
    fake = FakeAnthropic([message(text_block("[[person:carol]] owns core.")), _hist()])
    events = list(run_chat(graph, LLM(client=fake), "u1", "who owns core?"))
    ans = next(e for e in events if e["type"] == "answer")
    assert ans["usage"]["calls"] == 1 and ans["usage"]["input_tokens"] == 1


def test_historian_runs_at_low_effort(graph):
    fake = FakeAnthropic([_hist([{"text": "Is investigating core/graph.py", "kind": "semantic",
                                  "about_keys": [fk("core/graph.py")], "supersedes": []}])])
    llm = LLM(client=fake, effort="medium")
    facts = historian.extract_facts(llm, "what breaks if core/graph.py changes?", "a lot", [], [])
    assert facts and fake.calls[0]["output_config"] == {"effort": "low"}
    assert llm.effort == "medium"  # the shared instance is untouched


def test_recall_for_self_questions_widens_memory(graph):
    sid = memory.start_session(graph, "u9")
    memory.add_episode(graph, "u9", sid, "who reviews core/graph.py?", f"[[person:bob]] and [[{fk('core/graph.py')}]]",
                       "navigator", ["person:bob"])
    memory.remember(graph, "u9", "Is investigating reviewers for core/graph.py", "semantic", [fk("core/graph.py")])
    plain = recall_for(graph, "u9", "what was I working on last time?")
    assert any("core/graph.py" in f["text"] for f in plain["facts"])
    assert plain["episodes"][0]["question"].startswith("who reviews")


def test_triage_falls_back_to_ingested_issue_when_github_fails(graph):
    def handler(req: httpx.Request):
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    plan = {"summary": "s", "related_files": [], "modules": [], "blast_radius": [], "duplicates": [],
            "assignee": {"key": "person:carol", "login": "carol", "reason": "owns core"},
            "reviewers": [], "labels": [], "priority": "low", "draft_comment": "Thanks!"}
    fake = FakeAnthropic([message(tool_block("submit_triage_plan", plan)), _hist()])
    events = list(run_chat(graph, LLM(client=fake), "u1", f"Triage https://github.com/{R}/issues/2",
                           http=httpx.Client(transport=httpx.MockTransport(handler))))
    step = next(e for e in events if e["type"] == "step" and e["tool"] == "fetch_github_issue")
    assert "ingested copy" in step["summary"]
    first = fake.calls[0]
    assert "Vector index crash when graph is empty" in first["messages"][0]["content"]
    assert first["output_config"] == {"effort": triage.EFFORT}
    tri = next(e for e in events if e["type"] == "triage")["plan"]
    assert tri["issue"]["key"] == f"issue:{R}#2" and tri["issue"]["number"] == 2


def test_trace_decision_details_superseded_chain(graph):
    from tracebrain.tools import trace_decision

    d = trace_decision(graph, "decision:bbbbbbbbbbbb")["data"]
    linked = {x["key"]: x for x in d["linked_decisions"]}
    old = linked["decision:aaaaaaaaaaaa"]
    assert old["status"] == "superseded" and old["sources"] == [f"pr:{R}#10"]
    assert {p["key"] for p in old["people"]} == {"person:alice", "person:bob"}


def test_search_finds_precise_match_in_long_question(graph):
    from tracebrain.tools import search_entities

    res = search_entities(graph, "why did we pick quantized HNSW with int8 for the vector index memory usage",
                          labels=["Decision"])["data"]["results"]
    assert res[0]["key"] == "decision:bbbbbbbbbbbb"
