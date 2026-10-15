"""Agent task board = graph state: (Agent)-[:CLAIMED]->(Task)-[:FOR]->(Session), (Task)-[:HANDOFF_TO]->(Task),
(Task)-[:TOUCHED]->(any)."""

from __future__ import annotations

import json
import time
import uuid

from ..tools.common import now_iso


def _s(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)


def create_task(graph, kind: str, agent: str, input, session_id: str | None = None, parent: str | None = None,
                status: str = "queued") -> dict:
    tk = f"task:{uuid.uuid4()}"
    now = now_iso()
    graph.query(
        "MERGE (a:Agent {key: $ak}) ON CREATE SET a.title = $agent, a.name = $agent "
        "CREATE (a)-[:CLAIMED]->(t:Task {key: $tk, title: $title, kind: $kind, status: $status, agent: $agent, "
        "input: $input, output: '', created_at: $now, updated_at: $now, created_ts: $ts})",
        {"ak": f"agent:{agent}", "agent": agent, "tk": tk, "title": f"{kind}: {_s(input)[:80]}", "kind": kind,
         "status": status, "input": _s(input)[:4000], "now": now, "ts": time.time()},
    )
    if session_id:
        graph.query("MATCH (t:Task {key: $tk}), (s:Session {key: $sk}) MERGE (t)-[:FOR]->(s)",
                    {"tk": tk, "sk": f"session:{session_id}"})
    if parent:
        graph.query("MATCH (p:Task {key: $p}), (t:Task {key: $tk}) MERGE (p)-[:HANDOFF_TO]->(t)", {"p": parent, "tk": tk})
    return {"key": tk, "kind": kind, "status": status, "agent": agent}


def update_task(graph, task: dict, status: str, output=None) -> dict:
    params = {"tk": task["key"], "status": status, "now": now_iso()}
    q = "MATCH (t:Task {key: $tk}) SET t.status = $status, t.updated_at = $now"
    if output is not None:
        q += ", t.output = $output"
        params["output"] = _s(output)[:20000]
    graph.query(q, params)
    task["status"] = status
    return task


def touch(graph, task: dict, keys: list[str], limit: int = 60) -> None:
    keys = [k for k in dict.fromkeys(keys) if k and not k.startswith(("task:", "user:", "session:"))][:limit]
    if keys:
        graph.query("MATCH (t:Task {key: $tk}) UNWIND $keys AS k MATCH (x {key: k}) MERGE (t)-[:TOUCHED]->(x)",
                    {"tk": task["key"], "keys": keys})


def task_event(task: dict) -> dict:
    return {"type": "task", "task": {k: task[k] for k in ("key", "kind", "status", "agent")}}


def list_tasks(graph, limit: int = 50) -> list[dict]:
    rows = graph.ro_query(
        "MATCH (t:Task) OPTIONAL MATCH (t)-[:HANDOFF_TO]->(c:Task) OPTIONAL MATCH (t)-[:FOR]->(s:Session) "
        "WITH t, collect(DISTINCT c.key) AS handoffs, head(collect(s.key)) AS session "
        "RETURN t, handoffs, session ORDER BY t.created_ts DESC LIMIT $lim", {"lim": int(limit)}).result_set
    out = []
    for t, handoffs, session in rows:
        p = t.properties
        output = p.get("output") or ""
        try:
            output = json.loads(output) if output[:1] in "{[" else output
        except ValueError:
            pass
        out.append({"key": p["key"], "kind": p.get("kind"), "status": p.get("status"), "agent": p.get("agent"),
                    "input": p.get("input"), "output": output, "created_at": p.get("created_at"),
                    "updated_at": p.get("updated_at"), "handoffs": handoffs, "session": session})
    return out


def get_task(graph, task_key: str) -> dict | None:
    rows = graph.ro_query("MATCH (t:Task {key: $k}) RETURN t", {"k": task_key}).result_set
    if not rows:
        return None
    p = dict(rows[0][0].properties)
    try:
        p["output"] = json.loads(p.get("output") or "null")
    except ValueError:
        pass
    return p
