// Node label palette: muted, distinguishable in both themes. One accent (indigo) is reserved for highlights.
export const LABEL_COLORS: Record<string, string> = {
  Person: '#f2a65a',
  Repo: '#9aa4b2',
  Module: '#6cc1a8',
  File: '#5aa9e6',
  Issue: '#e5707e',
  PullRequest: '#b28dff',
  Label: '#8a8f98',
  Topic: '#e6c35c',
  Decision: '#4fd1c5',
  User: '#c0c4cc',
  Session: '#9aa4b2',
  Episode: '#a3b18a',
  Fact: '#d4a5a5',
  Agent: '#7c8cff',
  Task: '#8090a8',
}
export const labelColor = (label: string) => LABEL_COLORS[label] ?? '#9aa4b2'

export const LABEL_ORDER = Object.keys(LABEL_COLORS)

export const AGENT_COLORS: Record<string, string> = {
  coordinator: '#7c8cff',
  navigator: '#5aa9e6',
  triage: '#e5707e',
  historian: '#e6c35c',
}
export const agentColor = (a: string) => AGENT_COLORS[a?.toLowerCase()] ?? '#9aa4b2'

export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s)

/** Short, human label for a node on the canvas / in chips. */
export function shortTitle(n: { id: string; label: string; title?: string; props?: Record<string, unknown> }): string {
  const p = n.props ?? {}
  switch (n.label) {
    case 'Person':
      return String(p.login ?? n.title ?? n.id.replace(/^person:/, ''))
    case 'File': {
      const path = String(p.path ?? n.title ?? n.id.split(':').pop())
      // parent/basename keeps same-named files (graph.py vs asyncio/graph.py) distinguishable
      return path.split('/').slice(-2).join('/') || path
    }
    case 'Module':
      return String(p.path ?? n.title ?? n.id.split(':').pop()) + '/'
    case 'Issue':
      return `#${p.number ?? n.id.split('#').pop()}`
    case 'PullRequest':
      return `PR #${p.number ?? n.id.split('#').pop()}`
    case 'Repo':
      return String(p.name ?? n.title ?? n.id)
    default: {
      const t = String(n.title || p.summary || p.name || n.id)
      return t.length > 34 ? t.slice(0, 32) + '…' : t
    }
  }
}

/** Guess a label from a key prefix (used when a citation key isn't in the graph yet). */
export function labelFromKey(key: string): string {
  const prefix = key.split(':')[0]
  const map: Record<string, string> = {
    person: 'Person', repo: 'Repo', module: 'Module', file: 'File', issue: 'Issue', pr: 'PullRequest',
    label: 'Label', topic: 'Topic', decision: 'Decision', user: 'User', session: 'Session', episode: 'Episode',
    fact: 'Fact', agent: 'Agent', task: 'Task',
  }
  return map[prefix] ?? 'Node'
}

export function timeAgo(iso?: string | number): string {
  if (!iso) return ''
  const t = typeof iso === 'number' ? iso : Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}
