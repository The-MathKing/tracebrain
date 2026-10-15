# TraceBrain demo QA

Run 2026-10-15 against the live graph `org_falkordb` with real Claude (`anthropic/claude-opus-5.5` via OpenRouter).
User `demo-final` / `demo-final2`. Prompts 1-5 run in one session; prompt 6 runs in a **new session** for the same user.
At the time of the run, GraphRAG-SDK and falkordb-py were ingested (about 195 Decisions). QueryWeaver was still ingesting.

Latency is wall-clock time from POST to the `done` event, including the historian. "First token" is when the answer starts streaming.
Tokens are the agent loop's own usage (from the new `usage` field on the `answer` event); the historian adds about 2-3k input / 300 output per turn.
Each answer was checked against the graph with Cypher. Every `[[key]]` citation resolved to a real node in all final runs.

| # | Prompt | Track | Tools | Latency (first token) | Tokens in/out (cached) | Cites |
|---|---|---|---|---|---|---|
| 1 | Who knows the most about the GraphRAG-SDK api/main.py facade, and who should review changes to it? | T3 experts | search_entities, find_experts, blast_radius | 21s (18s) | 6.5k/1.3k (11k) | 22/22 |
| 2 | What breaks if graphrag_sdk core/models.py changes? | T1 blast radius | blast_radius | 15s (12s) | 4.6k/1.1k (7k) | 17/17 |
| 3 | Why does falkordb-py ship its own embedded FalkorDB server code instead of depending on falkordblite? | T3 decisions | trace_decision, get_neighbors, run_cypher | 22s (19s) | 8k/1k (15k) | 8/8 |
| 4 | Triage: https://github.com/FalkorDB/GraphRAG-SDK/issues/330 | T1 triage | fetch issue, find_related_work, search_entities, find_experts, blast_radius, submit_triage_plan | 26s | 16k/1.9k (6k) | 9/9 |
| 5 | How is mikemikimike connected to the GraphRAG-SDK ontology store (storage/ontology_store.py)? | multi-hop path | shortest_path, get_neighbors | 16s (13s) | 4.8k/0.9k (11k) | 6/6 |
| 6 | (new session) What was I working on last time? | T2 memory | none (answers from recalled episodes and facts) | 12.5s (4.5s) | 1.7k/0.9k (4k) | 11/11 |

## Recommended order for the 3-minute video

1. **#1 experts** (about 25s). Opens on an empty graph that fills with people, PRs and the file. The highlighted path is person → PR → file. The Memory tab then shows the historian's first fact ("Is investigating who should review changes to … api/main.py").
2. **#2 blast radius** (about 20s). The biggest graph burst on screen (140 nodes), with real numbers: 125 dependents, 88 of them direct.
3. **#4 triage #330** (about 30s). Show the Triage tab: assignee, reviewers, blast radius, the PR #331 match and the draft comment. Point out that nothing is posted to GitHub.
4. **#3 why** (about 25s). The decision chain with a real SUPERSEDES story (PR #130 → PR #164) and a verbatim maintainer quote.
5. **#5 path** (about 15s). A 3-hop path highlighted in the graph, connecting back to the triage.
6. Click **New session**, then **#6 memory** (about 15s). It recalls all five previous questions with their findings. This is the best closing shot.

If time is short, drop #5. #2 and #4 overlap less than it looks: one is a file question, the other is an issue workflow.
All six prompts are now the example chips in `web/src/components/ChatPane.tsx`, in a slightly different order. Index 3 stays the triage chip because the Triage tab's "Try" button uses it.

## Trimmed transcripts

### 1. Experts (21s)
Steps: `search_entities(GraphRAG-SDK api/main.py)` → `find_experts(file:…/api/main.py)` + `blast_radius(file:…/api/main.py)` in parallel.
> **[[person:galshubeli]] knows the facade best, with [[person:Naseem77]] a close second. Have both review changes to [[file:…/api/main.py]]: Gal as primary, Naseem as co-reviewer.**
> - galshubeli (score 60.8) has both changed the file and reviewed changes to it: #337, #336, #328, #278; most recent review #275 (Oct 12).
> - Naseem77 (score 42.5) changed it most recently: #341 (Oct 13), #333, #327.
> - Occasional contributors: drr00t (3 PRs in May), mikemikimike (#331). The graph has no CODEOWNERS entry, so the ranking is based only on who changed and reviewed it.
> - What a change can break: 42 files within 2 hops (11 direct), 29 of them tests. Consider gkorland for changes to the public API.

Checks: the scores and PRs match `find_experts`. The 11 direct and 42 total dependents match `blast_radius`.

### 2. Blast radius (15s)
Steps: `blast_radius(file:…/core/models.py)` only. Before the fix it took 5 calls and 43s (see below).
> **Pretty much the whole SDK depends on it: 125 files (88 direct, 36 at 2 hops, 1 at 3 hops) in 8 modules.**
> Tests 42, examples 11, ingestion 24 (chunking 6, loaders 5, extraction 4, resolution 3), providers 4, retrieval 5, storage 4, discovery 4. The public surface (`__init__.py`, `api/main.py`) imports it directly.
> Reviewers: galshubeli (changed models.py itself in #337/#336/#335/#328), Naseem77 (most recent modifier of affected files), mikemikimike (#331).

Checks: 88 direct importers confirmed with Cypher. The 125 total is also correct: an explicit 2-hop Cypher pattern finds the 6 files that FalkorDB's `[:IMPORTS*1..3]` match misses.

### 3. Why was it decided (22s)
Steps: `trace_decision(...)` returns 4 decisions plus the linked SUPERSEDES chain, then `get_neighbors`, then `run_cypher`.
> **The maintainer wanted embedded mode to live inside falkordb-py itself, not in an outside package. That decision has since been superseded: the current proposal keeps the server-management code in a `falkordb.lite` package but takes the binaries from a `falkordb-bin` dependency.**
> - [[decision:ceb79cff715f]], made by [[person:gkorland]] in [[pr:FalkorDB/falkordb-py#130]]: copy the redislite/falkordblite process-management and config code into `falkordb/embedded`. Quote: *"the code should not depend on redislite but should copy the functionality to this project."*
> - Two related decisions from the same PR, both also superseded: decision:3ffc6deec19a (the `embedded` extra) and decision:4b4c1d022f82 (a `setup.py` step that downloads and builds Redis and FalkorDB).
> - Current direction: [[decision:d297828b7bd3]] in [[pr:FalkorDB/falkordb-py#164]] (`lite` extra on `falkordb-bin`, `embedded=True`, connection pool). Plus decision:8389f5be0b43 (no `atexit` cleanup).
> - The graph records only the maintainer's preference as the reason.

Checks: DECIDED_IN, MADE and SUPERSEDES edges confirmed (d297 supersedes ceb79, 3ffc6 and 4b4c). The quote is verbatim from the Decision node.

### 4. Triage of GraphRAG-SDK#330 (26s, plan via `submit_triage_plan`)
Plan: assignee **mikemikimike** ("author of open PR #331, which CLOSES #330 and modifies api/main.py and test_facade.py"). Reviewers **galshubeli** and **Naseem77**, both top experts on api/main.py. Labels `bug, python, Impact S` (all existing repo labels). Priority medium. 6 blast-radius files. Related: pr#331. No duplicates.
Draft comment (not posted):
> Thanks for the detailed write-up and repro.
> The cause is in `graphrag_sdk/src/graphrag_sdk/api/main.py`. `_ensure_ontology_initialized()` and `_validate_graph_config()` set their flags only after several awaits. Every concurrent cold request gets past the guard and repeats the ontology load/register and the embedder probe.
> Open PR #331 ("serialize concurrent GraphRAG lazy initialization") already covers this. @mikemikimike, as its author, is best placed to finish it. @galshubeli and @Naseem77 have been working on `api/main.py` recently and can review.
> What to test: `asyncio.gather` of N `retrieve()` calls → exactly one `register()` and one `aembed_query` probe; `get_ontology()` still re-reads after init; the existing facade and ontology-evolve tests still pass.

Checks: confirmed that PR #331 CLOSES issue #330 and that mikemikimike AUTHORED it.

### 5. Multi-hop path (16s)
> **[[person:mikemikimike]] is connected to the ontology store indirectly, through 3 hops. Their PR doesn't edit [[file:…/storage/ontology_store.py]] itself.**
> 1. mikemikimike AUTHORED [[pr:FalkorDB/GraphRAG-SDK#331]] → 2. which MODIFIES [[file:…/api/main.py]] → 3. which IMPORTS ontology_store.py.
> #331 CLOSES [[issue:FalkorDB/GraphRAG-SDK#330]]. Reviewers on the PR: galshubeli and coderabbitai[bot]. Treat mikemikimike as the expert on the init path in `main.py`, not on the store.

Checks: all three edges and both REVIEWED edges confirmed.

### 6. Memory follow-up in a new session (12.5s, no graph tools)
> **Last session (2026-10-15) you were mostly triaging the GraphRAG-SDK concurrency bug in [[issue:FalkorDB/GraphRAG-SDK#330]] and looking at the code around it. Separately, you also asked a question about falkordb-py.**
> It then lists the triage of #330, the mikemikimike → ontology store path, the reviewers for the facade (galshubeli, Naseem77), the blast radius of models.py (125 files, 88 direct) and the embedded-server decision.
> Next step: check whether PR #331 has been reviewed or merged.

The recall correctly covers all five prior turns. Each turn also wrote one semantic fact that shows in the Memory tab.

## Before and after

| Prompt | Before | After |
|---|---|---|
| Blast radius | 43s, 5 tool calls. The 27k-char result was truncated mid-JSON, so the model re-counted with 4 `run_cypher` calls. 600+ word answer, and `affected_count` was capped at 100. | 15s, 1 call. Compact result with complete counts by distance and directory. About 250 words. |
| Triage | 34s. The GitHub fetch got a 403 (the token's rate limit was used up by ingest), so the plan's `issue` was null and the model worked without the issue body. Labels were invented. Emoji in the draft. | 23-26s at effort `low`. Anonymous GitHub retry, then a fallback to the ingested Issue node. Repo labels are passed in. Draft under 150 words, no emoji. |
| Experts | 24s, good but long (table plus advice paragraphs). | 21s, concise bullets. |
| Why | Not testable at first (0 Decisions). The first runs missed the key decision (ceb79) or said wrongly that it had no source PR. | Wider rerank pool, a merged exact-term full-text pool, and linked/sibling SUPERSEDES details. The key decision and its source now appear for 3 phrasings. |
| Path | `shortest_path` could route through memory nodes (person → Episode → PR) or Repo hubs. | Paths and neighbor expansion stay inside org labels and rels. Repo/Label hubs are used only as a fallback. Each step states its real edge direction. |
| Memory | The historian wrote 0 facts for focused questions. Recall for "last time" depended on text matching. | One "Is investigating …" fact per focused turn (historian at effort `low`). Self-referential questions get 6 episodes plus recent facts. |

## Known weaknesses

- **Decision search is still lexical.** It is better than before, but a "why" question phrased very differently from the decision summary can miss it. The navigator usually reformulates the query, which helps. Prompt #3 is tested with 3 phrasings, so use it as written.
- **Data quirk:** Dependabot release-notes PRs (e.g. pr#188) carry REFERENCES edges to unrelated issues, so some paths look odd (gkorland → #188 → #330). Avoid path prompts between gkorland and #330.
- **Answers carry memory over within a session** ("as you found earlier…"). That is good for T2, but it means answers change slightly with run order. Run the demo for a fresh `user_id`.
- **Triage shows no streaming tokens.** The plan arrives as one structured block about 20s in, while the step timeline keeps moving.
- **The "why" answer sometimes adds 1-2 extra tool calls** (get_neighbors/run_cypher) to re-verify the chain. That takes it from about 16s to about 22s. Acceptable.
- **The historian does not record a fact for triage turns** when the model treats them as one-off tasks. The episode is still saved, so #6 still works.
- **QueryWeaver was not ingested yet** at test time, so no cross-repo prompt involving it was verified. gkorland spans GraphRAG-SDK and falkordb-py.
- **FalkorDB `[:R*1..n]` var-length matches undercount** compared with explicit hop patterns here (119 vs 125). The tools use iterative BFS and are correct. Ad-hoc `run_cypher` counts may differ.
