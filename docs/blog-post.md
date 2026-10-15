---
title: "Your agent doesn't need more context, it needs connected context"
subtitle: "Building TraceBrain, a company brain for engineering teams, on FalkorDB"
author: Aryan Padarthi
tags: [falkordb, graphs, agents, llm, mcp]
canonical_url: TODO
---

# Your agent doesn't need more context, it needs connected context

*Building TraceBrain for Graph Hacks: a FalkorDB graph of a GitHub org, a few agents that query it, and a UI that shows the path behind every answer.*

![TraceBrain](img/hero.png)

## The question that started it

"Who should review this change?" sounds like a simple question. To answer it well you need to know who wrote the file, who reviewed the last few PRs that touched it, whether anyone owns it in CODEOWNERS, how recent all of that is, and which other files import it. None of that is in any single document. It sits in the connections between people, PRs, files and imports.

That's where I think most "chat with your codebase" tools go wrong. They chunk issues and files, embed the chunks, and hand the model the top 20 that look similar. The model then has to reconstruct the joins in its head: is the person who opened this PR the same one who reviewed that one? Does this file import that one? Usually it can't, so it guesses, and the guess reads as confidently as a real answer.

So for Graph Hacks I built **TraceBrain**. It puts a GitHub org into FalkorDB and gives the agents graph tools instead of a bigger context window. Every tool returns the Cypher it ran and the nodes and edges it touched, and the UI draws that next to the answer.

## What's in the graph

The demo tenant is three FalkorDB repos: GraphRAG-SDK, falkordb-py and QueryWeaver. Ingest pulls issues, PRs, reviews, comments, the files each PR modifies, the default-branch tree, Python imports (parsed with `ast` and resolved to repo files), and CODEOWNERS. It then asks an LLM to pull *decisions* out of the 40 most-discussed threads: what was chosen, why, who proposed and approved it, a supporting quote, and the files it affects.

After ingesting all three repos, the graph has 93 people, 719 pull requests, 181 issues, 1,806 files in 54 modules, and 528 extracted decisions, connected by about 17,000 edges. Extraction cost about $4.60 in model calls for the whole org. The node counts matter less than the edges:

```
(Person)-[:AUTHORED]->(PullRequest)-[:MODIFIES]->(File)-[:IN_MODULE]->(Module)
(Person)-[:REVIEWED {state, at}]->(PullRequest)      (File)-[:IMPORTS]->(File)
(Decision)-[:DECIDED_IN]->(PullRequest)              (Decision)-[:SUPERSEDES]->(Decision)
```

Agent memory and the agents' task board live in the same graph, which turned out to be the most useful design choice in the project. More on that below.

## One real answer, end to end

Here is a real exchange from testing, lightly trimmed. The question:

> **Who knows the most about the GraphRAG-SDK `api/main.py` facade, and who should review changes to it?**

The Navigator agent made exactly one tool call: `find_experts` on the file's key. The tool scopes the question to that file and its module, then collects evidence over six kinds of path. Two of them:

```cypher
MATCH (p:Person)-[:AUTHORED]->(pr:PullRequest)-[:MODIFIES]->(f:File)
WHERE f.key IN $files
RETURN p, pr.key, coalesce(pr.merged_at, pr.created_at), collect(f.key)[..3]

MATCH (p:Person)-[r:REVIEWED]->(pr:PullRequest)-[:MODIFIES]->(f:File)
WHERE f.key IN $files
RETURN p, pr.key, coalesce(r.at, pr.created_at), collect(f.key)[..3]
```

The others are CODEOWNERS ownership, authoring or commenting on related issues, and taking part in a decision. Each piece of evidence gets a weight (owns 5, modified 3, decided 3, reviewed 2, authored 2, 0.5 per comment) and a recency decay with a 180-day half-life. The tool returns each person's total, the breakdown by evidence type, and their top evidence paths as lists like `[person, AUTHORED, pr, MODIFIES, file]`.

The answer that came back (abridged):

> **galshubeli knows `api/main.py` best and should be the main reviewer. Naseem77 is a close second.** The graph has no CODEOWNERS entry for this file, so this ranking comes only from PR activity.
> - **galshubeli** (score 60.8): the most modifications, and by far the strongest reviewer (review score 24.1). Recent PRs: #337 (Oct 8), #336, #328.
> - **Naseem77** (score 42.5): almost as many modifications, and the most recent one (#341, Oct 13). Fewer reviews (6.6).
> - **drr00t** (score 5.0): three changes in May. Older knowledge, background only.
> - **mikemikimike** (score 2.6): one change, #331, the lazy-init lock fix for issue #330 that you triaged.

Three things in that answer came from the graph and not the model.

1. **The numbers are auditable.** 60.8 is a sum over specific PRs, and each person and PR is cited as a node key that the UI turns into a clickable chip. Click galshubeli and the evidence graph highlights the AUTHORED → MODIFIES and REVIEWED → MODIFIES paths that produced the score.
2. **It says what's missing.** No `OWNS` edge exists, so it says so, instead of presenting the top committer as the owner.
3. **"That you triaged."** This user had triaged issue #330 a few minutes earlier. The Coordinator recalled that episode from the graph before routing the question, and the agent connected it to PR #331 without being asked.

Point 3 is the one I care about most, so here's how memory works.

## Memory that points at things

Each chat turn ends with a Historian agent that writes an `Episode` node and asks a small model whether anything durable was learned about the user. Facts are nodes too, and they link to the org nodes they're about:

```
(User)-[:HAD]->(Session)-[:CONTAINS]->(Episode)-[:CITED]->(File|PR|Person ...)
(User)-[:KNOWS]->(Fact {kind: semantic|preference|procedural, valid})-[:ABOUT]->(File|PR ...)
```

Since facts hang off real nodes, recall doesn't need the question to share words with the memory. The Coordinator resolves the entities in the new question and pulls in any fact attached to them:

```cypher
MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact)-[:ABOUT]->(x)
WHERE f.valid = true AND x.key IN $keys
RETURN f, x.key
```

That query starts from the caller's own `User` node, and the `user_id` comes from the request context, never from the model. That's how two people sharing a tenant graph never see each other's memory.

When a fact stops being true ("I moved from falkordb-py to QueryWeaver"), the Historian lists the old fact's key as superseded, and nothing gets deleted:

```cypher
MATCH (:User {key: $uk})-[:KNOWS]->(o:Fact {key: $ok}), (f:Fact {key: $fk})
SET o.valid = false, o.updated_at = $now
MERGE (f)-[:SUPERSEDES]->(o)
```

Recall filters on `valid = true`, so the old fact doesn't steer future answers, but the history stays, and the Memory tab shows it struck through. Extracted engineering decisions follow the same pattern: `(newer:Decision)-[:SUPERSEDES]->(older:Decision)`. Asking "why do we do X?" therefore returns the current decision plus the chain it replaced:

```cypher
MATCH p=(:Decision {key: $k})-[:SUPERSEDES*1..5]->(:Decision) RETURN p
```

In a new session, the same tester asked "What was I working on last time?". The agent answered with **zero tool calls**: the reviewers for `api/main.py`, the lazy-init race in #330 with fix PR #331, and the 88 files that import `core/models.py` directly. It finished with "Want me to check whether PR #331 has been reviewed?". All of it came from the recalled episodes and facts.

## Blast radius is a traversal, not a search

"What breaks if `core/models.py` changes?" is a question similarity search can't answer. The answer has to be computed. `blast_radius` runs a breadth-first search over reverse imports, one hop per query, so every file gets an exact distance and the edge that reached it:

```cypher
MATCH (dep:File)-[:IMPORTS]->(f:File) WHERE f.key IN $frontier RETURN dep.key, f.key
```

For GraphRAG-SDK's `core/models.py` that came out as **125 dependent files: 88 direct, 36 at two hops, 1 at three hops**, including the public `__init__.py` and `api/main.py`. The agent grouped them by area (42 test files, about 24 in ingestion, 11 examples), named who most recently changed the file and its dependents, and pointed out that an open PR touching `api/main.py` might conflict. That last point only works because PRs, files and imports are in the same graph.

## The agents coordinate through the graph too

A chat turn is a small task tree: `chat (coordinator) → navigate | triage → remember (historian)`. Each task is a node, `CLAIMED` by an `Agent` node, linked by `HANDOFF_TO`, with `TOUCHED` edges to every node it used. The "agent board" in the UI is a `MATCH (t:Task)` query. There's no queue and no separate state store. A side effect: "which tasks touched this file?" is a one-hop query.

The Triage agent uses the same tools in a fixed order (related work, files, blast radius, experts). It submits a plan through a strict-schema tool: an assignee and exactly two reviewers, each with a reason from the evidence, duplicate candidates with the PR that resolved them, labels, priority and a draft GitHub comment. It never posts. Posting is a separate API call that returns 403 unless you set `TRACEBRAIN_ALLOW_GITHUB_WRITE=1`.

All 11 tools are also served over MCP, so Claude Desktop can call `centrality` and get FalkorDB's `algo.pageRank` over the import graph directly.

## What I learned

- **Return paths, not answers.** Every tool returns `{data, trace: {cypher, nodes, edges}}`. That one decision gave me the evidence graph, citations, debuggability and the explainability story for free. When an answer looks wrong, the first thing to read is the Cypher.
- **The agent should be able to say "the graph doesn't know."** Early on I asked what breaks if `falkordb/graph.py` changes before falkordb-py had finished ingesting. The agent checked with `search_entities`, `get_neighbors` and a couple of read-only Cypher queries, then replied that the repo node had no files linked yet and it couldn't answer. That's the right behaviour, and it only happens because the tool results are structured enough for the model to notice what's missing.
- **Pick the projection, not just the algorithm.** PageRank on `File` over `IMPORTS` finds the files everything depends on. PageRank on `Person` is meaningless, because people are almost only edge sources. The tool chooses a projection per label and falls back to degree when the scores come out flat, and it reports which method it actually used.
- **FalkorDB's `shortestPath()` is directed.** For "how are A and B connected" I try both directions, then confirm the result is minimal with bounded undirected iterative deepening (`MATCH p=(a)-[*d]-(b) RETURN p LIMIT 1`).
- **Balance your anchors.** A free-text question like "Cypher generation" can match files, issues, PRs and decisions. If one search ranks them together, one kind can crowd out the other, for example issues burying the code. The expert tool searches separately for up to four code anchors and four discussion anchors, and scores people over both.
- **Deterministic keys make ingest boring, which is good.** Every node has a string key, every write is `MERGE` on that key, and a `SyncState` node stores the last sync time. Re-running ingest is idempotent, and incremental sync only re-sends a thread to the LLM if it changed since the last extraction.

## What's next

- More sources: Slack threads and design docs as `Discussion` nodes, linked to the same people and files.
- Import graphs for TypeScript and Go, so blast radius works outside Python.
- Embeddings alongside full-text for duplicate detection. Right now similarity is full-text score plus Jaccard term overlap, which misses paraphrases.
- Real auth, so `user_id` comes from a login instead of the client.

The code is MIT-licensed: https://github.com/The-MathKing/tracebrain. There's a live demo at `TODO: live URL` and a 3-minute video at `TODO: video URL`. If you try it on your own org, I'd like to hear what the graph got wrong.

---

### Where to publish

1. **dev.to**: tag `#falkordb #graphdatabase #ai #python`. Strong reach for agent and LLM how-tos, supports canonical URLs and mermaid via embeds.
2. **Hashnode**: on a personal blog with a custom domain so it is the canonical copy. Good SEO and native code highlighting. Cross-post to dev.to with `canonical_url` pointing here.
3. **Medium**: submit to *Towards Data Science* or *Level Up Coding* for a wider ML audience, using Medium's import tool so the canonical link points to the Hashnode original.
