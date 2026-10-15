# TraceBrain: 3-minute demo video script

**Setup before recording**
- Graph ingested for all three repos (`tracebrain-ingest FalkorDB/GraphRAG-SDK FalkorDB/falkordb-py FalkorDB/QueryWeaver`) with an LLM key set, so Decisions exist.
- API on :8000, web on :5173 (or the live URL). Browser at 1440×900, dark mode, zoom 110%.
- Run every prompt below once before recording. The first run warms the prompt cache and lets you check the answers. Then switch to a fresh user (user switcher → `demo`) so the Memory tab starts empty.
- Have Claude Desktop open with the `tracebrain` MCP server configured (README) for the last shot.
- Keep it cut tight: trim the waiting while answers stream. Speed up 2× where marked.

| Time | Shot | Do | Voiceover |
|---|---|---|---|
| 0:00–0:12 | Title card, then the empty UI | Show the logo/title, cut to the UI with the example chips | "Ask an AI agent who should review a change, and it will guess. The answer is in your org's history: who wrote the code, who reviewed it, what imports it. That history is a graph. TraceBrain puts it in FalkorDB and lets agents query it." |
| 0:12–0:22 | Header + empty evidence graph | Hover the green health dot and tenant name `falkordb` | "This tenant is three FalkorDB repos: people, files, Python imports, issues, PRs, reviews, CODEOWNERS and decisions extracted from discussion threads, all in one graph." |
| 0:22–0:55 | **Experts** | Type *"Who knows the most about the GraphRAG-SDK api/main.py facade, and who should review changes to it?"* (tested live: one `find_experts` call, galshubeli 60.8 / Naseem77 42.5 with PR evidence). The chip *"Who knows the most about the Cypher query generation code?"* also works. While it runs, expand the step timeline. When the answer lands, click a person citation chip | "The Navigator resolves 'Cypher generation' to files and threads, then calls find_experts. That scores people over real paths: authored a PR that modifies this file, reviewed it, owns it in CODEOWNERS, commented, with a 180-day half-life. Every citation is a node. Click one and the graph shows the path behind the score." |
| 0:55–1:20 | **Blast radius** | Type *"What breaks if graphrag_sdk core/models.py changes?"* (tested live: 125 dependents, 88 direct / 36 / 1). Point at the highlighted import fan-in in the graph, then the people in the answer. `TODO: the falkordb/graph.py chip needs falkordb-py's code ingested; in the 15 Oct test run the graph had no falkordb-py files yet and the agent correctly said it couldn't answer` | "Blast radius walks the import graph in reverse, three hops out: 125 files depend on models.py, the modules they're in, who owns them, who touched them last, and any decisions that constrain them." |
| 1:20–1:45 | **Decision trace** | Click *"Why did GraphRAG-SDK switch its LLM abstraction?"*. Click the Decision chip, then the source PR node | "Why was it decided? Decisions are nodes linked to the thread they came from, the people who proposed and approved them, the code they affect, and the older decisions they replaced, with a verbatim quote." `TODO: confirm a Decision is found for this prompt after extraction; otherwise use the recommended decision prompt from docs/demo-qa.md` |
| 1:45–2:15 | **Triage** (2× while streaming) | Type *"Triage https://github.com/FalkorDB/GraphRAG-SDK/issues/330"* (tested live: finds open PR #331 already fixing it) or click the #274 chip. Open the **Triage** tab: assignee, 2 reviewers with reasons, blast radius, duplicates, draft comment. Click **Copy** | "Paste an issue and the Triage agent acts on the graph: duplicates with the PR that already fixed them, the files involved, their blast radius, and an assignee and two reviewers, each with a reason from the evidence. It drafts the GitHub comment. It never posts on its own. That's a separate, opt-in action." |
| 2:15–2:40 | **Memory across sessions** | Type: *"I mostly work on GraphRAG-SDK. Keep answers to short bullet lists."* Open **Memory**: new preference + semantic facts. Click **New session**, ask *"What was I working on last time?"* (tested live: answered from recalled episodes and facts with zero tool calls). Show the recalled-fact highlight. `TODO: check against docs/demo-qa.md` | "The Historian writes each turn to the graph as an episode, and pulls out durable facts, linked to the nodes they're about. New session: the Coordinator recalls them before routing, and it picks up where I left off. If I change my mind, the old fact is superseded, not deleted." |
| 2:40–2:50 | **Agent board** | Open **Agents**: show `chat → navigate → remember` chains with statuses | "Coordination is graph state too. Every turn is a task tree: the coordinator hands off to the navigator or triage, then to the historian, and every node they touched is linked." |
| 2:50–3:00 | **MCP** in Claude Desktop | Ask Claude Desktop: *"Using tracebrain, what are the most depended-upon files in GraphRAG-SDK?"* and show the `centrality` tool call | "The same graph tools are an MCP server, so Claude can use FalkorDB PageRank directly. TraceBrain: connected context, with the path behind every answer." |

**End card (2 s):** repo URL · live URL · "Built on FalkorDB for Graph Hacks".

## Backup prompts (if one misbehaves on camera)

- *"How are galshubeli and falkordb/graph.py connected?"* (`shortest_path`)
- *"Which files in GraphRAG-SDK are the most depended upon?"* (`centrality`, PageRank on IMPORTS)
- *"Has anyone reported a bug like 'connection pool exhausted under load'?"* (`find_related_work`)

`TODO: replace or confirm these with the live-tested prompts in docs/demo-qa.md.`

## Recording notes

- Record the UI at 60 fps and the voiceover separately, then cut answers to the moment the first citation chip appears.
- Zoom (in post) on the step timeline once in the Experts shot so the tool names are readable. That shot is where "the graph shapes what the agent does" is visible.
- Keep the evidence graph visible in every Navigator shot. It is the main thing judges should see.
