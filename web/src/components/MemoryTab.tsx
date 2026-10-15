import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Fact, MemoryResponse } from '../types'
import { timeAgo } from '../theme'
import { AgentBadge } from './StepTimeline'
import { Icon } from './Icon'

const KINDS: { kind: string; title: string; hint: string }[] = [
  { kind: 'semantic', title: 'Semantic', hint: 'What the agents know about you and your work' },
  { kind: 'preference', title: 'Preferences', hint: 'How you like answers' },
  { kind: 'procedural', title: 'Procedures', hint: 'How your team does things' },
]

export function MemoryTab({ userId, tick, fresh, recalled }: { userId: string; tick: number; fresh: Set<string>; recalled: Set<string> }) {
  const [mem, setMem] = useState<MemoryResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .memory(userId)
      .then((m) => {
        setMem({ facts: m.facts ?? [], episodes: m.episodes ?? [], sessions: m.sessions ?? [] })
        setErr(null)
      })
      .catch((e) => setErr(String(e.message || e)))
  }, [userId])

  useEffect(() => {
    load()
  }, [load, tick])

  const del = async (f: Fact) => {
    setMem((m) => (m ? { ...m, facts: m.facts.filter((x) => x.key !== f.key) } : m))
    try {
      await api.deleteFact(userId, f.key)
    } catch {
      load()
    }
  }

  if (err && !mem) return <div className="tab-empty">Memory unavailable: {err}</div>
  if (!mem) return <div className="tab-empty muted">Loading memory…</div>

  const groups = [...KINDS]
  for (const f of mem.facts) if (!groups.some((g) => g.kind === f.kind)) groups.push({ kind: f.kind, title: f.kind, hint: '' })

  return (
    <div className="tab-body">
      <div className="tab-intro">
        <span className="mono">user:{userId}</span>
        <span className="muted small">
          {mem.facts.filter((f) => f.valid !== false).length} facts · {mem.episodes.length} episodes · {mem.sessions.length} sessions
        </span>
      </div>
      {groups.map((g) => {
        const facts = mem.facts.filter((f) => f.kind === g.kind)
        return (
          <div key={g.kind} className="group">
            <div className="group-head">
              <h4>{g.title}</h4>
              <span className="muted small">{facts.length || ''}</span>
            </div>
            {facts.length === 0 ? (
              <div className="group-empty">{g.hint ? `Nothing yet. ${g.hint}.` : 'Nothing yet.'}</div>
            ) : (
              <ul className="facts">
                {facts.map((f) => (
                  <li key={f.key} className={`fact ${f.valid === false ? 'is-invalid' : ''} ${fresh.has(f.key) ? 'is-fresh' : ''}`}>
                    <div className="fact-text">{f.text}</div>
                    <div className="fact-meta">
                      {fresh.has(f.key) && <span className="tag tag-accent">new</span>}
                      {recalled.has(f.key) && <span className="tag">recalled</span>}
                      {f.valid === false && <span className="tag tag-warn">superseded</span>}
                      {typeof f.confidence === 'number' && (
                        <span className="conf" title={`confidence ${Math.round(f.confidence * 100)}%`}>
                          <span className="conf-bar">
                            <span style={{ width: `${Math.round(f.confidence * 100)}%` }} />
                          </span>
                          <span className="mono">{Math.round(f.confidence * 100)}</span>
                        </span>
                      )}
                      <span className="muted small">{timeAgo(f.updated_at ?? f.created_at)}</span>
                      <button className="icon-btn fact-del" onClick={() => del(f)} aria-label="Delete fact" title="Forget this fact">
                        <Icon name="trash" />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}
      <div className="group">
        <div className="group-head">
          <h4>Recent episodes</h4>
        </div>
        {mem.episodes.length === 0 ? (
          <div className="group-empty">No episodes yet.</div>
        ) : (
          <ul className="episodes">
            {mem.episodes.slice(0, 8).map((e) => (
              <li key={e.key} className="episode">
                <div className="episode-q">{e.question}</div>
                {e.answer && <div className="episode-a">{e.answer}</div>}
                <div className="fact-meta">
                  {e.agent && <AgentBadge agent={e.agent} />}
                  <span className="muted small">{timeAgo(e.created_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
