# TraceBrain — shared build spec (source of truth for all builders)

Graph Hacks (FalkorDB) entry. Deadline: Oct 18, 2026 11:59 PM IST. FalkorDB must be the primary DB and central.

**Pitch:** A company brain for engineering orgs. Ingest GitHub (people, issues, PRs, reviews, files, code imports, CODEOWNERS) plus LLM-extracted *decisions* into FalkorDB. A small team of agents answers "who knows X / why was Y decided / what breaks if Z changes", triages new issues (picks owners, reviewers, blast radius, duplicates), remembers each user across sessions, and coordinates via shared graph state. Every answer shows the graph path it came from.
Demo tenant: FalkorDB org repos `FalkorDB/GraphRAG-SDK`, `FalkorDB/falkordb-py`, `FalkorDB/QueryWeaver` (all Python).

Tracks: T1 (triage agent acts on graph, graph algos as MCP tools), T2 (episodic/semantic/procedural memory, per-tenant graphs, agent task board), T3 (company brain with sources + incremental sync).

## Layout
```
backend/            python package `tracebrain` (Python 3.14 venv at /Volumes/2TB/graphhacks/.venv)
  pyproject.toml    (owner: integrator; builders list deps they need in their report)
  tracebrain/
    config.py       env settings (owner: B)          db.py   connection helper (owner: B, see below)
    schema.py       labels/indexes (owner: A)        ingest/ github.py code.py extract.py sync.py cli.py (owner: A)
    tools/          graph tool functions (owner: B)  agents/ llm.py coordinator.py navigator.py triage.py historian.py (owner: B)
    memory.py       (owner: B)                       api.py FastAPI app (owner: B)    mcp_server.py (owner: B)
  tests/            test_ingest_*.py (A), test_tools_*.py / test_agents_*.py / test_api_*.py (B)
web/                Vite + React + TS app (owner: C)
```
Builders only write files they own. Don't touch others' files; if you need a change there, say so in your report.

## Environment
- `.env` at repo root (never commit): `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` (locally: `gh auth token`), `FALKORDB_URL` (unset → embedded falkordblite at `data/falkordb.db`), `TRACEBRAIN_MODEL` (default `claude-opus-5-5`), `TRACEBRAIN_TENANT` (default `falkordb`).
- **db.py contract:** `get_db()` returns a FalkorDB client (`falkordb.FalkorDB.from_url(url)` if FALKORDB_URL else `redislite.falkordb_client.FalkorDB(path)`); `get_graph(tenant: str | None = None)` returns `db.select_graph(f"org_{tenant}")`. One graph per tenant (multigraph isolation). Tests use tenant `test_<uuid>` and delete the graph afterwards.
- No ANTHROPIC_API_KEY is available yet. Everything must run without it: ingest skips decision extraction; agents are tested with a fake LLM client. Live-call paths must fail clearly if the key is missing.

## Graph schema (tenant graph `org_<tenant>`)
Every node has a unique string `key` (exact formats below), plus `title` (a human display string) and `url` when there is one. Datetimes are ISO-8601 strings; also store `*_ts` as int epoch seconds when needed for range queries.

Nodes:
- `Person {key:"person:<login>", login, name, avatar_url}`
- `Repo {key:"repo:<owner>/<name>", name, description}`
- `Module {key:"module:<owner>/<name>:<dir>", path}`, i.e. a top-level package or dir
- `File {key:"file:<owner>/<name>:<path>", path, language}`
- `Issue {key:"issue:<owner>/<name>#<n>", number, body, state, labels, created_at, closed_at}`
- `PullRequest {key:"pr:<owner>/<name>#<n>", number, body, state, merged, created_at, merged_at}`
- `Label {key:"label:<owner>/<name>:<label>", name}`
- `Topic {key:"topic:<slug>", name}` (LLM-extracted or label-derived)
- `Decision {key:"decision:<sha1(source_key+summary)[:12]>", summary, rationale, status: proposed|accepted|superseded|rejected, decided_at, quote, source_url}`
- Memory: `User {key:"user:<id>"}`, `Session {key:"session:<uuid>", started_at}`, `Episode {key:"episode:<uuid>", question, answer, agent, created_at}`, `Fact {key:"fact:<uuid>", text, kind: semantic|preference|procedural, confidence, created_at, updated_at, valid: bool}`
- Coordination: `Agent {key:"agent:<name>"}`, `Task {key:"task:<uuid>", kind, status: queued|running|done|failed, input, output, created_at, updated_at}`
- `SyncState {key:"sync:<owner>/<name>", last_synced_at}`

Edges:
- `(Person)-[:AUTHORED]->(Issue|PullRequest)`, `(Person)-[:REVIEWED {state, at}]->(PullRequest)`, `(Person)-[:COMMENTED_ON {count, last_at}]->(Issue|PullRequest)`
- `(PullRequest)-[:MODIFIES {additions, deletions}]->(File)`, `(File)-[:IN_MODULE]->(Module)-[:IN_REPO]->(Repo)`, `(Issue|PullRequest)-[:IN_REPO]->(Repo)`
- `(File)-[:IMPORTS]->(File)`: resolved Python imports inside the repo
- `(PullRequest)-[:CLOSES]->(Issue)` for "fixes/closes/resolves #n", `(Issue|PullRequest)-[:REFERENCES]->(Issue|PullRequest)` for other #n mentions
- `(Issue|PullRequest)-[:HAS_LABEL]->(Label)`, `(Person)-[:OWNS]->(Module|File)` from CODEOWNERS
- `(Decision)-[:DECIDED_IN]->(Issue|PullRequest)`, `(Person)-[:MADE {role: proposer|approver}]->(Decision)`, `(Decision)-[:AFFECTS]->(Module|File)`, `(Decision)-[:ABOUT]->(Topic)`, `(Decision)-[:SUPERSEDES]->(Decision)`, `(Issue|PullRequest)-[:ABOUT]->(Topic)`
- Memory: `(User)-[:HAD]->(Session)-[:CONTAINS]->(Episode)`, `(Episode)-[:NEXT]->(Episode)`, `(Episode)-[:CITED]->(any)`, `(User)-[:KNOWS]->(Fact)-[:ABOUT]->(any)`, `(Fact)-[:SUPERSEDES]->(Fact)`, `(Fact)-[:LEARNED_FROM]->(Episode)`
- Coordination: `(Agent)-[:CLAIMED]->(Task)`, `(Task)-[:HANDOFF_TO]->(Task)`, `(Task)-[:FOR]->(Session)`, `(Task)-[:TOUCHED]->(any)`

Indexes (schema.py `ensure_schema(graph)`, idempotent): a range index on `key` for every label; full-text indexes on Issue(title, body), PullRequest(title, body), Decision(summary, rationale), File(path), Topic(name), Person(login, name), Fact(text). All writes use `MERGE` on `key`, so re-ingesting is idempotent.

## Tool contract (backend/tracebrain/tools)
Each tool is a plain function `fn(graph, **args) -> ToolResult`, where `ToolResult = {"data": <json>, "trace": {"cypher": [str], "nodes": [Node], "edges": [Edge]}}`.
`Node = {"id": key, "label": str, "title": str, "url": str|None, "props": {...}}`, `Edge = {"source": key, "target": key, "type": str}`.
Tools: `search_entities(query, labels=None, limit=10)`, `get_neighbors(key, rel_types=None, depth=1, limit=50)`, `find_experts(target, limit=5)` (target = a key or free text; scores people by AUTHORED/REVIEWED/MODIFIES/OWNS/COMMENTED_ON paths with recency decay; returns each person's score breakdown and the evidence paths), `trace_decision(query)` (decision + source + people + SUPERSEDES chain + affected modules), `blast_radius(key, depth=3)` (reverse IMPORTS, plus owners of affected files), `find_related_work(text)` (similar issues/PRs → duplicate candidates), `shortest_path(a, b)`, `centrality(label="Person", algo="pagerank")` (FalkorDB `algo.pageRank`, falling back to degree), `run_cypher(query, params)` (read-only: reject CREATE/MERGE/SET/DELETE/REMOVE/DROP/CALL db.*), `recall(user_id, query)`, `remember(user_id, text, kind, about_keys)`.
The same registry is exposed (1) as Anthropic tool definitions for the agents and (2) over MCP (stdio) in `mcp_server.py`, built with the official `mcp` Python SDK (FastMCP).

## Agents (backend/tracebrain/agents)
- LLM: Anthropic Python SDK, model `TRACEBRAIN_MODEL` (default `claude-opus-5-5`), `thinking={"type":"adaptive"}`, `output_config={"effort": "medium"}`, a manual tool-use loop with streaming, `tool_choice` auto only (forced tool choice returns a 400 on this model), and prompt caching on the system prompt plus the tools. Read `/private/tmp/claude-501/bundled-skills/2.1.295/b80df0381691bb68323a67c9454c920a/claude-api/python/claude-api/{README.md,tool-use.md}` before writing LLM code. The LLM client is injectable so tests can use a fake.
- **Coordinator**: creates a `Task` in the graph, routes it to Navigator (questions) or Triage (an issue URL/text, or "triage ..."), recalls memory first and passes the relevant Facts/Episodes in, then hands off to Historian.
- **Navigator** (T3): answers questions with tools, cites node keys inline as `[[key]]`.
- **Triage** (T1): given issue text or a GitHub issue URL, it finds related files/modules, blast radius, duplicate candidates, a recommended assignee + 2 reviewers with reasons, and labels. It outputs a structured plan plus a draft GitHub comment. It NEVER posts to GitHub. A separate explicit API action does that, and it's disabled unless `TRACEBRAIN_ALLOW_GITHUB_WRITE=1`.
- **Historian** (T2): after each turn it writes an Episode (CITED edges to cited keys) and extracts/updates Facts: user preferences, things the user is working on, and procedures. Contradicted facts get `valid=false` plus SUPERSEDES.
- Every agent step emits events (below) and updates its Task node, so the "agent board" is just graph state.

## HTTP API (FastAPI, port 8000, all under /api, CORS for localhost:5173)
- `POST /api/chat` body `{user_id, session_id?, message}` → `text/event-stream`, each `data:` line is JSON:
  `{"type":"task","task":{key,kind,status,agent}}` · `{"type":"step","agent","tool","input","summary"}` · `{"type":"subgraph","nodes":[Node],"edges":[Edge],"highlight":[key]}` · `{"type":"token","text"}` (answer streaming) · `{"type":"answer","text","citations":[Node]}` · `{"type":"triage","plan":{...}}` · `{"type":"memory","written":[{key,text,kind}],"recalled":[{key,text,kind}]}` · `{"type":"error","message"}` · `{"type":"done","session_id"}`
- `GET /api/graph/node/{key}?depth=1` → `{nodes, edges}` · `GET /api/graph/overview` → `{counts: {label: n}, top_people: [...], recent_decisions: [...]}`
- `GET /api/memory/{user_id}` → `{facts:[...], episodes:[...], sessions:[...]}` · `DELETE /api/memory/{user_id}/facts/{key}`
- `GET /api/tasks?limit=50` → `[{key,kind,status,agent,input,output,created_at,handoffs:[key]}]`
- `POST /api/ingest {repo, since?}` → `{job_id}` · `GET /api/ingest/{job_id}` → `{status, counts, log}`
- `GET /api/health` → `{ok, falkordb, llm_configured}`
- Key strings contain `/`, `#`, `:`, so the frontend must `encodeURIComponent` them and the backend route must use a `{key:path}` param.

## Web (web/)
Vite + React + TypeScript, `react-force-graph-2d`, dev proxy `/api` → `http://localhost:8000`. Single page, three panes: (left) chat with a collapsible agent-step timeline per answer, inline `[[key]]` citations rendered as chips that focus the node; (center) an evidence graph that accumulates `subgraph` events, colored by label, with the `highlight` path emphasized and click-to-expand via /api/graph/node; (right) tabs **Memory** (facts by kind + episodes, delete fact), **Agents** (task board from /api/tasks, polled), **Triage** (renders the plan: owners, reviewers, blast radius list, duplicates, draft comment with a copy button). Header: tenant name, health dot, user switcher (a user_id stored in localStorage), "new session" button. Clean dark-first design. Must also work against the mock server described in C's brief.
