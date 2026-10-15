# Graph Hacks submission: copy/paste text

## Project name

TraceBrain

## Tagline (≤80 chars)

A company brain on FalkorDB: agents that answer, triage and show their path.

<!-- 76 characters -->

## Description (150 words)

TraceBrain turns a GitHub org into a FalkorDB graph: people, files, Python imports, issues, PRs, reviews, CODEOWNERS, and engineering decisions extracted by an LLM from discussion threads. A team of agents works on that graph. The Navigator answers "who knows X", "why was Y decided" and "what breaks if Z changes" with graph tools: expert scoring over authorship and review paths, reverse-import blast radius, decision tracing with supersession chains, PageRank. The Triage agent reads a new issue and proposes an assignee, two reviewers, likely duplicates, labels and a draft comment, without posting anything. The Historian stores each user's episodes and facts in the same graph and supersedes facts that become outdated, so memory carries across sessions. Every tool returns the Cypher it ran and the subgraph it touched. The UI draws that path next to the answer, and the same tools are available to Claude over MCP.

## How FalkorDB is used

FalkorDB is the only datastore. It holds the org knowledge graph, agent memory and the agents' task board.

- **One graph per tenant** (`org_<tenant>`, multigraph isolation). Embedded through falkordblite for local runs, or a FalkorDB server via `FALKORDB_URL`.
- **Graph queries as agent tools.** 11 tools run parameterized Cypher through `GRAPH.RO_QUERY`: multi-hop expert scoring (`Person-AUTHORED->PR-MODIFIES->File`, `REVIEWED`, `OWNS`, `MADE->Decision`), breadth-first reverse-`IMPORTS` blast radius, `SUPERSEDES*1..5` decision chains, and bidirectional `shortestPath` with undirected iterative deepening.
- **Built-in algorithms:** `algo.pageRank` on File/IMPORTS and Issue/REFERENCES projections, and `algo.betweenness`.
- **Full-text indexes** (`db.idx.fulltext`) on issues, PRs, decisions, files, people and facts power entity resolution, duplicate detection and memory recall. Range indexes on `key` back idempotent `MERGE` ingest.
- **Memory and coordination as graph state:** `User→Session→Episode`, `Fact` with `ABOUT`, `LEARNED_FROM` and `SUPERSEDES`, and `Agent-CLAIMED->Task-HANDOFF_TO->Task` with `TOUCHED` edges.
- **Explainability:** each tool returns its Cypher plus the nodes and edges it touched, which the UI renders as the evidence graph.

## Tracks entered

**Track 1: Agents That Act on Connected Data.** The agents' main tools are multi-hop graph queries and FalkorDB algorithms (PageRank, betweenness, shortest path), and the same registry is served over MCP for tool selection by Claude. The Triage agent acts on what the graph shows: it picks an owner, reviewers and labels from evidence paths and drafts the GitHub comment. Posting it is a separate action that is off by default. Every answer is explainable through its trace and inline node citations.

**Track 2: Agent Memory and Coordination.** Episodic memory (sessions, episodes, a `NEXT` timeline, `CITED` edges), plus semantic, preference and procedural facts linked to the org nodes they are about. Contradicted facts are superseded instead of deleted. Recall uses the graph to pull in facts about the entities a question mentions. Agents coordinate through a task tree stored in the graph (`HANDOFF_TO`, `CLAIMED`, `TOUCHED`), which the UI shows as the agent board. Each tenant has its own graph, and memory is scoped per user by query shape.

**Track 3: Company Brain.** Code, issues, PRs, reviews, CODEOWNERS and LLM-extracted decisions live in one graph. TraceBrain answers who owns or knows what (with a score breakdown) and traces decisions to the source thread, the people and a supporting quote. It stays current through incremental sync with `SyncState` and idempotent `MERGE`. Answers cite their sources inline.

## Tech stack

FalkorDB (falkordblite / FalkorDB server) · Python · FastAPI · Anthropic Python SDK with Claude Opus 5.5 (adaptive thinking, prompt caching, strict tool use) · MCP Python SDK · httpx · React 19 + TypeScript + Vite · react-force-graph-2d

## Links

- Repository: https://github.com/The-MathKing/tracebrain
- Live demo: `TODO`
- Demo video: `TODO`
- Blog post: `TODO`
