// Shapes from SPEC.md (tool contract + HTTP API + SSE events).

export interface GNode {
  id: string
  label: string
  title: string
  url: string | null
  props: Record<string, unknown>
}

export interface GEdge {
  source: string
  target: string
  type: string
}

export interface TaskRef {
  key: string
  kind: string
  status: TaskStatus
  agent: string
}

export type TaskStatus = 'queued' | 'running' | 'done' | 'failed'

export interface Task extends TaskRef {
  input: unknown
  output: unknown
  created_at: string
  updated_at?: string
  handoffs: string[]
}

export interface MemoryItem {
  key: string
  text: string
  kind: FactKind | string
}

export type FactKind = 'semantic' | 'preference' | 'procedural'

export interface Fact extends MemoryItem {
  confidence?: number
  created_at?: string
  updated_at?: string
  valid?: boolean
}

export interface Episode {
  key: string
  question: string
  answer: string
  agent: string
  created_at: string
}

export interface MemoryResponse {
  facts: Fact[]
  episodes: Episode[]
  sessions: { key: string; started_at: string }[]
}

export interface Health {
  ok: boolean
  falkordb: boolean
  llm_configured: boolean
  tenant?: string
  mock?: boolean
}

export interface Overview {
  counts: Record<string, number>
  top_people: unknown[]
  recent_decisions: unknown[]
}

/** The triage plan shape is not pinned down in SPEC; every field is optional and normalized in TriageTab. */
export type TriagePlan = Record<string, unknown>

export type ChatEvent =
  | { type: 'task'; task: TaskRef }
  | { type: 'step'; agent: string; tool: string; input: unknown; summary: string }
  | { type: 'subgraph'; nodes: GNode[]; edges: GEdge[]; highlight?: string[] }
  | { type: 'token'; text: string }
  | { type: 'answer'; text: string; citations: GNode[] }
  | { type: 'triage'; plan: TriagePlan }
  | { type: 'memory'; written: MemoryItem[]; recalled: MemoryItem[] }
  | { type: 'error'; message: string }
  | { type: 'done'; session_id: string }

export interface Step {
  agent: string
  tool: string
  input: unknown
  summary: string
  at: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  steps: Step[]
  /** agents in the order they started working on this turn (from task + step events) */
  agents: string[]
  /** ordered list of highlighted keys accumulated across this turn's subgraph events */
  highlight: string[]
  citations: GNode[]
  memory: { written: MemoryItem[]; recalled: MemoryItem[] }
  status: 'streaming' | 'done' | 'error'
  error?: string
  hasTriage?: boolean
  startedAt: number
  endedAt?: number
}
