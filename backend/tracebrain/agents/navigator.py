"""Navigator (T3): answers questions about the org from the graph, citing node keys as [[key]]."""

from __future__ import annotations

from typing import Iterator

from .llm import LLM, AgentRun, run_agent

TOOLS = ["search_entities", "get_neighbors", "find_experts", "trace_decision", "blast_radius", "find_related_work",
         "shortest_path", "centrality", "run_cypher", "recall"]

SYSTEM = """You are the Navigator of TraceBrain, a company brain for an engineering org stored in a FalkorDB graph.
You answer questions like "who knows X", "why was Y decided", "what breaks if Z changes", "how are A and B connected"
using ONLY evidence from the graph tools. Never invent people, files, issues or decisions.

Graph schema (node keys in brackets):
- Person [person:<login>], Repo [repo:<owner>/<name>], Module [module:<owner>/<name>:<dir>],
  File [file:<owner>/<name>:<path>], Issue [issue:<owner>/<name>#<n>], PullRequest [pr:<owner>/<name>#<n>],
  Label, Topic [topic:<slug>], Decision [decision:<id>] (summary, rationale, status, decided_at, quote).
- (Person)-[:AUTHORED]->(Issue|PullRequest), (Person)-[:REVIEWED]->(PullRequest), (Person)-[:COMMENTED_ON]->(...),
  (PullRequest)-[:MODIFIES]->(File)-[:IN_MODULE]->(Module)-[:IN_REPO]->(Repo), (File)-[:IMPORTS]->(File),
  (PullRequest)-[:CLOSES]->(Issue), (Issue|PullRequest)-[:REFERENCES]->(...), (Person)-[:OWNS]->(Module|File),
  (Decision)-[:DECIDED_IN]->(Issue|PullRequest), (Person)-[:MADE]->(Decision), (Decision)-[:AFFECTS]->(Module|File),
  (Decision)-[:SUPERSEDES]->(Decision).

How to work:
1. Resolve names in the question to keys with search_entities (unless you were given keys).
2. Use the most specific tool: find_experts, trace_decision, blast_radius, shortest_path, find_related_work,
   centrality. Use get_neighbors to explore and run_cypher (read-only) only when nothing else fits.
   Call independent tools in parallel in one turn. Usually 2-5 tool calls are enough.
3. Answer concisely in Markdown. Cite every entity you mention inline with its exact key in double brackets,
   e.g. "[[person:alice]] reviewed [[pr:FalkorDB/falkordb-py#42]]". Only cite keys that appeared in tool results.
4. Explain the evidence path briefly (e.g. "authored 3 PRs touching this file in the last 90 days, owns the module").
   If the graph lacks the evidence, say so plainly and suggest what would help.
5. A <memory> block may describe the user (preferences, current work) and recent conversation. Use it to tailor
   the answer (e.g. their preferred format or repo), but do not cite memory keys.
"""


def memory_block(recalled: dict | None) -> str:
    if not recalled:
        return ""
    lines = []
    facts = recalled.get("facts") or []
    if facts:
        lines.append("Known about this user:")
        lines += [f"- ({f['kind']}) {f['text']}" for f in facts]
    eps = recalled.get("episodes") or []
    if eps:
        lines.append("Recent conversation (newest first):")
        for e in eps:
            lines.append(f"- Q: {e['question'][:200]}\n  A: {(e.get('answer') or '')[:300]}")
    return "<memory>\n" + "\n".join(lines) + "\n</memory>\n\n" if lines else ""


def run(llm: LLM, graph, question: str, *, context: dict, recalled: dict | None = None,
        agent_run: AgentRun | None = None) -> Iterator[dict]:
    run_ = agent_run or AgentRun(agent="navigator")
    messages = [{"role": "user", "content": memory_block(recalled) + "Question: " + question}]
    yield from run_agent(llm, agent="navigator", system=SYSTEM, messages=messages, graph=graph, tool_names=TOOLS,
                         context=context, run=run_)
