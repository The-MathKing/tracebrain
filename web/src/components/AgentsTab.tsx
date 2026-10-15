import { useMemo } from 'react'
import type { Task } from '../types'
import { agentColor, cap, timeAgo } from '../theme'

const ROSTER = [
  { name: 'coordinator', role: 'Routes work, recalls memory' },
  { name: 'navigator', role: 'Answers with graph tools' },
  { name: 'triage', role: 'Plans issue triage' },
  { name: 'historian', role: 'Writes episodes and facts' },
]

const str = (v: unknown) => (v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v))

export function AgentsTab({ tasks, error }: { tasks: Task[] | null; error: string | null }) {
  const runs = useMemo(() => {
    if (!tasks) return []
    const byKey = new Map(tasks.map((t) => [t.key, t]))
    const child = new Set(tasks.flatMap((t) => t.handoffs ?? []))
    const roots = tasks.filter((t) => !child.has(t.key)).sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
    return roots.slice(0, 14).map((root) => {
      const chain: Task[] = []
      const seen = new Set<string>()
      const walk = (t: Task) => {
        if (seen.has(t.key)) return
        seen.add(t.key)
        chain.push(t)
        for (const h of t.handoffs ?? []) {
          const n = byKey.get(h)
          if (n) walk(n)
        }
      }
      walk(root)
      return chain
    })
  }, [tasks])

  const agentState = (name: string) => {
    const mine = (tasks ?? []).filter((t) => t.agent?.toLowerCase() === name)
    return {
      running: mine.some((t) => t.status === 'running'),
      queued: mine.filter((t) => t.status === 'queued').length,
      done: mine.filter((t) => t.status === 'done').length,
      failed: mine.filter((t) => t.status === 'failed').length,
    }
  }

  return (
    <div className="tab-body">
      <div className="roster">
        {ROSTER.map((a) => {
          const s = agentState(a.name)
          return (
            <div key={a.name} className={`agent-card ${s.running ? 'is-busy' : ''}`} style={{ ['--c' as string]: agentColor(a.name) }}>
              <div className="agent-card-head">
                <span className={`status-dot ${s.running ? 'running' : 'idle'}`} />
                <span className="agent-name">{cap(a.name)}</span>
              </div>
              <div className="muted small">{a.role}</div>
              <div className="agent-stats mono">
                {s.running ? 'working' : 'idle'} · {s.done} done{s.failed ? ` · ${s.failed} failed` : ''}
              </div>
            </div>
          )
        })}
      </div>

      <div className="group-head">
        <h4>Task board</h4>
        <span className="muted small">{error ? <span className="warn">offline</span> : 'live · 2s'}</span>
      </div>
      {!tasks ? (
        <div className="group-empty">{error ? `Can't load tasks: ${error}` : 'Loading…'}</div>
      ) : runs.length === 0 ? (
        <div className="group-empty">No tasks yet. Ask something and watch the handoffs.</div>
      ) : (
        <ul className="runs">
          {runs.map((chain) => {
            const root = chain[0]
            const live = chain.some((t) => t.status === 'running' || t.status === 'queued')
            return (
              <li key={root.key} className={`run ${live ? 'is-live' : ''}`}>
                <div className="run-head">
                  <span className="run-input" title={str(root.input)}>{str(root.input) || root.kind}</span>
                  <span className="muted small">{timeAgo(root.created_at)}</span>
                </div>
                <div className="handoffs">
                  {chain.map((t, i) => (
                    <span key={t.key} className="handoff">
                      {i > 0 && <span className="handoff-arrow">→</span>}
                      <span className={`task-pill st-${t.status}`} style={{ ['--c' as string]: agentColor(t.agent) }} title={`${t.key}\n${t.kind} · ${t.status}${t.output ? `\n→ ${str(t.output)}` : ''}`}>
                        <span className={`status-dot ${t.status}`} />
                        <span className="task-agent">{cap(t.agent)}</span>
                        <span className="task-kind mono">{t.kind}</span>
                      </span>
                    </span>
                  ))}
                </div>
                {chain[chain.length - 1].output != null && (
                  <div className="run-output muted small">→ {str(chain.filter((t) => t.output != null).slice(-1)[0]?.output)}</div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
