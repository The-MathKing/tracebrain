import json
from urllib.parse import quote

import httpx
import pytest

from fixtures_b import FakeAnthropic, R, fk, graph, message, tenant, text_block, tool_block  # noqa: F401
from tracebrain import config, memory
from tracebrain.agents.llm import LLM
from tracebrain.api import create_app


def sse_events(text: str) -> list[dict]:
    out = []
    for chunk in text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


@pytest.fixture
def make_client(tenant):
    def make(script=None):
        app = create_app(tenant=tenant, llm_factory=lambda: LLM(client=FakeAnthropic(script or [])))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return make


async def test_chat_sse_and_endpoints(make_client, graph):
    script = [
        message(tool_block("blast_radius", {"key": fk("core/vector_index.py")})),
        message(text_block(f"Changing it affects [[{fk('core/graph.py')}]]; ask [[person:carol]].")),
        message(tool_block("record_memory", {"facts": [
            {"text": "Prefers short answers", "kind": "preference", "about_keys": [], "supersedes": []}]})),
    ]
    async with make_client(script) as c:
        r = await c.post("/api/chat", json={"user_id": "u1", "message": "what breaks if vector_index.py changes?"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = sse_events(r.text)
        types = [e["type"] for e in events]
        assert types[0] == "task" and types[-1] == "done" and "answer" in types and "subgraph" in types
        sid = events[-1]["session_id"]
        ans = next(e for e in events if e["type"] == "answer")
        assert [n["id"] for n in ans["citations"]] == [fk("core/graph.py"), "person:carol"]

        r = await c.get("/api/graph/node/" + quote(f"pr:{R}#13", safe=""), params={"depth": 1})
        body = r.json()
        assert r.status_code == 200 and {n["id"] for n in body["nodes"]} >= {f"pr:{R}#13", "person:carol"}
        assert (await c.get("/api/graph/node/" + quote("file:nope", safe=""))).status_code == 404

        ov = (await c.get("/api/graph/overview")).json()
        assert ov["counts"]["Person"] == 4 and ov["recent_decisions"][0]["id"] == "decision:bbbbbbbbbbbb"
        assert all("bot" not in p["id"] for p in ov["top_people"])

        mem = (await c.get("/api/memory/u1")).json()
        assert mem["sessions"][0]["session_id"] == sid and mem["episodes"][0]["question"].startswith("what breaks")
        fact = mem["facts"][0]
        assert fact["text"] == "Prefers short answers"
        r = await c.delete(f"/api/memory/u2/facts/{quote(fact['key'], safe='')}")
        assert r.status_code == 404
        r = await c.delete(f"/api/memory/u1/facts/{quote(fact['key'], safe='')}")
        assert r.status_code == 200 and (await c.get("/api/memory/u1")).json()["facts"] == []

        tasks = (await c.get("/api/tasks", params={"limit": 10})).json()
        assert {t["kind"] for t in tasks} == {"chat", "navigate", "remember"}
        chat = next(t for t in tasks if t["kind"] == "chat")
        nav = next(t for t in tasks if t["kind"] == "navigate")
        assert chat["handoffs"] == [nav["key"]] and chat["status"] == "done"
        assert set(chat) >= {"key", "kind", "status", "agent", "input", "output", "created_at", "handoffs"}
        assert nav["output"]["citations"] == [fk("core/graph.py"), "person:carol"]

        h = (await c.get("/api/health")).json()
        assert h["ok"] and h["falkordb"] and "llm_configured" in h


async def test_triage_post_gated(make_client, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_GITHUB_WRITE", False)
    async with make_client() as c:
        r = await c.post("/api/triage/" + quote("task:abc", safe="") + "/post")
        assert r.status_code == 403
        monkeypatch.setattr(config, "ALLOW_GITHUB_WRITE", True)
        r = await c.post("/api/triage/" + quote("task:abc", safe="") + "/post")
        assert r.status_code == 404


async def test_chat_validation_and_missing_key(make_client, tenant, monkeypatch):
    def boom():
        raise RuntimeError("No LLM key: set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env")
    monkeypatch.setattr(config, "make_anthropic_client", boom)
    app = create_app(tenant=tenant)  # default factory -> live client path
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/api/chat", json={"user_id": "u1", "message": ""})).status_code == 422
        events = sse_events((await c.post("/api/chat", json={"user_id": "u1", "message": "hi"})).text)
        assert any(e["type"] == "error" and "LLM not configured" in e["message"] for e in events)
        assert events[-1]["type"] == "done"
        assert (await c.get("/api/ingest/nope")).status_code == 404
