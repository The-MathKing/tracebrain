"""Historian (T2): after each turn, writes an Episode (CITED edges) and extracts/updates Facts.
Contradicted facts are marked valid=false and linked with SUPERSEDES."""

from __future__ import annotations

import json
import logging

from .. import memory
from .llm import LLM

log = logging.getLogger("tracebrain.historian")

RECORD_TOOL = {
    "name": "record_memory",
    "description": "Record durable facts learned about the user in this turn (may be an empty list). Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {"facts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Self-contained statement, e.g. 'Prefers answers as bullet lists'"},
                "kind": {"type": "string", "enum": ["semantic", "preference", "procedural"]},
                "about_keys": {"type": "array", "items": {"type": "string"},
                               "description": "Graph keys the fact is about (from the cited keys)"},
                "supersedes": {"type": "array", "items": {"type": "string"},
                               "description": "Keys of existing facts this one contradicts or replaces"},
            },
            "required": ["text", "kind", "about_keys", "supersedes"], "additionalProperties": False}}},
        "required": ["facts"], "additionalProperties": False,
    },
    "strict": True,
}

SYSTEM = """You are the Historian of TraceBrain. After each conversation turn you decide what is worth remembering
about the USER (not about the codebase in general) for future sessions:
- preference: how they like answers (format, length, language, which repo they care about)
- semantic: what they work on / own / are investigating ("works on the vector index in falkordb-py")
- procedural: how they do things ("runs triage for every new GraphRAG-SDK issue on Mondays")
Only record facts clearly stated or strongly implied by the user. Skip one-off questions. Keep facts short and
self-contained. If a new fact contradicts an existing one, put the old fact's key in `supersedes`.
Do not duplicate existing facts. Call record_memory exactly once (with an empty list if nothing is worth keeping)."""


def extract_facts(llm: LLM, question: str, answer: str, cited: list[str], existing: list[dict]) -> list[dict]:
    ex = "\n".join(f"- {f['key']} ({f['kind']}): {f['text']}" for f in existing) or "(none)"
    prompt = (f"Existing valid facts about the user:\n{ex}\n\nUser message:\n{question}\n\n"
              f"Assistant answer (truncated):\n{answer[:2000]}\n\nCited keys: {', '.join(cited[:30]) or '(none)'}")
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        msg = llm.create(SYSTEM, [RECORD_TOOL], messages, max_tokens=4000)
        content = list(getattr(msg, "content", None) or [])
        if getattr(msg, "stop_reason", None) == "refusal":
            return []
        for b in content:
            if getattr(b, "type", None) == "tool_use" and b.name == "record_memory":
                facts = (b.input or {}).get("facts") or []
                return [f for f in facts if isinstance(f, dict) and f.get("text")]
        messages += [{"role": "assistant", "content": content},
                     {"role": "user", "content": "Call record_memory now (an empty list is fine)."}]
    return []


def record_turn(llm: LLM | None, graph, *, user_id: str, session_id: str, question: str, answer: str, agent: str,
                cited: list[str], existing: list[dict]) -> dict:
    """Write the Episode and any extracted facts. Returns {"episode": key, "written": [...], "error": str|None}."""
    ek = memory.add_episode(graph, user_id, session_id, question, answer, agent, cited)
    written, error = [], None
    if llm is not None:
        try:
            facts = extract_facts(llm, question, answer, cited, existing)
        except Exception as e:  # extraction is best-effort; the episode is already saved
            log.warning("fact extraction failed: %s", e)
            facts, error = [], f"fact extraction failed: {type(e).__name__}: {e}"
        valid_existing = {f["key"] for f in existing}
        for f in facts[:5]:
            kind = f.get("kind") if f.get("kind") in memory.FACT_KINDS else "semantic"
            sup = [k for k in f.get("supersedes") or [] if k in valid_existing or k.startswith("fact:")]
            res = memory.remember(graph, user_id, f["text"], kind, f.get("about_keys") or [], 0.8,
                                  learned_from=ek, supersedes=sup)
            fd = res["data"]["fact"]
            written.append({"key": fd["key"], "text": fd["text"], "kind": fd["kind"],
                            "superseded": res["data"]["superseded"], "created": res["data"]["created"]})
    return {"episode": ek, "written": written, "error": error}


__all__ = ["record_turn", "extract_facts", "json"]
