"""Coordinator: one chat turn = Task tree in the graph.

chat task (coordinator) --HANDOFF_TO--> navigate|triage task --HANDOFF_TO--> remember task (historian)

Events (SSE payloads): task, step, subgraph, token, answer, triage, memory, error, done (see SPEC)."""

from __future__ import annotations

import logging
import re
from typing import Iterator

from .. import memory
from ..tools.common import ToolError, nodes_by_keys
from . import board, historian, navigator, triage
from .llm import LLM, AgentRun, LLMNotConfigured, cited_keys

log = logging.getLogger("tracebrain.coordinator")

TRIAGE_RE = re.compile(r"^\s*triage\b", re.IGNORECASE)
# Questions about the user's own history: recall gets more episodes plus their recent work facts.
SELF_RE = re.compile(r"\b(last time|last session|previous(ly)?|earlier|remind me|where (did|was) i|what (was|were|did|am) i|"
                     r"i was (working|looking|investigating)|my (work|questions|history))\b", re.IGNORECASE)


def recall_for(graph, user_id: str, message: str) -> dict:
    """memory.recall, widened for self-referential questions ("what was I working on last time?"), whose words
    rarely match the stored facts' text."""
    if not SELF_RE.search(message or ""):
        return memory.recall(graph, user_id, message)["data"]
    rec = memory.recall(graph, user_id, message, episodes=6)["data"]
    have = {f["key"] for f in rec["facts"]}
    extra = [f for f in memory.get_memory(graph, user_id, limit=12)["facts"]
             if f.get("valid") and f["key"] not in have][:6]
    rec["facts"] = rec["facts"] + [{**f, "why": ["recent"]} for f in extra]
    return rec


def route(message: str) -> str:
    if triage.ISSUE_URL_RE.search(message or "") or TRIAGE_RE.match(message or ""):
        return "triage"
    return "navigator"


def _mem_items(facts: list[dict]) -> list[dict]:
    return [{"key": f["key"], "text": f["text"], "kind": f["kind"]} for f in facts]


def run_chat(graph, llm: LLM, user_id: str, message: str, session_id: str | None = None,
             http=None) -> Iterator[dict]:
    message = (message or "").strip()
    try:
        session_id = memory.start_session(graph, user_id, session_id)
    except ToolError as e:
        yield {"type": "error", "message": str(e)}
        yield {"type": "done", "session_id": session_id}
        return
    context = {"user_id": user_id, "session_id": session_id}
    root = board.create_task(graph, "chat", "coordinator", message, session_id, status="running")
    yield board.task_event(root)
    child = hist = None
    try:
        if not message:
            raise ToolError("empty message")
        # 1. recall memory first
        recalled = recall_for(graph, user_id, message)
        yield {"type": "step", "agent": "coordinator", "tool": "recall", "input": {"query": message[:200]},
               "summary": f"recalled {len(recalled['facts'])} facts, {len(recalled['episodes'])} episodes"}
        yield {"type": "memory", "written": [], "recalled": _mem_items(recalled["facts"])}

        # 2. route + hand off
        agent = route(message)
        kind = "triage" if agent == "triage" else "navigate"
        child = board.create_task(graph, kind, agent, message, session_id, parent=root["key"])
        yield board.task_event(child)
        yield {"type": "step", "agent": "coordinator", "tool": "handoff", "input": {"to": agent},
               "summary": f"routed to {agent}"}
        board.update_task(graph, child, "running")
        yield board.task_event(child)

        run = AgentRun(agent=agent)
        mod = triage if agent == "triage" else navigator
        kwargs = {"http": http} if agent == "triage" else {}
        yield from mod.run(llm, graph, message, context=context, recalled=recalled, agent_run=run, **kwargs)
        if run.error and not run.final_text:
            raise RuntimeError(run.error)

        # 3. answer with resolved citations
        keys = cited_keys(run.final_text)
        citations = nodes_by_keys(graph, keys)
        yield {"type": "answer", "text": run.final_text, "citations": citations, "agent": agent,
               "usage": run.usage or None}
        board.touch(graph, child, [c["id"] for c in citations] + run.touched)
        out = {"answer": run.final_text, "citations": [c["id"] for c in citations],
               "tool_calls": [{"tool": c["tool"], "input": c["input"]} for c in run.tool_calls],
               "usage": run.usage}
        if agent == "triage":
            out["plan"] = getattr(run, "plan", None)
        board.update_task(graph, child, "done", out)
        yield board.task_event(child)

        # 4. historian
        hist = board.create_task(graph, "remember", "historian", {"question": message[:500]}, session_id,
                                 parent=child["key"], status="running")
        yield board.task_event(hist)
        existing = [f for f in memory.get_memory(graph, user_id, limit=40)["facts"] if f.get("valid")]
        res = historian.record_turn(llm, graph, user_id=user_id, session_id=session_id, question=message,
                                    answer=run.final_text, agent=agent, cited=[c["id"] for c in citations],
                                    existing=existing)
        yield {"type": "step", "agent": "historian", "tool": "remember", "input": {"episode": res["episode"]},
               "summary": res["error"] or f"saved episode, {len(res['written'])} facts written"}
        yield {"type": "memory", "written": [{k: w[k] for k in ("key", "text", "kind")} for w in res["written"]],
               "recalled": []}
        board.update_task(graph, hist, "done", res)
        yield board.task_event(hist)
        board.update_task(graph, root, "done", {"routed_to": agent, "episode": res["episode"]})
        yield board.task_event(root)
    except LLMNotConfigured as e:
        yield from _fail(graph, [root, child, hist], f"LLM not configured: {e}")
    except Exception as e:  # noqa: BLE001 - every failure must reach the UI as an event
        log.exception("chat turn failed")
        yield from _fail(graph, [root, child, hist], f"{type(e).__name__}: {e}")
    yield {"type": "done", "session_id": session_id}


def _fail(graph, tasks, message: str) -> Iterator[dict]:
    yield {"type": "error", "message": message}
    for t in tasks:
        if t and t["status"] not in ("done", "failed"):
            try:
                board.update_task(graph, t, "failed", {"error": message})
                yield board.task_event(t)
            except Exception:  # pragma: no cover
                pass
