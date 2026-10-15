"""TraceBrain HTTP API (see SPEC "HTTP API"). Run: uvicorn tracebrain.api:app --port 8000"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config, memory
from .agents import board, triage
from .agents.coordinator import run_chat
from .agents.llm import LLM
from .db import get_db, graph_name
from .tools import ToolError, get_neighbors
from .tools.common import from_fnode

log = logging.getLogger("tracebrain.api")


class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=20000)


class IngestIn(BaseModel):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    since: str | None = None


def _ensure_schema(graph) -> None:
    try:
        from .schema import ensure_schema  # builder A's module; tolerate absence
    except ImportError:
        log.warning("tracebrain.schema not available yet; skipping ensure_schema")
        return
    try:
        ensure_schema(graph)
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_schema failed: %s", e)


def _seed_in_background(tenant: str) -> None:
    """Import the bundled snapshot when the tenant graph is empty (fresh FalkorDB Cloud instance).
    Runs in a thread so the server binds its port immediately (Render's boot timeout)."""
    def work():
        try:
            from .seed import seed_if_empty
            seed_if_empty(tenant)
        except Exception:  # noqa: BLE001
            log.exception("seed-on-start failed")

    threading.Thread(target=work, daemon=True, name="seed-on-start").start()


def _mount_web(app: FastAPI) -> None:
    """Serve the built frontend (web/dist) with an SPA fallback. Registered after every /api route."""
    dist = Path(os.getenv("TRACEBRAIN_WEB_DIST") or (config.ROOT / "web" / "dist")).resolve()
    index = dist / "index.html"
    if not index.is_file():
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        if full_path:
            f = (dist / full_path).resolve()
            if f.is_file() and f.is_relative_to(dist):
                return FileResponse(f)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(tenant: str | None = None, llm_factory: Callable[[], LLM] | None = None, http_client=None) -> FastAPI:
    tenant = tenant or config.TENANT

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _ensure_schema(app.state.graph())
        if os.getenv("TRACEBRAIN_SEED_ON_START") == "1":
            _seed_in_background(app.state.tenant)
        yield

    app = FastAPI(title="TraceBrain", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.state.tenant = tenant
    app.state.graph = lambda: get_db().select_graph(graph_name(app.state.tenant))
    app.state.llm_factory = llm_factory or (lambda: LLM())
    app.state.http = http_client  # injectable httpx client for GitHub (tests)
    app.state.jobs = {}

    # ------------------------------------------------------------------ chat
    @app.post("/api/chat")
    def chat(body: ChatIn):
        graph = app.state.graph()
        llm = app.state.llm_factory()

        def gen():
            try:
                for ev in run_chat(graph, llm, body.user_id, body.message, body.session_id, http=app.state.http):
                    yield f"data: {json.dumps(ev, default=str, ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001
                log.exception("stream failed")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'session_id': body.session_id})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ graph
    @app.get("/api/graph/node/{key:path}")
    def graph_node(key: str, depth: int = Query(1, ge=1, le=3), limit: int = Query(80, ge=1, le=300)):
        graph = app.state.graph()
        try:
            res = get_neighbors(graph, key, depth=depth, limit=limit)
        except ToolError as e:
            raise HTTPException(404, str(e))
        return {"nodes": res["trace"]["nodes"], "edges": res["trace"]["edges"], "center": key}

    @app.get("/api/graph/overview")
    def overview():
        graph = app.state.graph()
        counts = {r[0]: r[1] for r in graph.ro_query("MATCH (n) RETURN labels(n)[0], count(n)").result_set if r[0]}
        people = []
        for p, a, rv, c in graph.ro_query(
            "MATCH (p:Person) WHERE NOT p.login ENDS WITH '[bot]' AND coalesce(p.bot, false) <> true "
            "OPTIONAL MATCH (p)-[a:AUTHORED]->() WITH p, count(a) AS authored "
            "OPTIONAL MATCH (p)-[r:REVIEWED]->() WITH p, authored, count(r) AS reviewed "
            "OPTIONAL MATCH (p)-[c:COMMENTED_ON]->() WITH p, authored, reviewed, count(c) AS commented "
            "RETURN p, authored, reviewed, commented ORDER BY authored + reviewed DESC LIMIT 10").result_set:
            n = from_fnode(p)
            if n:
                people.append({**n, "authored": a, "reviewed": rv, "commented": c})
        decisions = []
        for (d,) in graph.ro_query("MATCH (d:Decision) RETURN d ORDER BY d.decided_at DESC LIMIT 10").result_set:
            n = from_fnode(d)
            if n:
                decisions.append(n)
        repos = [from_fnode(r[0]) for r in graph.ro_query("MATCH (r:Repo) RETURN r").result_set]
        return {"tenant": app.state.tenant, "counts": counts, "top_people": people, "recent_decisions": decisions,
                "repos": [r for r in repos if r]}

    # ------------------------------------------------------------------ memory
    @app.get("/api/memory/{user_id}")
    def get_memory(user_id: str):
        try:
            return memory.get_memory(app.state.graph(), user_id)
        except ToolError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/memory/{user_id}/facts/{key:path}")
    def delete_fact(user_id: str, key: str):
        try:
            ok = memory.delete_fact(app.state.graph(), user_id, key)
        except ToolError as e:
            raise HTTPException(400, str(e))
        if not ok:
            raise HTTPException(404, "fact not found for this user")
        return {"deleted": key}

    # ------------------------------------------------------------------ tasks
    @app.get("/api/tasks")
    def tasks(limit: int = Query(50, ge=1, le=500)):
        return board.list_tasks(app.state.graph(), limit)

    @app.post("/api/triage/{task_key:path}/post")
    def post_triage(task_key: str):
        if not config.ALLOW_GITHUB_WRITE:
            raise HTTPException(403, "GitHub writes are disabled. Set TRACEBRAIN_ALLOW_GITHUB_WRITE=1 to enable.")
        graph = app.state.graph()
        t = board.get_task(graph, task_key)
        if not t or t.get("kind") != "triage":
            raise HTTPException(404, "triage task not found")
        plan = (t.get("output") or {}).get("plan") if isinstance(t.get("output"), dict) else None
        if not plan or not (plan.get("issue") or {}).get("url"):
            raise HTTPException(400, "task has no plan with a GitHub issue URL")
        if plan.get("posted"):
            raise HTTPException(409, "already posted")
        try:
            res = triage.post_comment(plan["issue"]["url"], plan["draft_comment"], http=app.state.http)
        except (ToolError, PermissionError) as e:
            raise HTTPException(502, str(e))
        plan["posted"] = True
        plan["comment_url"] = res.get("url")
        board.update_task(graph, {"key": task_key, "status": t["status"]}, t["status"], {**t["output"], "plan": plan})
        return {"posted": True, "comment_url": res.get("url")}

    # ------------------------------------------------------------------ ingest
    @app.post("/api/ingest")
    def ingest(body: IngestIn):
        job_id = uuid.uuid4().hex[:12]
        job = {"status": "running", "counts": {}, "log": [], "repo": body.repo, "started": time.time()}
        app.state.jobs[job_id] = job

        def work():
            try:
                from .ingest.sync import ingest_repo
            except ImportError as e:
                job.update(status="failed", log=job["log"] + [f"ingest module unavailable: {e}"])
                return
            try:
                job["counts"] = ingest_repo(body.repo, app.state.tenant, body.since,
                                            log=lambda m: job["log"].append(str(m))) or {}
                job["status"] = "done"
            except Exception as e:  # noqa: BLE001
                job["log"].append(f"error: {type(e).__name__}: {e}")
                job["status"] = "failed"

        threading.Thread(target=work, daemon=True, name=f"ingest-{job_id}").start()
        return {"job_id": job_id}

    @app.get("/api/ingest/{job_id}")
    def ingest_status(job_id: str):
        job = app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "unknown job")
        return {"status": job["status"], "counts": job["counts"], "log": job["log"][-200:], "repo": job["repo"]}

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    def health():
        ok_db = False
        try:
            app.state.graph().ro_query("RETURN 1")
            ok_db = True
        except Exception:  # noqa: BLE001
            pass
        return {"ok": ok_db, "falkordb": ok_db, "llm_configured": config.llm_configured(),
                "tenant": app.state.tenant, "model": config.MODEL, "github_write": config.ALLOW_GITHUB_WRITE}

    _mount_web(app)  # must stay last: its catch-all route would shadow anything registered after it
    return app


app = create_app()
