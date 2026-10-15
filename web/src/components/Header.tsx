import { useEffect, useRef, useState } from 'react'
import type { Health } from '../types'
import { Icon } from './Icon'

function HealthDot({ health, error }: { health: Health | null; error: boolean }) {
  let state = 'pending'
  let text = 'Checking backend…'
  if (error) {
    state = 'down'
    text = 'Backend unreachable at /api'
  } else if (health) {
    if (!health.ok || !health.falkordb) {
      state = 'down'
      text = 'FalkorDB unavailable'
    } else if (!health.llm_configured) {
      state = 'warn'
      text = 'FalkorDB connected · LLM key not configured'
    } else {
      state = 'ok'
      text = 'FalkorDB connected · LLM ready'
    }
  }
  return (
    <span className={`health health-${state}`} title={text} aria-label={text}>
      <span className="health-dot" />
      <span className="health-text">{state === 'ok' ? 'Live' : state === 'warn' ? 'No LLM' : state === 'down' ? 'Offline' : '…'}</span>
    </span>
  )
}

function UserSwitcher({ userId, users, onChange }: { userId: string; users: string[]; onChange: (u: string) => void }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const off = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', off)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', off)
      document.removeEventListener('keydown', esc)
    }
  }, [open])
  const pick = (u: string) => {
    const v = u.trim().toLowerCase().replace(/[^a-z0-9_.-]/g, '-')
    if (!v) return
    onChange(v)
    setDraft('')
    setOpen(false)
  }
  return (
    <div className="user-switcher" ref={ref}>
      <button className="btn btn-ghost user-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <span className="avatar avatar-sm">{userId.slice(0, 1).toUpperCase()}</span>
        <span className="user-id mono">{userId}</span>
        <Icon name="chevron" size={12} />
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-label">Ask as</div>
          {users.map((u) => (
            <button key={u} role="menuitemradio" aria-checked={u === userId} className={`menu-item ${u === userId ? 'is-on' : ''}`} onClick={() => pick(u)}>
              <span className="avatar avatar-sm">{u.slice(0, 1).toUpperCase()}</span>
              <span className="mono">{u}</span>
              {u === userId && <Icon name="check" size={12} />}
            </button>
          ))}
          <form
            className="menu-add"
            onSubmit={(e) => {
              e.preventDefault()
              pick(draft)
            }}
          >
            <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add user id…" aria-label="New user id" />
          </form>
        </div>
      )}
    </div>
  )
}

export function Header(props: {
  health: Health | null
  healthError: boolean
  userId: string
  users: string[]
  onUser: (u: string) => void
  onNewSession: () => void
  sessionId: string | null
}) {
  const tenant = props.health?.tenant ?? 'falkordb'
  return (
    <header className="header">
      <div className="brand">
        <svg className="logo" viewBox="0 0 24 24" width="22" height="22" aria-hidden>
          <path d="M5 17 L12 6 L19 15" stroke="var(--accent)" strokeWidth="1.8" fill="none" strokeLinecap="round" />
          <circle cx="5" cy="17" r="2.6" fill="var(--accent)" />
          <circle cx="12" cy="6" r="2.6" fill="var(--text)" />
          <circle cx="19" cy="15" r="2.6" fill="var(--accent)" />
        </svg>
        <span className="wordmark">
          Trace<span>Brain</span>
        </span>
      </div>
      <span className="sep" />
      <span className="tenant" title={`Tenant graph org_${tenant}`}>
        <span className="muted">tenant</span>
        <span className="mono">{tenant}</span>
      </span>
      <HealthDot health={props.health} error={props.healthError} />
      {props.health?.mock && <span className="tag" title="Served by web/mock/server.mjs">mock data</span>}
      <span className="spacer" />
      {props.sessionId && (
        <span className="session mono muted" title={props.sessionId}>
          {props.sessionId.replace(/^session:/, '').slice(0, 8)}
        </span>
      )}
      <UserSwitcher userId={props.userId} users={props.users} onChange={props.onUser} />
      <button className="btn btn-sm" onClick={props.onNewSession}>
        <Icon name="plus" /> <span className="hide-sm">New session</span>
      </button>
    </header>
  )
}
