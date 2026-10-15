"""LLM client wrapper and the manual tool-use agent loop (streaming, event-yielding).

- The client is injectable (`LLM(client=FakeAnthropic(...))` in tests). Live clients are built only
  through `config.make_anthropic_client()` (Anthropic direct or OpenRouter).
- Requests use adaptive thinking, `output_config.effort`, and prompt caching on system + tools. If an
  Anthropic-compatible endpoint rejects one of those (HTTP 400 naming the param), we retry once
  without it and remember the downgrade for the rest of the process.
- tool_choice is always auto (forced tool choice is a 400 on claude-opus-5-5).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .. import config
from ..tools import registry
from ..tools.common import ToolError

log = logging.getLogger("tracebrain.llm")

# Optional request features that may be dropped if an endpoint rejects them (process-wide).
OPTIONAL_FEATURES = ("thinking", "output_config", "cache_control", "strict")
_DISABLED: set[str] = set()
_FEATURE_PATTERNS = {
    "thinking": r"thinking|adaptive",
    "output_config": r"output_config|effort",
    "cache_control": r"cache_control|cache control|caching",
    "strict": r"strict",
}
TOOL_RESULT_MAX_CHARS = 14000


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def _status(e: Exception) -> int | None:
    return getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)


def _rejected_feature(e: Exception, active: set[str]) -> str | None:
    if _status(e) != 400:
        return None
    msg = str(getattr(e, "message", "") or e)
    for feat in OPTIONAL_FEATURES:
        if feat in active and re.search(_FEATURE_PATTERNS[feat], msg, re.IGNORECASE):
            return feat
    return None


class LLM:
    def __init__(self, client: Any = None, model: str | None = None, effort: str = "medium", max_tokens: int = 16000):
        self._client = client
        self.model = model or config.MODEL
        self.effort = effort
        self.max_tokens = max_tokens

    @property
    def client(self):
        if self._client is None:
            try:
                self._client = config.make_anthropic_client()
            except RuntimeError as e:
                raise LLMNotConfigured(str(e)) from e
        return self._client

    def build_kwargs(self, system: str, tools: list[dict], messages: list, max_tokens: int | None = None) -> dict:
        feats = set(OPTIONAL_FEATURES) - _DISABLED
        cache = "cache_control" in feats
        kw: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": [{"type": "text", "text": system, **({"cache_control": {"type": "ephemeral"}} if cache else {})}],
            "messages": messages,
        }
        if tools:
            ts = []
            for t in tools:
                t = dict(t)
                if "strict" not in feats:
                    t.pop("strict", None)
                ts.append(t)
            if cache:
                ts[-1] = {**ts[-1], "cache_control": {"type": "ephemeral"}}  # caches system + all tools
            kw["tools"] = ts
            kw["tool_choice"] = {"type": "auto"}
        if "thinking" in feats:
            kw["thinking"] = {"type": "adaptive"}
        if "output_config" in feats:
            kw["output_config"] = {"effort": self.effort}
        return kw, feats

    def _with_downgrade(self, call: Callable[[dict], Any], system, tools, messages, max_tokens=None):
        for _ in range(len(OPTIONAL_FEATURES) + 1):
            kw, feats = self.build_kwargs(system, tools, messages, max_tokens)
            try:
                return call(kw)
            except LLMNotConfigured:
                raise
            except Exception as e:
                feat = _rejected_feature(e, feats)
                if feat is None:
                    raise
                log.warning("endpoint rejected %s; retrying without it for this process: %s", feat, e)
                _DISABLED.add(feat)
        raise LLMError("request kept failing after dropping optional features")

    def create(self, system: str, tools: list[dict], messages: list, max_tokens: int | None = None):
        return self._with_downgrade(lambda kw: self.client.messages.create(**kw), system, tools, messages, max_tokens)

    def stream(self, system: str, tools: list[dict], messages: list) -> Iterator[tuple[str, Any]]:
        """Yields ("text", delta) while streaming, then ("message", final_message)."""
        out: list = []

        def call(kw):
            # The request is sent when the context manager is entered; 400s surface here.
            mgr = self.client.messages.stream(**kw)
            s = mgr.__enter__()
            out.append((mgr, s))
            return s

        stream = self._with_downgrade(call, system, tools, messages)
        mgr = out[-1][0]
        try:
            for delta in stream.text_stream:
                if delta:
                    yield "text", delta
            msg = stream.get_final_message()
        except BaseException as e:
            mgr.__exit__(type(e), e, e.__traceback__)
            raise
        mgr.__exit__(None, None, None)
        yield "message", msg


# ------------------------------------------------------------------------------------------ loop
@dataclass
class AgentRun:
    agent: str
    final_text: str = ""
    touched: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    stopped_by_tool: str | None = None
    error: str | None = None
    messages: list = field(default_factory=list)


def _block_get(b, attr, default=None):
    return b.get(attr, default) if isinstance(b, dict) else getattr(b, attr, default)


def highlight_keys(name: str, args: dict, data: dict) -> list[str]:
    keys: list[str] = []
    for a in ("key", "a", "b", "target"):
        v = (args or {}).get(a)
        if isinstance(v, str) and ":" in v:
            keys.append(v)
    d = data or {}
    if name == "shortest_path":
        keys += d.get("path") or []
    for k in ("results", "experts", "candidates", "decisions", "affected_files", "ranking", "neighbors", "facts"):
        for item in (d.get(k) or [])[:5]:
            if isinstance(item, dict) and item.get("key"):
                keys.append(item["key"])
    for o in (d.get("owners") or [])[:3]:
        keys.append(o["key"])
    return list(dict.fromkeys(keys))


def _tool_result_content(data: Any) -> str:
    s = json.dumps(data, default=str, ensure_ascii=False)
    if len(s) > TOOL_RESULT_MAX_CHARS:
        s = s[:TOOL_RESULT_MAX_CHARS] + '..."(truncated)"'
    return s


ExtraHandler = Callable[[str, dict], tuple[str, bool]]


def run_agent(
    llm: LLM,
    *,
    agent: str,
    system: str,
    messages: list,
    graph,
    tool_names: list[str],
    context: dict | None = None,
    extra_tools: list[dict] | None = None,
    on_extra: ExtraHandler | None = None,
    max_iters: int = 10,
    run: AgentRun | None = None,
) -> Iterator[dict]:
    """Manual tool-use loop. Yields UI events (token/step/subgraph/error); fills `run`.

    `extra_tools` are agent-specific tools (e.g. submit_triage_plan) handled by `on_extra`, which returns
    (tool_result_text, stop_loop)."""
    run = run or AgentRun(agent=agent)
    run.messages = messages
    tools = registry.anthropic_tools(tool_names) + list(extra_tools or [])
    extra_names = {t["name"] for t in extra_tools or []}
    budget_notice_sent = False
    for it in range(max_iters + 2):
        msg = None
        for kind, val in llm.stream(system, tools, messages):
            if kind == "text":
                yield {"type": "token", "text": val, "agent": agent}
            else:
                msg = val
        stop = _block_get(msg, "stop_reason")
        content = list(_block_get(msg, "content") or [])
        if stop == "refusal":
            details = _block_get(msg, "stop_details")
            run.error = "The model declined this request" + (
                f" ({_block_get(details, 'category')})" if details and _block_get(details, "category") else "")
            yield {"type": "error", "message": run.error}
            return
        tool_uses = [b for b in content if _block_get(b, "type") == "tool_use"]
        texts = [_block_get(b, "text") for b in content if _block_get(b, "type") == "text"]
        messages.append({"role": "assistant", "content": content})  # full content (thinking, tool_use)
        if stop == "pause_turn":
            continue
        if stop == "max_tokens" and tool_uses:
            run.error = "The model ran out of output tokens mid tool call"
            yield {"type": "error", "message": run.error}
            return
        if not tool_uses:
            run.final_text = "".join(t or "" for t in texts).strip()
            return
        results = []
        stop_loop = False
        for b in tool_uses:
            name, args, tid = _block_get(b, "name"), dict(_block_get(b, "input") or {}), _block_get(b, "id")
            if name in extra_names and on_extra:
                text, stop_now = on_extra(name, args)
                stop_loop = stop_loop or stop_now
                run.stopped_by_tool = name if stop_now else run.stopped_by_tool
                results.append({"type": "tool_result", "tool_use_id": tid, "content": text})
                continue
            try:
                res = registry.dispatch(graph, name, args, context)
            except ToolError as e:
                yield {"type": "step", "agent": agent, "tool": name, "input": args,
                       "summary": registry.summarize(name, args, error=str(e))}
                results.append({"type": "tool_result", "tool_use_id": tid, "content": f"Error: {e}", "is_error": True})
                continue
            except Exception as e:  # DB errors etc: let the model recover
                log.exception("tool %s failed", name)
                yield {"type": "step", "agent": agent, "tool": name, "input": args,
                       "summary": registry.summarize(name, args, error=type(e).__name__)}
                results.append({"type": "tool_result", "tool_use_id": tid, "content": f"Error: {e}", "is_error": True})
                continue
            run.tool_calls.append({"tool": name, "input": args, "data": res["data"]})
            tr = res["trace"]
            run.touched += [n["id"] for n in tr["nodes"]]
            yield {"type": "step", "agent": agent, "tool": name, "input": args,
                   "summary": registry.summarize(name, args, res), "cypher": tr["cypher"]}
            if tr["nodes"]:
                yield {"type": "subgraph", "nodes": tr["nodes"], "edges": tr["edges"],
                       "highlight": highlight_keys(name, args, res["data"]), "agent": agent}
            results.append({"type": "tool_result", "tool_use_id": tid, "content": _tool_result_content(res["data"])})
        # All results of this turn go back in ONE user message.
        user_content: list = list(results)
        if it >= max_iters - 1 and not stop_loop and not budget_notice_sent:
            user_content.append({"type": "text", "text": "Tool budget is nearly exhausted. Stop calling graph tools and "
                                                          "give your final answer now from what you have."})
            budget_notice_sent = True
        messages.append({"role": "user", "content": user_content})
        if stop_loop:
            run.final_text = "".join(t or "" for t in texts).strip()
            return
    run.error = "Agent hit its iteration limit"
    yield {"type": "error", "message": run.error}


CITE_RE = re.compile(r"\[\[([^\[\]\s]+)\]\]")


def cited_keys(text: str) -> list[str]:
    return list(dict.fromkeys(CITE_RE.findall(text or "")))
