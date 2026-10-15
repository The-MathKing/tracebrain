import type { TriagePlan } from '../types'
import { labelColor } from '../theme'
import { CopyButton } from './CopyButton'
import { Icon } from './Icon'

// The SPEC leaves the triage plan's shape open, so normalize defensively:
// people / files / issues may arrive as plain strings or as objects with varying field names.

interface Person { key?: string; login: string; name?: string; reason?: string; score?: number }
interface FileRef { key?: string; path: string; depth?: number; owners: string[]; reason?: string }
interface Dup { key?: string; title: string; url?: string; number?: number; state?: string; similarity?: number }

const asArr = (v: unknown): unknown[] => (Array.isArray(v) ? v : v == null ? [] : [v])
const obj = (v: unknown) => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {})
const s = (v: unknown) => (v == null ? undefined : String(v))
const num = (v: unknown) => (typeof v === 'number' ? v : undefined)

function person(v: unknown): Person | null {
  if (!v) return null
  if (typeof v === 'string') return { login: v.replace(/^person:/, ''), key: v.startsWith('person:') ? v : `person:${v}` }
  const o = obj(v)
  const key = s(o.key ?? o.id)
  const login = s(o.login ?? o.name ?? key?.replace(/^person:/, '')) ?? '?'
  return { key: key ?? `person:${login}`, login, name: s(o.name), reason: s(o.reason ?? o.why ?? o.rationale), score: num(o.score) }
}
function fileRef(v: unknown): FileRef {
  if (typeof v === 'string') return { key: v.startsWith('file:') ? v : undefined, path: v.replace(/^file:[^:]+:/, ''), owners: [] }
  const o = obj(v)
  const key = s(o.key ?? o.id)
  return {
    key,
    path: s(o.path ?? o.title ?? key?.replace(/^(file|module):[^:]+:/, '')) ?? '?',
    depth: num(o.depth ?? o.distance),
    owners: asArr(o.owners).map((x) => (typeof x === 'string' ? x.replace(/^person:/, '') : s(obj(x).login) ?? '')).filter(Boolean),
    reason: s(o.reason),
  }
}
function dup(v: unknown): Dup {
  if (typeof v === 'string') return { key: v, title: v }
  const o = obj(v)
  const key = s(o.key ?? o.id)
  return { key, title: s(o.title) ?? key ?? '?', url: s(o.url), number: num(o.number), state: s(o.state), similarity: num(o.similarity ?? o.score) }
}

export function normalizePlan(p: TriagePlan) {
  const issue = obj(p.issue)
  const issueKey = s(issue.key ?? p.issue_key ?? (typeof p.issue === 'string' ? p.issue : undefined))
  return {
    issueKey,
    issueTitle: s(issue.title ?? p.title) ?? issueKey ?? 'Issue',
    issueUrl: s(issue.url ?? p.issue_url ?? p.url),
    issueNumber: num(issue.number) ?? (issueKey?.match(/#(\d+)/) ? Number(issueKey.match(/#(\d+)/)![1]) : undefined),
    repo: s(issue.repo),
    summary: s(p.summary ?? p.analysis),
    priority: s(p.priority),
    assignee: person(p.assignee ?? p.owner ?? p.recommended_assignee),
    reviewers: asArr(p.reviewers).map(person).filter(Boolean) as Person[],
    labels: asArr(p.labels ?? p.suggested_labels).map((l) => (typeof l === 'string' ? l : s(obj(l).name) ?? '')).filter(Boolean),
    related: asArr(p.related_files ?? p.files ?? p.related_modules).map(fileRef),
    blast: asArr(p.blast_radius ?? p.impact).map(fileRef),
    duplicates: asArr(p.duplicates ?? p.duplicate_candidates ?? p.related_work).map(dup),
    comment: s(p.draft_comment ?? p.comment ?? p.draft) ?? '',
  }
}

function PersonRow({ p, role, onFocus }: { p: Person; role: string; onFocus: (k: string) => void }) {
  return (
    <div className="person-row">
      <button className="avatar" style={{ background: labelColor('Person') }} onClick={() => p.key && onFocus(p.key)} title={p.key}>
        {(p.name ?? p.login).slice(0, 1).toUpperCase()}
      </button>
      <div className="person-main">
        <div className="person-line">
          <button className="link-btn" onClick={() => p.key && onFocus(p.key)}>
            <span className="person-name">{p.name ?? p.login}</span>
            <span className="mono muted">@{p.login}</span>
          </button>
          <span className="tag">{role}</span>
          {p.score != null && (
            <span className="conf" title={`expertise score ${p.score.toFixed(2)}`}>
              <span className="conf-bar">
                <span style={{ width: `${Math.round(Math.min(1, p.score) * 100)}%` }} />
              </span>
              <span className="mono">{p.score.toFixed(2)}</span>
            </span>
          )}
        </div>
        {p.reason && <div className="person-reason">{p.reason}</div>}
      </div>
    </div>
  )
}

export function TriageTab({ plan, onFocus, onTry }: { plan: TriagePlan | null; onFocus: (key: string) => void; onTry: () => void }) {
  if (!plan) {
    return (
      <div className="tab-empty">
        <Icon name="triage" size={20} />
        <p>No triage plan yet. Paste a GitHub issue URL starting with “Triage:” and the Triage agent will propose an owner, reviewers, blast radius, and a draft comment.</p>
        <button className="btn btn-sm" onClick={onTry}>Try triaging GraphRAG-SDK#330</button>
      </div>
    )
  }
  const t = normalizePlan(plan)
  return (
    <div className="tab-body triage">
      <div className="issue-card">
        <div className="issue-top">
          <span className="badge" style={{ ['--c' as string]: labelColor('Issue') }}>
            <span className="dot" style={{ background: labelColor('Issue') }} />
            Issue{t.issueNumber != null ? ` #${t.issueNumber}` : ''}
          </span>
          {t.priority && <span className="tag tag-warn">{t.priority}</span>}
          {t.issueUrl && (
            <a className="icon-link push" href={t.issueUrl} target="_blank" rel="noreferrer">
              GitHub <Icon name="external" size={12} />
            </a>
          )}
        </div>
        <button className="issue-title link-btn" onClick={() => t.issueKey && onFocus(t.issueKey)}>{t.issueTitle}</button>
        {t.repo && <div className="mono muted small">{t.repo}</div>}
        {t.summary && <p className="issue-summary">{t.summary}</p>}
        {t.labels.length > 0 && (
          <div className="labels">
            {t.labels.map((l) => (
              <span key={l} className="gh-label">{l}</span>
            ))}
          </div>
        )}
      </div>

      <div className="group">
        <div className="group-head"><h4>Owners</h4></div>
        {t.assignee && <PersonRow p={t.assignee} role="assignee" onFocus={onFocus} />}
        {t.reviewers.map((r) => (
          <PersonRow key={r.login} p={r} role="reviewer" onFocus={onFocus} />
        ))}
        {!t.assignee && t.reviewers.length === 0 && <div className="group-empty">No owners suggested.</div>}
      </div>

      {t.related.length > 0 && (
        <div className="group">
          <div className="group-head"><h4>Related code</h4></div>
          <ul className="file-list">
            {t.related.map((f) => (
              <li key={f.path}>
                <button className="link-btn mono" onClick={() => f.key && onFocus(f.key)}>{f.path}</button>
                {f.reason && <span className="muted small">{f.reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="group">
        <div className="group-head">
          <h4>Blast radius</h4>
          <span className="muted small">{t.blast.length} affected</span>
        </div>
        {t.blast.length === 0 ? (
          <div className="group-empty">Nothing downstream.</div>
        ) : (
          <ul className="file-list">
            {t.blast.map((f) => (
              <li key={f.path}>
                {f.depth != null && <span className="depth mono" title="import distance">d{f.depth}</span>}
                <button className="link-btn mono" onClick={() => f.key && onFocus(f.key)}>{f.path}</button>
                {f.owners.length > 0 && <span className="muted small">@{f.owners.join(', @')}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="group">
        <div className="group-head"><h4>Possible duplicates</h4></div>
        {t.duplicates.length === 0 ? (
          <div className="group-empty">No similar issues found.</div>
        ) : (
          <ul className="dups">
            {t.duplicates.map((d) => (
              <li key={d.key ?? d.title}>
                <button className="link-btn" onClick={() => d.key && onFocus(d.key)}>
                  {d.number != null && <span className="mono muted">#{d.number}</span>} {d.title}
                </button>
                <span className="dup-meta">
                  {d.state && <span className={`tag ${d.state === 'open' ? 'tag-ok' : ''}`}>{d.state}</span>}
                  {d.similarity != null && <span className="mono small">{Math.round(d.similarity * 100)}%</span>}
                  {d.url && (
                    <a className="icon-link" href={d.url} target="_blank" rel="noreferrer" aria-label="Open on GitHub">
                      <Icon name="external" size={12} />
                    </a>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {t.comment && (
        <div className="group">
          <div className="group-head">
            <h4>Draft comment</h4>
            <CopyButton text={t.comment} label="Copy" />
          </div>
          <pre className="draft">{t.comment}</pre>
          <div className="muted small draft-note">Draft only. TraceBrain never posts to GitHub on its own.</div>
        </div>
      )}
    </div>
  )
}
