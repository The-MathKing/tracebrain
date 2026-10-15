"""Drive the real anthropic SDK (no network) through a mock transport serving canned SSE, to check that
our request kwargs serialize and that streamed tool_use / text parse through the live code path."""

import json

import anthropic
import httpx2
import pytest

from fixtures_b import R, fk, graph, tenant  # noqa: F401
from tracebrain.agents import llm as llm_mod
from tracebrain.agents.coordinator import run_chat
from tracebrain.agents.llm import LLM


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _msg_start(i):
    return {"type": "message_start", "message": {"id": f"msg_{i}", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0}}}


def _tool_turn():
    args = json.dumps({"target": fk("core/vector_index.py")})
    return _sse([
        _msg_start(1),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking. "}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1",
                                                                      "name": "find_experts", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": args[:10]}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": args[10:]}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ])


def _text_turn(text):
    return _sse([
        _msg_start(2),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ])


def test_live_sdk_path(graph, monkeypatch):
    monkeypatch.setattr(llm_mod, "_DISABLED", set())
    bodies = []
    stream_responses = [_tool_turn(), _text_turn("[[person:carol]] owns it.")]

    def handler(req: httpx2.Request):
        body = json.loads(req.content)
        bodies.append(body)
        if body.get("stream"):
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stream_responses.pop(0))
        # historian create(): non-streaming tool_use
        return httpx2.Response(200, json={
            "id": "msg_h", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "tool_use", "id": "toolu_h", "name": "record_memory", "input": {"facts": []}}],
            "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.Anthropic(api_key="test", base_url="http://mock",
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    events = list(run_chat(graph, LLM(client=client), "u1", "who knows vector_index.py?"))
    types = [e["type"] for e in events]
    assert "error" not in types, [e for e in events if e["type"] == "error"]
    assert next(e for e in events if e["type"] == "answer")["citations"][0]["id"] == "person:carol"
    assert any(e["type"] == "step" and e["tool"] == "find_experts" for e in events)
    b0, b1 = bodies[0], bodies[1]
    assert b0["thinking"] == {"type": "adaptive"} and b0["output_config"] == {"effort": "medium"}
    assert b0["system"][0]["cache_control"] == {"type": "ephemeral"} and b0["tools"][-1]["cache_control"]
    assert b0["tool_choice"] == {"type": "auto"}
    # assistant content (text + tool_use) echoed back, then a single user message with the tool_result
    assert [c["type"] for c in b1["messages"][1]["content"]] == ["text", "tool_use"]
    assert b1["messages"][1]["content"][1]["input"] == {"target": fk("core/vector_index.py")}
    assert [c["type"] for c in b1["messages"][2]["content"]] == ["tool_result"]
    assert bodies[2]["tools"][0]["name"] == "record_memory" and "stream" not in bodies[2]
