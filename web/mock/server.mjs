// TraceBrain mock backend: plain Node http, no dependencies.
// Implements every endpoint in SPEC.md with realistic fake data so the web UI
// can be developed (and demoed) without the real FastAPI + FalkorDB backend.
//
//   node mock/server.mjs            (port 8000, override with PORT=...)
//   MOCK_FAST=1 node mock/server.mjs  (no delays, useful for quick checks)

import http from 'node:http'
import { randomUUID } from 'node:crypto'

const PORT = Number(process.env.PORT || 8000)
const FAST = process.env.MOCK_FAST === '1'
const TENANT = process.env.TRACEBRAIN_TENANT || 'falkordb'
const GH = 'https://github.com'

// ---------------------------------------------------------------------------
// Fake tenant graph
// ---------------------------------------------------------------------------

/** @type {Map<string, {id:string,label:string,title:string,url:string|null,props:Record<string,any>}>} */
const NODES = new Map()
/** @type {{source:string,target:string,type:string}[]} */
const EDGES = []

function node(id, label, title, url, props = {}) {
  NODES.set(id, { id, label, title, url: url ?? null, props: { key: id, ...props } })
  return id
}
function edge(source, target, type) {
  if (!EDGES.some((e) => e.source === source && e.target === target && e.type === type)) {
    EDGES.push({ source, target, type })
  }
}

const REPOS = {
  sdk: 'FalkorDB/GraphRAG-SDK',
  py: 'FalkorDB/falkordb-py',
  qw: 'FalkorDB/QueryWeaver',
}
node(`repo:${REPOS.sdk}`, 'Repo', REPOS.sdk, `${GH}/${REPOS.sdk}`, { name: 'GraphRAG-SDK', description: 'Build fast and accurate GenAI apps with GraphRAG on FalkorDB' })
node(`repo:${REPOS.py}`, 'Repo', REPOS.py, `${GH}/${REPOS.py}`, { name: 'falkordb-py', description: 'Python client for FalkorDB' })
node(`repo:${REPOS.qw}`, 'Repo', REPOS.qw, `${GH}/${REPOS.qw}`, { name: 'QueryWeaver', description: 'Text2SQL powered by a graph-based semantic layer' })

const PEOPLE = [
  ['mira-okafor', 'Mira Okafor'],
  ['tkessler', 'Tom Kessler'],
  ['lior-benami', 'Lior Ben-Ami'],
  ['ana-ruiz-dev', 'Ana Ruiz'],
  ['jpark42', 'June Park'],
  ['swatson', 'Sam Watson'],
  ['ravi-iyer', 'Ravi Iyer'],
]
for (const [login, name] of PEOPLE) {
  node(`person:${login}`, 'Person', name, `${GH}/${login}`, {
    login,
    name,
    avatar_url: `https://avatars.githubusercontent.com/${login}`,
  })
}

function mod(repo, dir) {
  const k = `module:${repo}:${dir}`
  node(k, 'Module', dir, `${GH}/${repo}/tree/main/${dir}`, { path: dir })
  edge(k, `repo:${repo}`, 'IN_REPO')
  return k
}
function file(repo, path, moduleKey) {
  const k = `file:${repo}:${path}`
  node(k, 'File', path, `${GH}/${repo}/blob/main/${path}`, { path, language: 'Python' })
  edge(k, moduleKey, 'IN_MODULE')
  return k
}

const mSdk = mod(REPOS.sdk, 'graphrag_sdk')
const mSdkModels = mod(REPOS.sdk, 'graphrag_sdk/models')
const mSdkSteps = mod(REPOS.sdk, 'graphrag_sdk/steps')
const mPy = mod(REPOS.py, 'falkordb')
const mPyAsync = mod(REPOS.py, 'falkordb/asyncio')
const mPyTests = mod(REPOS.py, 'tests')
const mQw = mod(REPOS.qw, 'api')

const F = {
  kg: file(REPOS.sdk, 'graphrag_sdk/kg.py', mSdk),
  chat: file(REPOS.sdk, 'graphrag_sdk/chat_session.py', mSdk),
  ontology: file(REPOS.sdk, 'graphrag_sdk/ontology.py', mSdk),
  helpers: file(REPOS.sdk, 'graphrag_sdk/helpers.py', mSdk),
  prompts: file(REPOS.sdk, 'graphrag_sdk/fixtures/prompts.py', mSdk),
  gqs: file(REPOS.sdk, 'graphrag_sdk/steps/graph_query_step.py', mSdkSteps),
  qa: file(REPOS.sdk, 'graphrag_sdk/steps/qa_step.py', mSdkSteps),
  model: file(REPOS.sdk, 'graphrag_sdk/models/model.py', mSdkModels),
  litellm: file(REPOS.sdk, 'graphrag_sdk/models/litellm.py', mSdkModels),
  openai: file(REPOS.sdk, 'graphrag_sdk/models/openai.py', mSdkModels),
  gemini: file(REPOS.sdk, 'graphrag_sdk/models/gemini.py', mSdkModels),
  pyInit: file(REPOS.py, 'falkordb/__init__.py', mPy),
  pyGraph: file(REPOS.py, 'falkordb/graph.py', mPy),
  pyFalkor: file(REPOS.py, 'falkordb/falkordb.py', mPy),
  pyResult: file(REPOS.py, 'falkordb/query_result.py', mPy),
  pyCluster: file(REPOS.py, 'falkordb/cluster.py', mPy),
  pyNode: file(REPOS.py, 'falkordb/node.py', mPy),
  pyAsyncGraph: file(REPOS.py, 'falkordb/asyncio/graph.py', mPyAsync),
  pyAsyncFalkor: file(REPOS.py, 'falkordb/asyncio/falkordb.py', mPyAsync),
  pyTestGraph: file(REPOS.py, 'tests/test_graph.py', mPyTests),
  qwLoader: file(REPOS.qw, 'api/loaders/graph_loader.py', mQw),
  qwAgent: file(REPOS.qw, 'api/agents/analysis_agent.py', mQw),
}

// Python imports (A IMPORTS B)
;[
  [F.kg, F.chat], [F.kg, F.ontology], [F.chat, F.gqs], [F.chat, F.qa],
  [F.gqs, F.helpers], [F.gqs, F.prompts], [F.gqs, F.model], [F.qa, F.model],
  [F.litellm, F.model], [F.openai, F.model], [F.gemini, F.model], [F.helpers, F.ontology],
  [F.pyFalkor, F.pyGraph], [F.pyAsyncGraph, F.pyGraph], [F.pyInit, F.pyFalkor],
  [F.pyAsyncFalkor, F.pyAsyncGraph], [F.pyCluster, F.pyFalkor], [F.pyGraph, F.pyResult],
  [F.pyResult, F.pyNode], [F.pyTestGraph, F.pyGraph],
].forEach(([a, b]) => edge(a, b, 'IMPORTS'))

// CODEOWNERS
edge('person:tkessler', mSdkSteps, 'OWNS')
edge('person:mira-okafor', mSdkModels, 'OWNS')
edge('person:swatson', mPy, 'OWNS')
edge('person:ana-ruiz-dev', F.pyResult, 'OWNS')
edge('person:ravi-iyer', mQw, 'OWNS')

// Labels and topics
const L = {
  bug: node(`label:${REPOS.sdk}:bug`, 'Label', 'bug', null, { name: 'bug' }),
  enh: node(`label:${REPOS.sdk}:enhancement`, 'Label', 'enhancement', null, { name: 'enhancement' }),
  llm: node(`label:${REPOS.sdk}:llm`, 'Label', 'llm', null, { name: 'llm' }),
  cypher: node(`label:${REPOS.sdk}:cypher`, 'Label', 'cypher', null, { name: 'cypher' }),
}
const T = {
  llm: node('topic:llm-abstraction', 'Topic', 'LLM abstraction', null, { name: 'LLM abstraction' }),
  cypher: node('topic:cypher-generation', 'Topic', 'Cypher generation', null, { name: 'Cypher generation' }),
  client: node('topic:client-api', 'Topic', 'Client API', null, { name: 'Client API' }),
}

function issue(repo, n, title, { state = 'open', labels = [], author, created_at, closed_at = null, body = '' }) {
  const k = `issue:${repo}#${n}`
  node(k, 'Issue', title, `${GH}/${repo}/issues/${n}`, { number: n, state, labels: labels.map((l) => l.split(':').pop()), created_at, closed_at, body })
  edge(k, `repo:${repo}`, 'IN_REPO')
  if (author) edge(`person:${author}`, k, 'AUTHORED')
  labels.forEach((l) => edge(k, l, 'HAS_LABEL'))
  return k
}
function pr(repo, n, title, { author, reviewers = [], files = [], merged = true, created_at, merged_at = null, body = '' }) {
  const k = `pr:${repo}#${n}`
  node(k, 'PullRequest', title, `${GH}/${repo}/pull/${n}`, { number: n, state: merged ? 'closed' : 'open', merged, created_at, merged_at, body })
  edge(k, `repo:${repo}`, 'IN_REPO')
  edge(`person:${author}`, k, 'AUTHORED')
  reviewers.forEach((r) => edge(`person:${r}`, k, 'REVIEWED'))
  files.forEach((f) => edge(k, f, 'MODIFIES'))
  return k
}

const I = {
  burden: issue(REPOS.sdk, 198, 'Supporting each LLM provider separately is a maintenance burden', { state: 'closed', labels: [L.llm, L.enh], author: 'lior-benami', created_at: '2025-02-11T09:12:00Z', closed_at: '2025-03-04T16:40:00Z', body: 'Every provider (OpenAI, Gemini, Ollama, Azure) has its own wrapper class and they drift. Proposal: route everything through LiteLLM.' }),
  dir: issue(REPOS.sdk, 263, 'Generated Cypher sometimes reverses relationship direction', { state: 'open', labels: [L.bug, L.cypher], author: 'jpark42', created_at: '2025-08-19T11:03:00Z' }),
  fences: issue(REPOS.sdk, 231, 'Gemini returns Cypher wrapped in markdown fences', { state: 'closed', labels: [L.bug, L.cypher], author: 'ravi-iyer', created_at: '2025-05-02T14:20:00Z', closed_at: '2025-05-09T10:00:00Z' }),
  multihop: issue(REPOS.sdk, 274, 'Cypher generation fails on multi-hop questions with Gemini', { state: 'open', labels: [L.bug], author: 'ravi-iyer', created_at: '2025-09-30T08:45:00Z', body: 'Asking "which suppliers ship to customers of X" produces a query that fails with "Unknown function" on gemini-2.0-flash. Works with gpt-4o.' }),
  timeout: issue(REPOS.py, 91, 'Graph.query has no timeout in the asyncio client', { state: 'closed', labels: [], author: 'ana-ruiz-dev', created_at: '2025-06-01T10:00:00Z', closed_at: '2025-06-20T12:00:00Z' }),
}
const P = {
  litellm: pr(REPOS.sdk, 212, 'Switch LLM layer to LiteLLM', { author: 'mira-okafor', reviewers: ['tkessler', 'lior-benami'], files: [F.model, F.litellm, F.openai, F.gemini], created_at: '2025-02-20T10:00:00Z', merged_at: '2025-03-04T16:38:00Z', body: 'Replaces the per-provider classes with a LiteLLM-backed model. Closes #198.' }),
  validate: pr(REPOS.sdk, 241, 'Validate generated Cypher against the ontology', { author: 'tkessler', reviewers: ['jpark42'], files: [F.gqs, F.helpers, F.prompts], created_at: '2025-05-05T09:00:00Z', merged_at: '2025-05-09T09:58:00Z', body: 'Strips markdown fences, checks labels/relationships against the ontology, and retries once. Fixes #231.' }),
  retry: pr(REPOS.sdk, 257, 'Retry Cypher generation with error feedback', { author: 'tkessler', reviewers: ['jpark42', 'mira-okafor'], files: [F.gqs, F.prompts], created_at: '2025-07-14T13:00:00Z', merged_at: '2025-07-22T15:10:00Z' }),
  fewshot: pr(REPOS.sdk, 188, 'Few-shot examples for the Cypher prompt', { author: 'jpark42', reviewers: ['tkessler'], files: [F.prompts], created_at: '2025-01-15T12:00:00Z', merged_at: '2025-01-18T08:00:00Z' }),
  ontology: pr(REPOS.sdk, 266, 'Ontology-aware relationship direction hints', { author: 'jpark42', reviewers: ['tkessler'], files: [F.gqs, F.ontology, F.prompts], merged: false, created_at: '2025-09-02T10:00:00Z' }),
  asyncTimeout: pr(REPOS.py, 96, 'Add timeout to async Graph.query', { author: 'swatson', reviewers: ['ana-ruiz-dev'], files: [F.pyGraph, F.pyAsyncGraph], created_at: '2025-06-10T10:00:00Z', merged_at: '2025-06-20T11:58:00Z' }),
  result: pr(REPOS.py, 88, 'Refactor QueryResult parsing', { author: 'ana-ruiz-dev', reviewers: ['swatson'], files: [F.pyResult, F.pyGraph, F.pyNode], created_at: '2025-04-03T10:00:00Z', merged_at: '2025-04-11T17:00:00Z' }),
  cluster: pr(REPOS.py, 103, 'Cluster-aware connection pooling', { author: 'swatson', reviewers: ['lior-benami'], files: [F.pyCluster, F.pyFalkor], created_at: '2025-08-01T10:00:00Z', merged_at: '2025-08-09T10:00:00Z' }),
  loader: pr(REPOS.qw, 41, 'Load schema graph via falkordb-py async client', { author: 'ravi-iyer', reviewers: ['swatson'], files: [F.qwLoader, F.qwAgent], created_at: '2025-08-20T10:00:00Z', merged_at: '2025-08-28T10:00:00Z' }),
}
edge(P.litellm, I.burden, 'CLOSES')
edge(P.validate, I.fences, 'CLOSES')
edge(P.asyncTimeout, I.timeout, 'CLOSES')
edge(I.multihop, I.fences, 'REFERENCES')
edge(P.ontology, I.dir, 'REFERENCES')
edge('person:mira-okafor', I.burden, 'COMMENTED_ON')
edge('person:tkessler', I.burden, 'COMMENTED_ON')
edge('person:tkessler', I.dir, 'COMMENTED_ON')
edge('person:tkessler', I.fences, 'COMMENTED_ON')
;[I.burden, P.litellm].forEach((k) => edge(k, T.llm, 'ABOUT'))
;[I.dir, I.fences, I.multihop, P.validate, P.retry, P.fewshot, P.ontology].forEach((k) => edge(k, T.cypher, 'ABOUT'))
;[P.asyncTimeout, P.result, P.cluster].forEach((k) => edge(k, T.client, 'ABOUT'))

const D = {
  wrappers: node('decision:9b04e1d7aa20', 'Decision', 'Maintain first-party wrappers per LLM provider', null, { summary: 'Maintain first-party wrappers per LLM provider', rationale: 'Early on, fine-grained control over each provider API outweighed the duplication.', status: 'superseded', decided_at: '2024-07-02T00:00:00Z', quote: 'Let us keep thin wrappers for now and revisit when we add a third provider.', source_url: `${GH}/${REPOS.sdk}/pull/34` }),
  litellm: node('decision:3fa1c09b2e71', 'Decision', 'Adopt LiteLLM as the single LLM abstraction', `${GH}/${REPOS.sdk}/pull/212`, { summary: 'Adopt LiteLLM as the single LLM abstraction', rationale: 'Four provider wrappers had drifted (streaming, tool calls, retries). LiteLLM gives one interface for 100+ providers and moves the maintenance burden upstream.', status: 'accepted', decided_at: '2025-03-04T16:38:00Z', quote: 'We spend more time keeping wrappers in sync than on GraphRAG itself. LiteLLM covers all of them.', source_url: `${GH}/${REPOS.sdk}/pull/212` }),
  validate: node('decision:c51e8a0f3d22', 'Decision', 'Validate generated Cypher against the ontology before execution', `${GH}/${REPOS.sdk}/pull/241`, { summary: 'Validate generated Cypher against the ontology before execution', rationale: 'LLM output was executed blindly; malformed queries surfaced as user-facing errors.', status: 'accepted', decided_at: '2025-05-09T09:58:00Z', quote: 'Never send model output straight to the DB.', source_url: `${GH}/${REPOS.sdk}/pull/241` }),
}
edge(D.litellm, D.wrappers, 'SUPERSEDES')
edge(D.litellm, P.litellm, 'DECIDED_IN')
edge(D.litellm, I.burden, 'DECIDED_IN')
edge('person:lior-benami', D.litellm, 'MADE')
edge('person:mira-okafor', D.litellm, 'MADE')
edge('person:tkessler', D.litellm, 'MADE')
edge(D.litellm, mSdkModels, 'AFFECTS')
edge(D.litellm, T.llm, 'ABOUT')
edge(D.validate, P.validate, 'DECIDED_IN')
edge('person:tkessler', D.validate, 'MADE')
edge(D.validate, F.gqs, 'AFFECTS')
edge(D.validate, T.cypher, 'ABOUT')

// Extra background activity so that "expand neighbors" has something to show.
const fillerTitles = [
  'Bump litellm to 1.52', 'Docs: ontology from JSON', 'Fix flaky test_kg_create', 'Support Azure OpenAI deployments',
  'Expose temperature in ChatSession', 'Typo in README quickstart', 'Add Ollama example notebook', 'Stream answers from qa_step',
  'Handle empty ontology gracefully', 'Cache ontology prompt fragment', 'Add py.typed marker', 'Pin falkordb>=1.0.10',
]
const fillerFiles = [F.kg, F.chat, F.qa, F.ontology, F.litellm, F.prompts, F.helpers, F.gqs]
fillerTitles.forEach((t, i) => {
  const [login] = PEOPLE[(i * 3 + 1) % PEOPLE.length]
  const [rev] = PEOPLE[(i * 5 + 2) % PEOPLE.length]
  const n = 140 + i * 7
  const day = String(1 + ((i * 3) % 27)).padStart(2, '0')
  pr(REPOS.sdk, n, t, { author: login, reviewers: rev !== login ? [rev] : [], files: [fillerFiles[i % fillerFiles.length], fillerFiles[(i + 3) % fillerFiles.length]], created_at: `2025-0${1 + (i % 9)}-${day}T10:00:00Z`, merged_at: `2025-0${1 + (i % 9)}-${day}T18:00:00Z` })
})

function subgraph(keys, highlight = []) {
  const set = new Set(keys)
  return {
    type: 'subgraph',
    nodes: keys.filter((k) => NODES.has(k)).map((k) => NODES.get(k)),
    edges: EDGES.filter((e) => set.has(e.source) && set.has(e.target)),
    highlight,
  }
}

function neighbors(key, depth = 1) {
  const seen = new Set([key])
  let frontier = [key]
  for (let d = 0; d < depth; d++) {
    const next = []
    for (const k of frontier) {
      for (const e of EDGES) {
        const other = e.source === k ? e.target : e.target === k ? e.source : null
        if (other && !seen.has(other)) {
          seen.add(other)
          next.push(other)
        }
      }
    }
    frontier = next.slice(0, 60)
  }
  const keys = [...seen].slice(0, 80)
  const set = new Set(keys)
  return {
    nodes: keys.map((k) => NODES.get(k)).filter(Boolean),
    edges: EDGES.filter((e) => set.has(e.source) && set.has(e.target)),
  }
}

// ---------------------------------------------------------------------------
// Memory + task state (in-process, per user)
// ---------------------------------------------------------------------------

const now = () => new Date().toISOString()
const ago = (min) => new Date(Date.now() - min * 60_000).toISOString()

/** @type {Map<string, {facts:any[], episodes:any[], sessions:any[]}>} */
const MEMORY = new Map()
function memoryFor(user) {
  if (!MEMORY.has(user)) {
    MEMORY.set(user, {
      facts: [
        { key: `fact:${randomUUID()}`, text: 'Works mostly on GraphRAG-SDK, specifically the Cypher generation step', kind: 'semantic', confidence: 0.86, created_at: ago(60 * 26), updated_at: ago(60 * 3), valid: true, about: [F.gqs] },
        { key: `fact:${randomUUID()}`, text: 'Prefers short answers with file paths, not prose', kind: 'preference', confidence: 0.92, created_at: ago(60 * 50), updated_at: ago(60 * 50), valid: true, about: [] },
        { key: `fact:${randomUUID()}`, text: 'For falkordb-py changes, always loop in the CODEOWNER of falkordb/ as reviewer', kind: 'procedural', confidence: 0.74, created_at: ago(60 * 72), updated_at: ago(60 * 72), valid: true, about: [mPy] },
        { key: `fact:${randomUUID()}`, text: 'Was investigating the Ollama provider wrapper', kind: 'semantic', confidence: 0.4, created_at: ago(60 * 24 * 9), updated_at: ago(60 * 24 * 2), valid: false, about: [F.openai] },
      ],
      episodes: [
        { key: `episode:${randomUUID()}`, question: 'Who reviewed the LiteLLM migration?', answer: 'Tom Kessler and Lior Ben-Ami reviewed PR #212.', agent: 'navigator', created_at: ago(60 * 3) },
        { key: `episode:${randomUUID()}`, question: 'Triage issue #263', answer: 'Assigned to June Park; reviewers Tom Kessler, Mira Okafor.', agent: 'triage', created_at: ago(60 * 27) },
      ],
      sessions: [{ key: `session:${randomUUID()}`, started_at: ago(60 * 27) }],
    })
  }
  return MEMORY.get(user)
}

/** @type {any[]} */
const TASKS = []
function seedTasks() {
  const mk = (kind, agent, status, input, output, minAgo, handoffs = []) => {
    const t = { key: `task:${randomUUID()}`, kind, status, agent, input, output, created_at: ago(minAgo), updated_at: ago(minAgo - 0.2), handoffs }
    TASKS.push(t)
    return t
  }
  const h1 = mk('remember', 'historian', 'done', 'episode + facts', '1 episode, 1 fact', 180)
  const n1 = mk('answer', 'navigator', 'done', 'Who reviewed the LiteLLM migration?', 'Answered with 4 citations', 180.2, [h1.key])
  mk('route', 'coordinator', 'done', 'Who reviewed the LiteLLM migration?', 'routed to navigator', 180.4, [n1.key])
  const h2 = mk('remember', 'historian', 'done', 'episode + facts', '1 episode, 2 facts', 27 * 60)
  const t2 = mk('triage', 'triage', 'done', `${GH}/${REPOS.sdk}/issues/263`, 'plan: assignee jpark42', 27 * 60 + 0.3, [h2.key])
  mk('route', 'coordinator', 'done', `Triage: ${GH}/${REPOS.sdk}/issues/263`, 'routed to triage', 27 * 60 + 0.5, [t2.key])
}
seedTasks()

function createTask(kind, agent, input) {
  const t = { key: `task:${randomUUID()}`, kind, status: 'queued', agent, input, output: null, created_at: now(), updated_at: now(), handoffs: [] }
  TASKS.push(t)
  return t
}
function setTask(t, status, output) {
  t.status = status
  if (output !== undefined) t.output = output
  t.updated_at = now()
}
const taskEvent = (t) => ({ type: 'task', task: { key: t.key, kind: t.kind, status: t.status, agent: t.agent } })

// ---------------------------------------------------------------------------
// Scripted chat scenarios
// ---------------------------------------------------------------------------

const SCENARIOS = {
  experts: {
    match: (m) => /who (knows|owns|should)|expert|most about/i.test(m),
    steps: [
      { tool: 'search_entities', input: { query: 'Cypher query generation', labels: ['File', 'Module', 'Topic'] }, summary: 'Matched topic:cypher-generation and 3 files (graph_query_step.py, prompts.py, helpers.py)', graph: () => subgraph([T.cypher, F.gqs, F.prompts, F.helpers, mSdkSteps]) },
      { tool: 'find_experts', input: { target: F.gqs, limit: 5 }, summary: 'Scored 6 people via AUTHORED/REVIEWED/MODIFIES/OWNS with recency decay; top: tkessler 0.91, jpark42 0.64', graph: () => subgraph(['person:tkessler', 'person:jpark42', 'person:mira-okafor', P.validate, P.retry, P.fewshot, P.ontology, F.gqs, F.prompts, F.helpers, mSdkSteps, T.cypher], ['person:tkessler', P.validate, F.gqs, mSdkSteps]) },
      { tool: 'trace_decision', input: { query: 'validate cypher' }, summary: 'Found decision c51e8a0f3d22 (accepted), made by tkessler in PR #241', graph: () => subgraph([D.validate, P.validate, 'person:tkessler', F.gqs], ['person:tkessler', D.validate, P.validate, F.gqs]) },
    ],
    answer: `**Tom Kessler** ([[person:tkessler]]) is the clear expert on Cypher generation.

- He owns the steps module via CODEOWNERS ([[${mSdkSteps}]]) and authored the two most recent changes to [[${F.gqs}]]: ontology validation in [[${P.validate}]] and retry-with-feedback in [[${P.retry}]].
- He made the decision to validate generated Cypher before execution ([[${D.validate}]]).
- **June Park** ([[person:jpark42]]) is a strong second: she wrote the few-shot prompt ([[${P.fewshot}]]), reviews most of Tom's PRs, and has an open PR on relationship direction ([[${P.ontology}]]).

For a quick review, ask Tom; for prompt changes, June.`,
    citations: ['person:tkessler', mSdkSteps, F.gqs, P.validate, P.retry, D.validate, 'person:jpark42', P.fewshot, P.ontology],
    facts: [{ text: 'Interested in who maintains Cypher generation (graph_query_step.py)', kind: 'semantic' }],
  },
  decision: {
    match: (m) => /why|decid|switch|decision/i.test(m),
    steps: [
      { tool: 'search_entities', input: { query: 'LLM abstraction switch', labels: ['Decision', 'Topic', 'PullRequest'] }, summary: 'Matched topic:llm-abstraction, PR #212 and 2 decisions', graph: () => subgraph([T.llm, P.litellm, D.litellm, D.wrappers]) },
      { tool: 'trace_decision', input: { query: 'LLM abstraction' }, summary: 'Decision 3fa1c09b2e71 (accepted 2025-03-04) supersedes 9b04e1d7aa20; source PR #212 closes issue #198', graph: () => subgraph([D.litellm, D.wrappers, P.litellm, I.burden, 'person:lior-benami', 'person:mira-okafor', 'person:tkessler', mSdkModels, T.llm], [D.wrappers, D.litellm, P.litellm, I.burden]) },
      { tool: 'get_neighbors', input: { key: mSdkModels, rel_types: ['IN_MODULE'], depth: 1 }, summary: 'Affected module graphrag_sdk/models has 4 files', graph: () => subgraph([mSdkModels, F.model, F.litellm, F.openai, F.gemini, D.litellm], [D.litellm, mSdkModels]) },
    ],
    answer: `GraphRAG-SDK replaced its per-provider wrappers with **LiteLLM** ([[${D.litellm}]]), which supersedes the earlier choice to keep first-party wrappers ([[${D.wrappers}]]).

**Why:** Lior Ben-Ami ([[person:lior-benami]]) raised in [[${I.burden}]] that four wrapper classes had drifted on streaming, tool calls and retries. Mira Okafor ([[person:mira-okafor]]) implemented the switch in [[${P.litellm}]], reviewed by Tom Kessler.

> "We spend more time keeping wrappers in sync than on GraphRAG itself."

**Impact:** everything in [[${mSdkModels}]] now routes through [[${F.litellm}]]; the old provider files remain as thin shims.`,
    citations: [D.litellm, D.wrappers, 'person:lior-benami', I.burden, 'person:mira-okafor', P.litellm, mSdkModels, F.litellm],
    facts: [{ text: 'Asked about the history of the LLM abstraction in GraphRAG-SDK', kind: 'semantic' }],
  },
  blast: {
    match: (m) => /break|blast|impact|depends|change/i.test(m),
    steps: [
      { tool: 'search_entities', input: { query: 'falkordb/graph.py', labels: ['File'] }, summary: `Resolved to ${F.pyGraph}`, graph: () => subgraph([F.pyGraph, mPy]) },
      { tool: 'blast_radius', input: { key: F.pyGraph, depth: 3 }, summary: '6 files import it transitively (depth ≤ 3) across 3 modules; 1 test file', graph: () => subgraph([F.pyGraph, F.pyFalkor, F.pyAsyncGraph, F.pyInit, F.pyAsyncFalkor, F.pyCluster, F.pyTestGraph, F.pyResult], [F.pyInit, F.pyFalkor, F.pyGraph]) },
      { tool: 'find_experts', input: { target: F.pyGraph, limit: 3 }, summary: 'Owners: swatson (CODEOWNERS falkordb/), ana-ruiz-dev (recent PR #88)', graph: () => subgraph(['person:swatson', 'person:ana-ruiz-dev', mPy, P.asyncTimeout, P.result, F.pyGraph, F.pyResult], ['person:swatson', mPy, F.pyGraph]) },
    ],
    answer: `Changing [[${F.pyGraph}]] reaches **6 files** in falkordb-py:

- Direct importers: [[${F.pyFalkor}]], [[${F.pyAsyncGraph}]] and [[${F.pyTestGraph}]].
- Transitive: [[${F.pyInit}]] (the public API), [[${F.pyCluster}]] and [[${F.pyAsyncFalkor}]].
- It depends on [[${F.pyResult}]], so signature changes ripple into result parsing.

**Who to loop in:** Sam Watson ([[person:swatson]]) owns [[${mPy}]] and last changed it in [[${P.asyncTimeout}]]; Ana Ruiz ([[person:ana-ruiz-dev]]) refactored parsing in [[${P.result}]]. Downstream, QueryWeaver's loader ([[${F.qwLoader}]]) uses the async client.`,
    citations: [F.pyGraph, F.pyFalkor, F.pyAsyncGraph, F.pyTestGraph, F.pyInit, F.pyCluster, F.pyAsyncFalkor, F.pyResult, 'person:swatson', mPy, P.asyncTimeout, 'person:ana-ruiz-dev', P.result, F.qwLoader],
    facts: [{ text: 'Considering changes to falkordb-py graph.py', kind: 'semantic' }],
  },
}

function pickScenario(message) {
  if (SCENARIOS.experts.match(message)) return SCENARIOS.experts
  if (/break|blast|impact/i.test(message)) return SCENARIOS.blast
  if (SCENARIOS.decision.match(message)) return SCENARIOS.decision
  if (SCENARIOS.blast.match(message)) return SCENARIOS.blast
  return SCENARIOS.experts
}

function triagePlan(message) {
  const m = message.match(/issues\/(\d+)/)
  const n = m ? Number(m[1]) : 274
  const issueKey = `issue:${REPOS.sdk}#${n}`
  if (!NODES.has(issueKey)) {
    issue(REPOS.sdk, n, 'Cypher generation fails on multi-hop questions', { state: 'open', labels: [], author: 'ravi-iyer', created_at: ago(60 * 5) })
    edge(issueKey, I.fences, 'REFERENCES')
  }
  const iss = NODES.get(issueKey)
  return {
    issueKey,
    plan: {
      issue: { key: issueKey, title: iss.title, url: iss.url, number: n, repo: REPOS.sdk },
      summary: 'Gemini produces Cypher with unsupported functions on multi-hop questions. The query step validates labels but not function names, so the error surfaces at execution time.',
      assignee: { key: 'person:tkessler', login: 'tkessler', name: 'Tom Kessler', score: 0.91, reason: 'Owns graphrag_sdk/steps (CODEOWNERS); authored the ontology validation (#241) and retry loop (#257) in graph_query_step.py' },
      reviewers: [
        { key: 'person:jpark42', login: 'jpark42', name: 'June Park', score: 0.64, reason: 'Wrote the Cypher few-shot prompt (#188); has an open PR touching the same prompt (#266)' },
        { key: 'person:mira-okafor', login: 'mira-okafor', name: 'Mira Okafor', score: 0.52, reason: 'Owns graphrag_sdk/models; Gemini goes through the LiteLLM layer she built (#212)' },
      ],
      related_files: [
        { key: F.gqs, path: 'graphrag_sdk/steps/graph_query_step.py', reason: 'Generates and validates Cypher' },
        { key: F.prompts, path: 'graphrag_sdk/fixtures/prompts.py', reason: 'Cypher generation prompt' },
        { key: F.gemini, path: 'graphrag_sdk/models/gemini.py', reason: 'Provider named in the report' },
      ],
      blast_radius: [
        { key: F.chat, path: 'graphrag_sdk/chat_session.py', depth: 1, owners: ['tkessler'] },
        { key: F.kg, path: 'graphrag_sdk/kg.py', depth: 2, owners: [] },
        { key: F.helpers, path: 'graphrag_sdk/helpers.py', depth: 1, owners: [] },
        { key: F.qa, path: 'graphrag_sdk/steps/qa_step.py', depth: 2, owners: ['tkessler'] },
      ],
      duplicates: [
        { key: I.fences, number: 231, title: NODES.get(I.fences).title, url: NODES.get(I.fences).url, state: 'closed', similarity: 0.82 },
        { key: I.dir, number: 263, title: NODES.get(I.dir).title, url: NODES.get(I.dir).url, state: 'open', similarity: 0.67 },
      ],
      labels: ['bug', 'cypher', 'llm'],
      priority: 'P2',
      draft_comment: `Thanks for the report, @ravi-iyer!

This looks related to #231 (Gemini output wrapped in markdown fences, fixed in #241) and possibly #263 (relationship direction). Our Cypher validation in \`graphrag_sdk/steps/graph_query_step.py\` checks labels and relationship types against the ontology, but not function names, so unsupported functions only fail at execution time.

Suggested next steps:
- Extend validation to reject unknown functions and feed the error back into the retry loop (#257).
- Add a multi-hop regression test using gemini-2.0-flash.

Proposed owner: @tkessler. Reviewers: @jpark42, @mira-okafor.
Labels: \`bug\`, \`cypher\`, \`llm\`.

_Drafted by TraceBrain from the FalkorDB knowledge graph. Not posted automatically._`,
    },
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, FAST ? 0 : ms))

async function runChat(body, send) {
  const message = String(body.message || '').trim()
  const userId = String(body.user_id || 'anonymous')
  const sessionId = body.session_id || `session:${randomUUID()}`
  const mem = memoryFor(userId)
  if (!body.session_id) mem.sessions.unshift({ key: sessionId, started_at: now() })
  const isTriage = /^triage\b/i.test(message) || /github\.com\/.+\/issues\/\d+/.test(message)

  const coord = createTask('route', 'coordinator', message)
  send(taskEvent(coord))
  await sleep(150)
  setTask(coord, 'running')
  send(taskEvent(coord))

  const recalled = mem.facts.filter((f) => f.valid).slice(0, 2).map(({ key, text, kind }) => ({ key, text, kind }))
  send({ type: 'step', agent: 'coordinator', tool: 'recall', input: { user_id: userId, query: message }, summary: `Recalled ${recalled.length} facts and 1 recent episode for ${userId}` })
  send({ type: 'memory', written: [], recalled })
  await sleep(150)

  const agentName = isTriage ? 'triage' : 'navigator'
  const worker = createTask(isTriage ? 'triage' : 'answer', agentName, message)
  coord.handoffs.push(worker.key)
  send({ type: 'step', agent: 'coordinator', tool: 'handoff', input: { to: agentName }, summary: isTriage ? 'Issue URL detected, routing to Triage' : 'Question detected, routing to Navigator' })
  setTask(coord, 'done', `routed to ${agentName}`)
  send(taskEvent(coord))
  await sleep(150)
  setTask(worker, 'running')
  send(taskEvent(worker))
  await sleep(150)

  let answer, citations, facts
  if (isTriage) {
    const { issueKey, plan } = triagePlan(message)
    const steps = [
      { tool: 'get_neighbors', input: { key: issueKey, depth: 1 }, summary: `Fetched ${plan.issue.title.slice(0, 48)}… and its references`, graph: () => subgraph([issueKey, I.fences, `repo:${REPOS.sdk}`], [issueKey]) },
      { tool: 'find_related_work', input: { text: plan.issue.title }, summary: '2 duplicate candidates: #231 (0.82, closed), #263 (0.67, open)', graph: () => subgraph([issueKey, I.fences, I.dir, P.validate, P.ontology, T.cypher], [issueKey, I.fences, P.validate]) },
      { tool: 'search_entities', input: { query: 'cypher generation gemini', labels: ['File'] }, summary: 'Related files: graph_query_step.py, prompts.py, gemini.py', graph: () => subgraph([issueKey, I.fences, P.validate, F.gqs, F.prompts, F.gemini, mSdkSteps], [issueKey, I.fences, P.validate, F.gqs]) },
      { tool: 'blast_radius', input: { key: F.gqs, depth: 3 }, summary: '4 files affected upstream (chat_session.py, kg.py, helpers.py, qa_step.py)', graph: () => subgraph([F.gqs, F.chat, F.kg, F.helpers, F.qa, F.prompts], [F.gqs, F.chat, F.kg]) },
      { tool: 'find_experts', input: { target: F.gqs, limit: 3 }, summary: 'Assignee tkessler (0.91); reviewers jpark42 (0.64), mira-okafor (0.52)', graph: () => subgraph([issueKey, I.fences, P.validate, F.gqs, mSdkSteps, 'person:tkessler', 'person:jpark42', 'person:mira-okafor', P.fewshot, mSdkModels, P.litellm], [issueKey, I.fences, P.validate, 'person:tkessler']) },
    ]
    for (const s of steps) {
      send({ type: 'step', agent: 'triage', tool: s.tool, input: s.input, summary: s.summary })
      await sleep(180)
      send(s.graph())
      await sleep(150)
    }
    send({ type: 'triage', plan })
    answer = `Triage plan ready for [[${issueKey}]]. Assign **Tom Kessler** ([[person:tkessler]]), with June Park ([[person:jpark42]]) and Mira Okafor ([[person:mira-okafor]]) as reviewers. Likely duplicate of [[${I.fences}]], which was fixed by [[${P.validate}]]. The draft comment is in the Triage tab and has not been posted.`
    citations = [issueKey, 'person:tkessler', 'person:jpark42', 'person:mira-okafor', I.fences, P.validate]
    facts = [{ text: `Triaged ${REPOS.sdk}#${plan.issue.number}`, kind: 'semantic' }]
  } else {
    const sc = pickScenario(message)
    for (const s of sc.steps) {
      send({ type: 'step', agent: 'navigator', tool: s.tool, input: s.input, summary: s.summary })
      await sleep(200)
      send(s.graph())
      await sleep(150)
    }
    answer = sc.answer
    citations = sc.citations
    facts = sc.facts
  }

  // stream tokens
  const parts = answer.match(/\S+\s*|\s+/g) || []
  for (let i = 0; i < parts.length; i += 2) {
    send({ type: 'token', text: parts.slice(i, i + 2).join('') })
    await sleep(28)
  }
  send({ type: 'answer', text: answer, citations: citations.map((k) => NODES.get(k)).filter(Boolean) })
  setTask(worker, 'done', isTriage ? 'plan + draft comment' : `answered with ${citations.length} citations`)
  send(taskEvent(worker))
  await sleep(150)

  // historian
  const hist = createTask('remember', 'historian', 'episode + facts')
  worker.handoffs.push(hist.key)
  send(taskEvent(hist))
  await sleep(120)
  setTask(hist, 'running')
  send(taskEvent(hist))
  send({ type: 'step', agent: 'historian', tool: 'remember', input: { user_id: userId, kind: facts[0].kind }, summary: `Wrote 1 episode (${citations.length} CITED edges) and ${facts.length} fact` })
  const written = facts.map((f) => ({ key: `fact:${randomUUID()}`, text: f.text, kind: f.kind }))
  for (const w of written) {
    const same = mem.facts.find((f) => f.valid && f.text === w.text)
    if (same) {
      w.key = same.key
      same.updated_at = now()
      same.confidence = Math.min(0.99, (same.confidence ?? 0.7) + 0.08)
    } else mem.facts.unshift({ ...w, confidence: 0.7, created_at: now(), updated_at: now(), valid: true, about: [] })
  }
  mem.episodes.unshift({ key: `episode:${randomUUID()}`, question: message, answer: answer.replace(/\s*\(?\[\[[^\]]+\]\]\)?/g, '').replace(/\*\*/g, '').slice(0, 160), agent: agentName, created_at: now() })
  await sleep(150)
  send({ type: 'memory', written, recalled: [] })
  setTask(hist, 'done', `1 episode, ${written.length} fact`)
  send(taskEvent(hist))
  await sleep(80)
  send({ type: 'done', session_id: sessionId })
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

const INGEST = new Map()

function json(res, status, data) {
  res.writeHead(status, { 'content-type': 'application/json', 'access-control-allow-origin': '*' })
  res.end(JSON.stringify(data))
}
function readBody(req) {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', () => {
      try { resolve(raw ? JSON.parse(raw) : {}) } catch { resolve({}) }
    })
  })
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  const path = url.pathname
  const method = req.method
  if (method === 'OPTIONS') {
    res.writeHead(204, { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'GET,POST,DELETE' })
    return res.end()
  }
  console.log(method, path)

  try {
    if (method === 'GET' && path === '/api/health') {
      return json(res, 200, { ok: true, falkordb: true, llm_configured: false, tenant: TENANT, mock: true })
    }

    if (method === 'POST' && path === '/api/chat') {
      const body = await readBody(req)
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive', 'access-control-allow-origin': '*' })
      let closed = false
      req.on('close', () => (closed = true))
      res.on('close', () => (closed = true))
      const send = (ev) => { if (!closed) res.write(`data: ${JSON.stringify(ev)}\n\n`) }
      try {
        await runChat(body, send)
      } catch (err) {
        send({ type: 'error', message: String(err?.message || err) })
      }
      return res.end()
    }

    if (method === 'GET' && path.startsWith('/api/graph/node/')) {
      const key = decodeURIComponent(path.slice('/api/graph/node/'.length))
      if (!NODES.has(key)) return json(res, 404, { detail: `node not found: ${key}` })
      const depth = Math.min(2, Number(url.searchParams.get('depth') || 1))
      return json(res, 200, neighbors(key, depth))
    }

    if (method === 'GET' && path === '/api/graph/overview') {
      const counts = {}
      for (const n of NODES.values()) counts[n.label] = (counts[n.label] || 0) + 1
      const degree = (k) => EDGES.filter((e) => e.source === k || e.target === k).length
      const top_people = [...NODES.values()].filter((n) => n.label === 'Person')
        .map((n) => ({ key: n.id, login: n.props.login, name: n.props.name, score: degree(n.id) }))
        .sort((a, b) => b.score - a.score).slice(0, 5)
      const recent_decisions = [...NODES.values()].filter((n) => n.label === 'Decision')
        .map((n) => ({ key: n.id, summary: n.props.summary, status: n.props.status, decided_at: n.props.decided_at }))
      return json(res, 200, { counts, top_people, recent_decisions })
    }

    let m
    if ((m = path.match(/^\/api\/memory\/([^/]+)\/facts\/(.+)$/)) && method === 'DELETE') {
      const mem = memoryFor(decodeURIComponent(m[1]))
      const key = decodeURIComponent(m[2])
      const before = mem.facts.length
      mem.facts = mem.facts.filter((f) => f.key !== key)
      return json(res, before === mem.facts.length ? 404 : 200, { ok: before !== mem.facts.length })
    }
    if ((m = path.match(/^\/api\/memory\/([^/]+)$/)) && method === 'GET') {
      const mem = memoryFor(decodeURIComponent(m[1]))
      return json(res, 200, mem)
    }

    if (method === 'GET' && path === '/api/tasks') {
      const limit = Number(url.searchParams.get('limit') || 50)
      const list = [...TASKS].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, limit)
      return json(res, 200, list)
    }

    if (method === 'POST' && path === '/api/ingest') {
      const body = await readBody(req)
      const job_id = randomUUID()
      const job = { status: 'running', counts: {}, log: [`syncing ${body.repo}`] }
      INGEST.set(job_id, job)
      setTimeout(() => { job.status = 'done'; job.counts = { Issue: 12, PullRequest: 9, File: 4 }; job.log.push('done') }, FAST ? 0 : 3000)
      return json(res, 200, { job_id })
    }
    if ((m = path.match(/^\/api\/ingest\/([^/]+)$/)) && method === 'GET') {
      const job = INGEST.get(m[1])
      return job ? json(res, 200, job) : json(res, 404, { detail: 'unknown job' })
    }

    json(res, 404, { detail: `no route ${method} ${path}` })
  } catch (err) {
    json(res, 500, { detail: String(err?.message || err) })
  }
})

server.listen(PORT, () => {
  console.log(`TraceBrain mock backend on http://localhost:${PORT} (${NODES.size} nodes, ${EDGES.length} edges)`)
})
