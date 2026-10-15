# TraceBrain architecture

This document covers how TraceBrain is built and why. All Cypher below is copied from `backend/tracebrain` (whitespace normalized). `$params` are real query parameters; nothing user-supplied is interpolated into a query string except values that are validated first (relationship types against `^[A-Z][A-Z_]{0,40}$`, labels against a fixed allow-list, depths clamped to integers).

- [1. Why a graph](#1-why-a-graph)
- [2. Tenancy: one graph per org](#2-tenancy-one-graph-per-org)
- [3. The tool contract and traces](#3-the-tool-contract-and-traces)
- [4. Expert scoring](#4-expert-scoring)
- [5. Blast radius](#5-blast-radius)
- [6. Decisions: extraction, tracing, supersession](#6-decisions-extraction-tracing-supersession)
- [7. Related work and duplicates](#7-related-work-and-duplicates)
- [8. Centrality and paths](#8-centrality-and-paths)
- [9. Memory model](#9-memory-model)
- [10. Agent loop and task board](#10-agent-loop-and-task-board)
- [11. Ingest and incremental sync](#11-ingest-and-incremental-sync)
- [12. Safety rails](#12-safety-rails)

---

## 1. Why a graph

The questions an engineering org asks itself are about relationships, not documents:

| Question | Shape |
|---|---|
| Who knows this code? | Person → PR → File, Person → Review → PR → File, Person → OWNS → Module |
| What breaks if this changes? | transitive reverse `IMPORTS` closure, then owners of every file in it |
| Why is it built this way? | Decision → thread → people, Decision → SUPERSEDES → older Decision |
| Have we seen this bug before? | Issue ~ Issue (text), then PR → CLOSES → Issue |

With vector retrieval over chunks, an agent gets a pile of similar-looking text and has to rebuild the joins in its context window. That fails quietly. It cannot see that the person who wrote the PR is not the person who reviewed it, or that the file it is reading about is imported by forty others. In a graph the join is the data model, and an agent tool can return the *path* along with the answer. TraceBrain is built around that: the LLM decides which question to ask, and FalkorDB computes the answer and the evidence.

One concrete case. "Who should review a change to these files?" is a single query over two relationships plus a time decay:

```cypher
MATCH (p:Person)-[r:REVIEWED]->(pr:PullRequest)-[:MODIFIES]->(f:File)
WHERE f.key IN $files
RETURN p, pr.key, coalesce(r.at, pr.created_at), collect(f.key)[..3]
```

## 2. Tenancy: one graph per org

`db.get_graph(tenant)` returns `select_graph(f"org_{tenant}")`. FalkorDB is a multigraph database, so each tenant gets a physically separate graph: separate indexes and no shared nodes, and a query in `org_acme` cannot reach `org_falkordb`. Tests rely on this. Each test creates `org_test_<uuid>` and deletes it afterwards.

The same file holds the connection policy. If `FALKORDB_URL` is set, the code uses `falkordb.FalkorDB.from_url`. Otherwise it uses embedded `falkordblite` at `data/falkordb.db`, so the project runs with no server.

Inside a tenant, memory is per user. That is enforced by query shape, not convention: every memory read and write starts from `(:User {key: $uk})`, and `$uk` comes from the request context. `registry.dispatch` overwrites any `user_id` the LLM passes with the context's value ("context wins: the LLM can't pick another user's memory"). Sessions are checked for ownership too:

```cypher
MATCH (u:User)-[:HAD]->(:Session {key: $sk}) RETURN u.key   -- must equal the caller, else "session belongs to another user"
```

## 3. The tool contract and traces

Every tool is `fn(graph, **args) -> {"data": ..., "trace": {"cypher": [...], "nodes": [...], "edges": [...]}}`. A small `Tracer` (`tools/common.py`) runs each parameterized query through `graph.ro_query` (read-only at the server), appends the normalized Cypher to `trace.cypher`, and collects nodes and edges as the tool works. `Tracer.add_path` turns a FalkorDB `Path` into keyed nodes and typed edges.

The registry (`tools/registry.py`) defines 11 tools once, with JSON schemas, and exposes them in three places:

1. as Anthropic tool definitions (`strict: true`, in a fixed order so the prompt cache stays warm),
2. over MCP stdio (`mcp_server.py`), where context arguments such as `user_id` become explicit parameters,
3. to the UI: after each call the agent loop emits a `subgraph` event with the trace's nodes, edges and a `highlight` list, and a `step` event with a one-line summary and the Cypher.

The evidence graph in the UI is the union of these traces, so what you see is exactly what the agent touched.

## 4. Expert scoring

`find_experts(target)` (`tools/experts.py`) answers "who knows X" with a score you can audit.

**Step 1: anchor.** A key is used as-is. Free text is resolved with `search_entities` twice, balanced: up to 4 code anchors (File/Module) and up to 4 discussion anchors (Issue/PR/Topic/Decision), each kept only if its search score is at least 0.3. Without the balancing, a common word would let issues crowd out files, or the reverse.

**Step 2: expand the scope.** Each anchor label has its own expansion: a Module brings in its files, an Issue brings in the PRs that `CLOSES` it and their files, a Topic brings in its issues and decisions, a Decision brings in what it `AFFECTS`. For example:

```cypher
MATCH (pr:PullRequest)-[:CLOSES]->(:Issue {key: $k})
OPTIONAL MATCH (pr)-[:MODIFIES]->(f:File)
RETURN pr.key, f.key LIMIT $lim
```

**Step 3: collect evidence** over six path types:

```cypher
-- modified: authored a PR that touches a file in scope
MATCH (p:Person)-[:AUTHORED]->(pr:PullRequest)-[:MODIFIES]->(f:File) WHERE f.key IN $files
RETURN p, pr.key, coalesce(pr.merged_at, pr.created_at), collect(f.key)[..3]

-- owns: CODEOWNERS on a file or its module
MATCH (p:Person)-[:OWNS]->(x) WHERE x.key IN $files OR x.key IN $modules RETURN p, x.key

-- commented (weighted by comment count, capped at 5)
MATCH (p:Person)-[c:COMMENTED_ON]->(i) WHERE i.key IN $items RETURN p, i.key, c.last_at, c.count

-- decided
MATCH (p:Person)-[m:MADE]->(d:Decision) WHERE d.key IN $decisions RETURN p, d.key, d.decided_at, m.role
```

**Step 4: score.**

```
score(person) = Σ weight[type] × 0.5^(age_days / 180)      (OWNS is not decayed)
weights: owns 5 · modified 3 · decided 3 · reviewed 2 · authored 2 · commented 0.5 per comment (max 5)
```

Bots (`[bot]`, `-bot`, `bot=true`) are skipped. The result includes the per-type breakdown, the evidence count and the top 5 evidence paths, e.g. `[person:x, AUTHORED, pr:…#12, MODIFIES, file:…]`. Those paths become trace edges, so the UI draws why each person ranked where they did.

## 5. Blast radius

`blast_radius(key, depth=3)` (`tools/impact.py`) takes a File, Module (all its files) or PullRequest (the files it modifies) as seeds and runs a breadth-first search over reverse imports. There is one query per level, so each file's distance and the edge that reached it are exact:

```cypher
MATCH (dep:File)-[:IMPORTS]->(f:File) WHERE f.key IN $frontier RETURN dep.key, f.key
```

The search stops at `depth` (max 6) or once it has seen 3 × `limit` files. Then, over the seeds plus every affected file:

```cypher
MATCH (f:File)-[:IN_MODULE]->(m:Module) WHERE f.key IN $files RETURN f.key, m
MATCH (p:Person)-[:OWNS]->(x) WHERE x.key IN $files OR x.key IN $modules RETURN p, x.key
MATCH (p:Person)-[:AUTHORED]->(pr:PullRequest)-[:MODIFIES]->(f:File) WHERE f.key IN $files
  RETURN p, pr.key, coalesce(pr.merged_at, pr.created_at) AS at, f.key ORDER BY at DESC LIMIT 200
MATCH (d:Decision)-[:AFFECTS]->(x) WHERE x.key IN $files OR x.key IN $modules RETURN d, x.key LIMIT 20
```

The output: affected files with `distance` and `via`, modules, code owners, up to 8 recent human modifiers (with their PRs), and the decisions that constrain the area. So "what breaks" also tells you who to ask and which past decision you might be about to undo.

## 6. Decisions: extraction, tracing, supersession

**Extraction** (`ingest/extract.py`) runs only when an LLM key is configured. It picks the 40 most-discussed threads that changed since they were last read:

```cypher
MATCH (n) WHERE (n:Issue OR n:PullRequest) AND n.repo = $repo
  AND (n.extracted_ts IS NULL OR n.extracted_ts < n.updated_ts)
OPTIONAL MATCH (p:Person)-[c:COMMENTED_ON]->(n)
WITH n, sum(coalesce(c.count, 0)) AS disc WHERE disc > 0
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(n)
RETURN n.key, labels(n)[0], n.number, n.title, n.body, n.state, n.url,
       coalesce(n.merged_at, n.closed_at, n.updated_at), a.login, disc
ORDER BY disc DESC LIMIT $top
```

For each thread it fetches the issue comments, PR reviews and review comments. To fit the context budget it keeps the first 2 comments, the last 3 and the 5 with the most reactions or text, with bots dropped. The model must answer through a `record_decisions` tool: `summary, rationale, status, quote, proposer_login, approver_logins, affected_paths, topics`. Each decision becomes:

- `Decision {key: "decision:" + sha1(source_key + summary)[:12], …}`, a deterministic key, so re-extraction merges instead of duplicating
- `DECIDED_IN` → the thread, `MADE {role: proposer|approver}` → people (only logins that actually took part in the thread are kept), `AFFECTS` → files/modules, `ABOUT` → `Topic`

**Decision supersession.** After extraction, decisions are grouped by topic, and the model is asked which newer decision replaces an older one in the same group:

```cypher
MATCH (d:Decision)-[:ABOUT]->(t:Topic) WHERE d.repo = $repo
WITH t, collect({key: d.key, summary: d.summary, decided_at: d.decided_at}) AS ds
WHERE size(ds) > 1 RETURN t.name, ds LIMIT 25
```

Pairs are accepted only if both keys exist and `newer.decided_at >= older.decided_at`. Accepted pairs become `(newer)-[:SUPERSEDES]->(older)` and the older decision's status is set to `superseded`.

**Tracing** (`tools/decisions.py`) ranks full-text matches with a status boost (accepted +0.2, superseded/rejected −0.15). For each match it follows the source, the people, what it affects and the chain in both directions:

```cypher
MATCH p=(:Decision {key: $k})-[:SUPERSEDES*1..5]->(:Decision) RETURN p   -- what this replaced
MATCH p=(:Decision)-[:SUPERSEDES*1..5]->(:Decision {key: $k}) RETURN p   -- what replaced this
```

If no decision matches, it falls back to `find_related_work`, so the agent can still cite the closest threads and says it found no extracted decision.

## 7. Related work and duplicates

`find_related_work(text)` (`tools/search.py`) runs FalkorDB full-text search (RediSearch syntax, OR of terms, prefix match on terms of 4+ characters) over Issue and PR title+body:

```cypher
CALL db.idx.fulltext.queryNodes($label, $q) YIELD node, score
RETURN node, score ORDER BY score DESC LIMIT $lim
```

and re-ranks with `similarity = 0.5 × normalized_fulltext + 0.5 × min(1, 3 × jaccard(terms))`. The Jaccard term is scaled because overlap between long documents is always small. Each candidate comes back with its shared terms and the PRs that resolved it:

```cypher
MATCH (pr:PullRequest)-[:CLOSES]->(i:Issue) WHERE i.key IN $keys OR pr.key IN $keys RETURN pr.key, i.key
```

A likely duplicate that already has a closing PR is the most useful thing triage can return.

`search_entities` blends the per-label full-text score (40%) with query-term coverage of the title (60%), and down-weights generated assets (HTML/JSON/images) to 0.6. Where a label has no full-text index it falls back to a `CONTAINS` scan.

## 8. Centrality and paths

`centrality(label, algo)` (`tools/algos.py`) calls FalkorDB's built-in algorithms on a projection that makes sense for the label:

```cypher
-- most depended-upon files: PageRank over the import graph
CALL algo.pageRank($label, $rel) YIELD node, score RETURN node, score ORDER BY score DESC LIMIT $lim
-- with label = 'File', rel = 'IMPORTS'  (Issue/PR use 'REFERENCES')

CALL algo.betweenness({nodeLabels: [$label], relationshipTypes: [$rel]}) YIELD node, score
RETURN node, score ORDER BY score DESC LIMIT $lim
```

People are mostly edge *sources*, which makes PageRank on a Person projection meaningless. For them the tool ranks over the full graph and filters by label. If an algorithm is unavailable or returns flat scores, it falls back to activity degree (`AUTHORED|REVIEWED|COMMENTED_ON|OWNS|MADE`, bots excluded) and reports which method it actually used.

`shortest_path(a, b)` works around FalkorDB's `shortestPath()` only supporting directed traversal. It tries both directions, then confirms the result is minimal with bounded undirected iterative deepening:

```cypher
MATCH (x {key: $src}), (y {key: $dst}) WITH shortestPath((x)-[*..6]->(y)) AS p WHERE p IS NOT NULL RETURN p
MATCH p=(x {key: $a})-[*1]-(y {key: $b}) RETURN p LIMIT 1     -- then *2, *3 … up to the best length − 1
```

## 9. Memory model

Memory lives in the tenant graph next to the org data. A memory can then point *at* the code it is about, and a later question about that code can pull it back.

```
(User)-[:HAD]->(Session)-[:CONTAINS]->(Episode {question, answer, agent, created_at})
(Episode)-[:NEXT]->(Episode)          per-user timeline across sessions
(Episode)-[:CITED]->(org node)        what the answer relied on
(User)-[:KNOWS]->(Fact {text, kind: semantic|preference|procedural, confidence, valid})
(Fact)-[:ABOUT]->(org node)  (Fact)-[:LEARNED_FROM]->(Episode)  (Fact)-[:SUPERSEDES]->(Fact)
```

- **Episodic.** The Historian writes one `Episode` per turn, links it with `NEXT` to the user's previous episode (in any session), and adds `CITED` edges to every `[[key]]` in the answer.
- **Semantic, preference, procedural.** The Historian runs a second, small LLM call with a `record_memory` tool. It sees the question, the answer, the cited keys and the user's current valid facts, and records only what the user stated or strongly implied: preferences ("prefers bullet lists"), what they work on ("works on the vector index in falkordb-py"), procedures. Each fact can be `ABOUT` graph keys.
- **Supersession.** If a new fact contradicts an existing one, the model lists the old key in `supersedes`, and `memory.remember` runs:

  ```cypher
  MATCH (:User {key: $uk})-[:KNOWS]->(o:Fact {key: $ok}), (f:Fact {key: $fk})
  SET o.valid = false, o.updated_at = $now
  MERGE (f)-[:SUPERSEDES]->(o) RETURN o.key
  ```

  Nothing is deleted. The old fact stays as history (the Memory tab shows it with a "superseded" tag), and recall filters on `valid = true`. A fact identical to an existing valid one (case-insensitive) is refreshed rather than duplicated. Users can delete a fact themselves (`DELETE /api/memory/{user}/facts/{key}`).

- **Recall** runs *before* routing, in the Coordinator. It combines four signals, and each recalled fact carries a `why`:
  1. full-text match on the user's valid facts: `CALL db.idx.fulltext.queryNodes('Fact', $q) YIELD node, score MATCH (:User {key: $uk})-[:KNOWS]->(node) WHERE node.valid = true …`
  2. facts `ABOUT` entities the question mentions. This is where the graph does the work: the question is run through `search_entities`, and any fact attached to a matched node is pulled in even when it shares no words with the question:
     ```cypher
     MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact)-[:ABOUT]->(x)
     WHERE f.valid = true AND x.key IN $keys RETURN f, x.key
     ```
  3. all preference facts (they apply to every answer)
  4. the last 3 episodes

  The result goes into the agent prompt as a `<memory>` block, and the UI's Memory tab marks which facts were recalled for this turn.

## 10. Agent loop and task board

**Loop** (`agents/llm.py`): a manual streaming tool-use loop on the Anthropic SDK. Model `TRACEBRAIN_MODEL` (default `claude-opus-5-5`), `thinking: {type: "adaptive"}`, `output_config: {effort: "medium"}`, `cache_control` on the system prompt and the last tool (which caches system + all tools), and `tool_choice: auto`. Text deltas stream to the UI as `token` events. When the model returns several `tool_use` blocks, they all run and all results go back in one user message, so independent lookups happen in parallel within a turn. Tool errors go back as `is_error` results so the model can recover. One turn before the budget runs out (10 iterations), the loop tells the model to stop calling tools and answer. If an Anthropic-compatible endpoint (e.g. OpenRouter) rejects an optional feature with a 400, the loop drops that feature for the process and retries.

**Agents:**

| Agent | Tools | Output |
|---|---|---|
| Coordinator | `recall` (direct) | routes by regex: a GitHub issue URL or a message starting with "triage" goes to Triage, everything else to Navigator |
| Navigator | search, neighbors, experts, decisions, blast radius, related work, shortest path, centrality, read-only Cypher, recall | Markdown answer with inline `[[key]]` citations, resolved to nodes for the UI |
| Triage | search, related work, experts, blast radius, neighbors, decisions, shortest path + `submit_triage_plan` (strict schema) | plan: summary, related files, modules, blast radius, duplicates, assignee, exactly 2 reviewers, labels, priority, draft comment |
| Historian | `record_memory` | Episode + Facts (+ supersession) |

If Triage never calls `submit_triage_plan`, it gets one nudge. After that, `fallback_plan` builds a plan deterministically from the same tools, so the user always gets a plan.

**Task board = graph state** (`agents/board.py`). Each chat turn creates a task tree:

```cypher
MERGE (a:Agent {key: $ak}) ON CREATE SET a.title = $agent, a.name = $agent
CREATE (a)-[:CLAIMED]->(t:Task {key: $tk, kind: $kind, status: $status, agent: $agent, input: $input, ...})

MATCH (p:Task {key: $p}), (t:Task {key: $tk}) MERGE (p)-[:HANDOFF_TO]->(t)
MATCH (t:Task {key: $tk}) UNWIND $keys AS k MATCH (x {key: k}) MERGE (t)-[:TOUCHED]->(x)
```

`chat (coordinator) → navigate | triage → remember (historian)`. Status moves `queued → running → done | failed` and outputs (answer, citations, tool calls, plan) are stored on the node. The Agents tab polls `GET /api/tasks`, which is just:

```cypher
MATCH (t:Task) OPTIONAL MATCH (t)-[:HANDOFF_TO]->(c:Task) OPTIONAL MATCH (t)-[:FOR]->(s:Session)
WITH t, collect(DISTINCT c.key) AS handoffs, head(collect(s.key)) AS session
RETURN t, handoffs, session ORDER BY t.created_ts DESC LIMIT $lim
```

Because handoffs and `TOUCHED` sets are edges, you can also ask the graph which tasks touched a given file.

## 11. Ingest and incremental sync

`tracebrain-ingest owner/name …` → `ingest/sync.py:aingest_repo`:

1. `ensure_schema`: a range index on `key` for all 16 labels, plus full-text indexes. Idempotent.
2. `since = SyncState.last_synced_at` unless `--full`.
3. **Issues/PRs** (`github.py`): `GET /repos/{r}/issues?state=all&sort=updated&since=…` (up to `--max-items`, default 300), then per item, concurrently: comments, and for PRs the files, reviews and review comments. Reviews collapse to one `REVIEWED {state, at, count}` per reviewer, using the last decisive state. Comments aggregate into `COMMENTED_ON {count, last_at}`. `fixes/closes/resolves #n` becomes `CLOSES`, and other same-repo `#n` mentions or issue URLs become `REFERENCES`. Non-generic labels also become `Topic`s. Person names are filled from `/users/{login}` once.
4. **Code** (`code.py`): the default-branch tree becomes `File` + `Module` nodes (top-level dir, or first subpackage under a `src/` layout). Up to 400 `.py` files are fetched and parsed with `ast` (with a regex fallback). Absolute, relative and `from pkg import submodule` imports are resolved to repo paths, giving `IMPORTS`.
5. **CODEOWNERS** (`.github/`, root, or `docs/`): a rule covering a whole top-level dir owns the `Module`. Narrower rules own matching `File`s. Teams and emails are skipped.
6. **Decisions** (section 6), only for threads whose `updated_ts` is newer than their `extracted_ts`.
7. `SyncState {last_synced_at}` is written with the time the sync *started*, so items updated during the run are picked up next time.

Every write is a batched `UNWIND … MERGE (n:Label {key: r.key}) SET n += r.props` (500 rows per batch). Edges `MATCH` both endpoints by key and `MERGE` the relationship. Re-running is idempotent, and an incremental sync touches only what changed. The API exposes the same pipeline as a background job: `POST /api/ingest {repo, since?}`.

The client respects `retry-after` and `x-ratelimit-reset`, retries 5xx with backoff, follows `Link: next` pagination, and caps concurrency at 8.

## 12. Safety rails

- **Read-only Cypher for the model.** `run_cypher` strips string literals, rejects `CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD CSV|FOREACH` and `CALL db.*|dbms.*|algo.*write`, and then executes with `GRAPH.RO_QUERY`, so the server enforces it as well. Results are capped at 200 rows, with a 10 s timeout. All other tools use `ro_query` with an 8 s default timeout.
- **No GitHub writes by default.** Ingest and `fetch_issue` only read. The draft comment is posted only through `POST /api/triage/{task}/post`, which returns 403 unless `TRACEBRAIN_ALLOW_GITHUB_WRITE=1` and 409 if the comment was already posted.
- **Memory isolation.** Covered in section 2. The LLM never controls `user_id`.
- **Bounded work.** Depths, limits and the agent's iteration count are all clamped, and tool results sent to the model are truncated at 14k characters.
