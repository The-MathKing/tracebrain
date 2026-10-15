import httpx
import pytest

from fixtures_b import FakeAnthropic, R, fk, graph, message, tenant, text_block, tool_block  # noqa: F401
from tracebrain import config, memory
from tracebrain.agents import llm as llm_mod
from tracebrain.agents.coordinator import route, run_chat
from tracebrain.agents.llm import LLM


def _types(events):
    return [e["type"] for e in events]


def _hist(facts):
    return message(tool_block("record_memory", {"facts": facts}))


def test_navigator_turn(graph):
    fake = FakeAnthropic([
        message(text_block("Let me check. "),
                tool_block("search_entities", {"query": "vector index"}),
                tool_block("find_experts", {"target": fk("core/vector_index.py")})),
        message(text_block(f"[[person:carol]] knows it best: she owns [[module:{R}:core]] and authored "
                           f"[[pr:{R}#13]]. [[nope:missing]]")),
        _hist([{"text": "Works on the vector index", "kind": "semantic",
                "about_keys": [fk("core/vector_index.py")], "supersedes": []}]),
    ])
    events = list(run_chat(graph, LLM(client=fake), "u1", "Who knows the vector index code?"))
    types = _types(events)
    assert types[0] == "task" and types[-1] == "done"
    assert "error" not in types
    # order: recall step+memory before tools; tokens; answer; historian memory; done
    assert types.index("memory") < types.index("subgraph") < types.index("answer")
    steps = [e for e in events if e["type"] == "step"]
    assert [s["tool"] for s in steps] == ["recall", "handoff", "search_entities", "find_experts", "remember"]
    assert all(set(s) >= {"agent", "tool", "input", "summary"} for s in steps)
    sg = [e for e in events if e["type"] == "subgraph"]
    assert sg[1]["highlight"][0] == fk("core/vector_index.py") and "person:carol" in sg[1]["highlight"]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens.startswith("Let me check.")
    ans = next(e for e in events if e["type"] == "answer")
    assert [c["id"] for c in ans["citations"]] == ["person:carol", f"module:{R}:core", f"pr:{R}#13"]
    assert set(ans["citations"][0]) == {"id", "label", "title", "url", "props"}
    mem = [e for e in events if e["type"] == "memory"]
    assert mem[-1]["written"][0]["text"] == "Works on the vector index"
    done = events[-1]
    assert done["session_id"]

    # request shape: cached system+tools, adaptive thinking, effort, auto tool choice
    c0 = fake.calls[0]
    assert c0["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert c0["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert c0["thinking"] == {"type": "adaptive"} and c0["output_config"] == {"effort": "medium"}
    assert c0["tool_choice"] == {"type": "auto"} and c0["model"] == config.MODEL
    # both parallel tool results in ONE user message, after the full assistant content
    msgs = fake.calls[1]["messages"]
    assert msgs[-2]["role"] == "assistant" and len(msgs[-2]["content"]) == 3
    assert msgs[-1]["role"] == "user" and [b["type"] for b in msgs[-1]["content"]] == ["tool_result", "tool_result"]

    # graph state: task tree + episode
    rows = graph.query("MATCH (a:Agent)-[:CLAIMED]->(t:Task) RETURN a.key, t.kind, t.status ORDER BY t.created_ts").result_set
    assert rows == [["agent:coordinator", "chat", "done"], ["agent:navigator", "navigate", "done"],
                    ["agent:historian", "remember", "done"]]
    assert graph.query("MATCH (:Task {kind:'chat'})-[:HANDOFF_TO]->(:Task {kind:'navigate'})-[:HANDOFF_TO]->"
                       "(:Task {kind:'remember'}) RETURN count(*)").result_set == [[1]]
    assert graph.query("MATCH (:Task {kind:'navigate'})-[:TOUCHED]->(:Person {key:'person:carol'}) RETURN count(*)"
                       ).result_set == [[1]]
    assert graph.query("MATCH (t:Task)-[:FOR]->(s:Session {key:$s}) RETURN count(t)",
                       {"s": f"session:{done['session_id']}"}).result_set == [[3]]
    m = memory.get_memory(graph, "u1")
    assert m["episodes"][0]["agent"] == "navigator" and "person:carol" in m["episodes"][0]["cited"]
    assert m["facts"][0]["text"] == "Works on the vector index"
    learned = graph.query("MATCH (:Fact)-[:LEARNED_FROM]->(e:Episode) RETURN e.key").result_set
    assert learned == [[m["episodes"][0]["key"]]]

    # second turn in the same session recalls the fact and the previous episode
    fake2 = FakeAnthropic([message(text_block("Again: [[person:carol]].")), _hist([])])
    ev2 = list(run_chat(graph, LLM(client=fake2), "u1", "vector index again?", done["session_id"]))
    rec = next(e for e in ev2 if e["type"] == "memory")
    assert "Works on the vector index" in [r["text"] for r in rec["recalled"]]
    assert "<memory>" in fake2.calls[0]["messages"][0]["content"]
    assert ev2[-1]["session_id"] == done["session_id"]


def _gh_transport():
    def handler(req: httpx.Request):
        assert req.method == "GET"  # triage never writes
        if req.url.path == f"/repos/{R}/issues/2":
            return httpx.Response(200, json={"html_url": f"https://github.com/{R}/issues/2",
                                             "title": "Vector index crash when graph is empty",
                                             "body": "Server crashes creating vector index with no nodes.",
                                             "state": "open", "labels": [{"name": "bug"}], "user": {"login": "carol"}})
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_triage_turn(graph):
    plan = {
        "summary": "Crash creating a vector index on an empty graph; duplicate of #1.",
        "related_files": [{"key": fk("core/vector_index.py"), "reason": "vector index implementation"}],
        "modules": [f"module:{R}:core"],
        "blast_radius": [{"key": fk("core/graph.py"), "distance": 1}],
        "duplicates": [{"key": f"issue:{R}#1", "similarity": 0.9, "reason": "same crash, fixed by #10"}],
        "assignee": {"key": "person:carol", "login": "carol", "reason": "owns core"},
        "reviewers": [{"key": "person:alice", "login": "alice", "reason": "fixed #1"},
                      {"key": "person:carol", "login": "carol", "reason": "dup of assignee"},
                      {"key": "person:bob", "login": "bob", "reason": "reviewed #10"}],
        "labels": ["bug"], "priority": "high", "draft_comment": "Looks like a duplicate of #1.",
    }
    fake = FakeAnthropic([
        message(tool_block("find_related_work", {"text": "Vector index crash when graph is empty"}),
                tool_block("blast_radius", {"key": fk("core/vector_index.py")})),
        message(text_block("Submitting."), tool_block("submit_triage_plan", plan)),
        _hist([]),
    ])
    msg = f"triage https://github.com/{R}/issues/2"
    assert route(msg) == "triage" and route("who owns core?") == "navigator"
    events = list(run_chat(graph, LLM(client=fake), "u1", msg, http=_gh_transport()))
    types = _types(events)
    assert "error" not in types, events
    steps = [e["tool"] for e in events if e["type"] == "step"]
    assert steps[:3] == ["recall", "handoff", "fetch_github_issue"]
    tri = next(e for e in events if e["type"] == "triage")["plan"]
    assert tri["issue"]["key"] == f"issue:{R}#2" and tri["assignee"]["login"] == "carol"
    assert [r["login"] for r in tri["reviewers"]] == ["alice", "bob"]  # assignee removed, max 2
    assert tri["posted"] is False
    assert "Vector index crash" in fake.calls[0]["messages"][0]["content"]
    assert fake.calls[0]["tools"][-1]["name"] == "submit_triage_plan"
    ans = next(e for e in events if e["type"] == "answer")
    assert "person:carol" in [c["id"] for c in ans["citations"]]
    t = graph.query("MATCH (t:Task {kind:'triage'}) RETURN t.status, t.output").result_set[0]
    assert t[0] == "done" and '"plan"' in t[1]


def test_triage_fallback_plan_when_model_never_submits(graph):
    fake = FakeAnthropic([message(text_block("I think carol.")), message(text_block("Still no tool.")), _hist([])])
    events = list(run_chat(graph, LLM(client=fake), "u1", "triage: vector index crashes on empty graph"))
    tri = next(e for e in events if e["type"] == "triage")["plan"]
    assert tri["assignee"]["key"] and tri["duplicates"]
    assert "fallback_plan" in [e.get("tool") for e in events if e["type"] == "step"]


def test_missing_key_yields_clear_error(graph, monkeypatch):
    def boom():
        raise RuntimeError("No LLM key: set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env")
    monkeypatch.setattr(config, "make_anthropic_client", boom)
    events = list(run_chat(graph, LLM(), "u1", "who knows core?"))
    err = next(e for e in events if e["type"] == "error")
    assert "LLM not configured" in err["message"] and "ANTHROPIC_API_KEY" in err["message"]
    assert events[-1]["type"] == "done"
    statuses = graph.query("MATCH (t:Task) RETURN t.status").result_set
    assert statuses and all(s == ["failed"] for s in statuses)


def test_refusal_stops(graph):
    fake = FakeAnthropic([message(text_block(""), stop_reason="refusal")])
    events = list(run_chat(graph, LLM(client=fake), "u1", "who knows core?"))
    assert any(e["type"] == "error" and "declined" in e["message"] for e in events)
    assert events[-1]["type"] == "done"


class _BadRequest(Exception):
    status_code = 400


def test_feature_downgrade_on_400(graph, monkeypatch):
    monkeypatch.setattr(llm_mod, "_DISABLED", set())
    calls = []

    def first(kw):
        calls.append(kw)
        raise _BadRequest("thinking: unknown parameter for this provider")

    def second(kw):
        calls.append(kw)
        raise _BadRequest("output_config.effort is not supported")

    fake = FakeAnthropic([first, second, message(text_block("ok [[person:bob]]")), _hist([])])
    events = list(run_chat(graph, LLM(client=fake), "u1", "who is bob?"))
    assert next(e for e in events if e["type"] == "answer")["text"] == "ok [[person:bob]]"
    final = fake.calls[2]
    assert "thinking" not in final and "output_config" not in final and "cache_control" in final["system"][0]
    assert llm_mod._DISABLED == {"thinking", "output_config"}
    # the historian's create() call also skips the downgraded params
    assert "thinking" not in fake.calls[3]


def test_non_feature_400_is_not_retried(graph, monkeypatch):
    monkeypatch.setattr(llm_mod, "_DISABLED", set())

    def bad(kw):
        raise _BadRequest("messages: roles must alternate")

    events = list(run_chat(graph, LLM(client=FakeAnthropic([bad])), "u1", "q?"))
    assert any(e["type"] == "error" and "alternate" in e["message"] for e in events)
