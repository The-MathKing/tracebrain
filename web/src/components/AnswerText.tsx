import { Fragment, type ReactNode } from 'react'
import type { GNode } from '../types'
import { labelColor, labelFromKey, shortTitle } from '../theme'

type Resolve = (key: string) => GNode | undefined

export function CitationChip({ k, resolve, onCite }: { k: string; resolve: Resolve; onCite: (key: string) => void }) {
  const n = resolve(k)
  const label = n?.label ?? labelFromKey(k)
  const text = n ? shortTitle(n) : shortTitle({ id: k, label, title: k.split(/[:#]/).pop() })
  return (
    <button className="chip chip-cite" onClick={() => onCite(k)} title={`${label} · ${k}`}>
      <span className="dot" style={{ background: labelColor(label) }} />
      {text}
    </button>
  )
}

const INLINE = /(\[\[[^\]]+\]\]|`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:[^)]+\))/g

function inline(text: string, resolve: Resolve, onCite: (k: string) => void): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let i = 0
  for (const m of text.matchAll(INLINE)) {
    const tok = m[0]
    if (m.index! > last) out.push(text.slice(last, m.index))
    const key = `t${i++}`
    if (tok.startsWith('[[')) out.push(<CitationChip key={key} k={tok.slice(2, -2).trim()} resolve={resolve} onCite={onCite} />)
    else if (tok.startsWith('`')) out.push(<code key={key} className="inline-code">{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('**')) out.push(<strong key={key}>{inline(tok.slice(2, -2), resolve, onCite)}</strong>)
    else {
      const mm = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/)!
      out.push(<a key={key} href={mm[2]} target="_blank" rel="noreferrer">{mm[1]}</a>)
    }
    last = m.index! + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

/** Minimal markdown: paragraphs, bullet/numbered lists, blockquotes, headings, fenced code, plus [[key]] citation chips. */
export function AnswerText({ text, streaming, resolve, onCite }: { text: string; streaming: boolean; resolve: Resolve; onCite: (k: string) => void }) {
  let src = text
  if (streaming) src = src.replace(/\[\[[^\]]*$/, '') // hide a half-streamed citation
  const blocks: ReactNode[] = []
  const lines = src.split('\n')
  let i = 0
  let b = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }
    if (line.startsWith('```')) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++])
      i++
      blocks.push(<pre key={b++} className="code-block">{body.join('\n')}</pre>)
      continue
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: string[] = []
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''))
        i++
      }
      const Tag = ordered ? 'ol' : 'ul'
      blocks.push(<Tag key={b++}>{items.map((it, j) => <li key={j}>{inline(it, resolve, onCite)}</li>)}</Tag>)
      continue
    }
    if (line.startsWith('>')) {
      const q: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) q.push(lines[i++].replace(/^>\s?/, ''))
      blocks.push(<blockquote key={b++}>{inline(q.join(' '), resolve, onCite)}</blockquote>)
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      blocks.push(<h5 key={b++} className="md-h">{inline(h[2], resolve, onCite)}</h5>)
      i++
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(\s*([-*]|\d+\.)\s+|>|```|#{1,4}\s)/.test(lines[i])) para.push(lines[i++])
    blocks.push(
      <p key={b++}>
        {para.map((p, j) => (
          <Fragment key={j}>
            {j > 0 && <br />}
            {inline(p, resolve, onCite)}
          </Fragment>
        ))}
      </p>,
    )
  }
  return (
    <div className={`answer ${streaming ? 'is-streaming' : ''}`}>
      {blocks}
      {streaming && <span className="caret" aria-hidden />}
    </div>
  )
}
