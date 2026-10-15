# TraceBrain: 3-minute demo video script

**Setup before recording**
- Graph ingested for all three repos (`tracebrain-ingest FalkorDB/GraphRAG-SDK FalkorDB/falkordb-py FalkorDB/QueryWeaver`) with an LLM key set, so Decisions exist.
- API on :8000, web on :5173 (or the live URL). Browser at 1440×900, dark mode, zoom 110%.
- Run every prompt below once before recording. The first run warms the prompt cache and lets you check the answers. Then switch to a fresh user (user switcher → `demo`) so the Memory tab starts empty. Run prompts 1–5 in one session; the memory shot needs those earlier turns.
- Have Claude Desktop open with the `tracebrain` MCP server configured (README) for the last shot.
- Keep it cut tight: trim the waiting while answers stream. Speed up 2× where marked.

| Time | Shot | Do | Voiceover |
|---|---|---|---|
| 0:00–0:12 | Title card, then the empty UI | Show the logo/title, cut to the UI with the example chips | "Ask an AI agent who should review a change, and it will guess. The answer is in your org's history: who wrote the code, who reviewed it, what imports it. That history is a graph. TraceBrain puts it in FalkorDB and lets agents query it." |
| 0:12–0:22 | Header + empty evidence graph | Hover the green health dot and tenant name `falkordb` | "This tenant is three FalkorDB repos: people, files, Python imports, issues, PRs, reviews, CODEOWNERS and decisions extracted from discussion threads, all in one graph." |
| 0:22–0:50 | **Experts** | Click the chip *"Who knows the most about the GraphRAG-SDK api/main.py facade, and who should review changes to it?"* (tested: 21s, galshubeli 60.8 / Naseem77 42.5 with PR evidence). Expand the step timeline while it runs. When the answer lands, click a person citation chip | "The Navigator resolves the file, then calls find_experts. That scores people over real paths: authored a PR that modifies this file, reviewed it, commented, with a 180-day half-life. Every citation is a node. Click one and the graph shows the path behind the score." |
| 0:50–1:10 | **Blast radius** | *"What breaks if graphrag_sdk core/models.py changes?"* (tested: 15s, 125 dependents: 88 direct / 36 / 1, in 8 modules). Let the graph burst fill the screen, then point at the people in the answer | "Blast radius walks the import graph in reverse, three hops out. 125 files depend on models.py. It also names who touched them last and should review." |
| 1:10–1:40 | **Triage** (2× while streaming) | *"Triage: https://github.com/FalkorDB/GraphRAG-SDK/issues/330"* (tested: 26s, finds open PR #331 already fixing it). Open the **Triage** tab: assignee, 2 reviewers with reasons, blast radius, the #331 match, draft comment. Click **Copy** | "Paste an issue and the Triage agent acts on the graph: it finds the open PR that already fixes it, the files involved, their blast radius, and an assignee and reviewers, each with a reason. It drafts the GitHub comment. It never posts on its own. That's a separate, opt-in action." |
| 1:40–2:05 | **Decision trace** | *"Why does falkordb-py ship its own embedded FalkorDB server code instead of depending on falkordblite?"* (tested: 22s). Click the Decision chip, then its source PR #130, then the superseding PR #164 | "Why was it decided? Decisions are extracted from discussion threads into nodes linked to the PR they came from, who made them, the code they affect, and what replaced them, with a verbatim maintainer quote." |
| 2:05–2:20 | **Multi-hop path** | *"How is mikemikimike connected to the GraphRAG-SDK ontology store (storage/ontology_store.py)?"* (tested: 16s). Point at the highlighted 3-hop path and the "Path behind the answer" strip | "Person, authored PR #331, modifies main.py, which imports the ontology store. Three hops, each one an edge you can click." |
| 2:20–2:40 | **Memory across sessions** | Click **New session**, ask *"What was I working on last time?"* (tested: 12.5s, no graph tools). Open the **Memory** tab: the facts the Historian wrote during this demo | "New session. The Historian wrote each turn to the graph as an episode and pulled out facts linked to the nodes they're about. The Coordinator recalls them before routing, so it picks up where I left off: triaging #330, with PR #331 to check." |
| 2:40–2:50 | **Agent board** | Open **Agents**: show `chat → navigate → remember` chains with statuses | "Coordination is graph state too. Every turn is a task tree: the coordinator hands off to the navigator or triage, then to the historian, and every node they touched is linked." |
| 2:50–3:00 | **MCP** in Claude Desktop | Ask Claude Desktop: *"Using tracebrain, what are the most depended-upon files in GraphRAG-SDK?"* and show the `centrality` tool call | "The same graph tools are an MCP server, so Claude can use FalkorDB PageRank directly. TraceBrain: connected context, with the path behind every answer." |

**End card (2 s):** repo URL · live URL · "Built on FalkorDB for Graph Hacks".

## Backup prompts (if one misbehaves on camera)

- *"How is galshubeli connected to graphrag_sdk core/models.py?"* (`shortest_path`)
- *"Which files in GraphRAG-SDK are the most depended upon?"* (`centrality`, PageRank on IMPORTS)
- *"Has anyone reported a bug like 'connection pool exhausted under load'?"* (`find_related_work`)

All six main prompts were tested live; transcripts and timings are in `docs/demo-qa.md`. Use a fresh user_id for the recording, because answers refer back to earlier questions.

## Recording notes

- Record the UI at 60 fps and the voiceover separately, then cut answers to the moment the first citation chip appears.
- Zoom (in post) on the step timeline once in the Experts shot so the tool names are readable. That shot is where "the graph shapes what the agent does" is visible.
- Keep the evidence graph visible in every Navigator shot. It is the main thing judges should see.
