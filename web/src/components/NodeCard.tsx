import type { VNode } from '../hooks/useEvidenceGraph'
import { labelColor } from '../theme'
import { Icon } from './Icon'
import { CopyButton } from './CopyButton'

const HIDE = new Set(['key', 'avatar_url'])

function fmt(v: unknown): string {
  if (v == null) return '—'
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
  if (typeof v === 'object') return JSON.stringify(v)
  const s = String(v)
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) return s.slice(0, 10)
  return s
}

export function NodeCard({ node, degree, expanding, onClose, onExpand, onFocus }: {
  node: VNode
  degree: number
  expanding: boolean
  onClose: () => void
  onExpand: () => void
  onFocus: () => void
}) {
  const entries = Object.entries(node.props ?? {}).filter(([k, v]) => !HIDE.has(k) && v !== '' && v != null)
  const long = entries.filter(([, v]) => typeof v === 'string' && v.length > 90)
  const short = entries.filter(([, v]) => !(typeof v === 'string' && v.length > 90))
  return (
    <aside className="node-card" aria-label="Node details">
      <header>
        <span className="badge" style={{ ['--c' as string]: labelColor(node.label) }}>
          <span className="dot" style={{ background: labelColor(node.label) }} />
          {node.label}
        </span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          <Icon name="close" />
        </button>
      </header>
      <h4 className="node-card-title">{node.title || node.id}</h4>
      <div className="node-card-key">
        <code className="mono">{node.id}</code>
        <CopyButton text={node.id} small />
      </div>
      {short.length > 0 && (
        <dl className="props">
          {short.map(([k, v]) => (
            <div key={k} className="prop">
              <dt>{k}</dt>
              <dd className={typeof v === 'number' ? 'mono' : ''}>{fmt(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {long.map(([k, v]) => (
        <div key={k} className="prop-long">
          <div className="prop-long-k">{k}</div>
          <p>{String(v).length > 320 ? String(v).slice(0, 318) + '…' : String(v)}</p>
        </div>
      ))}
      <footer>
        <button className="btn btn-sm" onClick={onExpand} disabled={expanding || node.expanded}>
          <Icon name="expand" /> {expanding ? 'Expanding…' : node.expanded ? 'Expanded' : 'Expand neighbors'}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onFocus} title="Center on node">
          <Icon name="focus" />
        </button>
        <span className="muted small">{degree} links</span>
        {node.url && (
          <a className="btn btn-ghost btn-sm push" href={node.url} target="_blank" rel="noreferrer">
            Open on GitHub <Icon name="external" />
          </a>
        )}
      </footer>
    </aside>
  )
}
