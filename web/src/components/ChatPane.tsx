import { useEffect, useRef, useState } from 'react'
import type { ChatMessage, GNode } from '../types'
import { AnswerText, CitationChip } from './AnswerText'
import { StepTimeline } from './StepTimeline'
import { Icon } from './Icon'

export const EXAMPLES = [
  'Who knows the most about the GraphRAG-SDK api/main.py facade, and who should review changes to it?',
  'Why does falkordb-py ship its own embedded FalkorDB server code instead of depending on falkordblite?',
  'What breaks if graphrag_sdk core/models.py changes?',
  'Triage: https://github.com/FalkorDB/GraphRAG-SDK/issues/330',
  'How is mikemikimike connected to the GraphRAG-SDK ontology store (storage/ontology_store.py)?',
  'What was I working on last time?',
]

interface Props {
  messages: ChatMessage[]
  streaming: boolean
  activeId: string | null
  userId: string
  resolve: (key: string) => GNode | undefined
  onSend: (text: string) => void
  onStop: () => void
  onCite: (key: string) => void
  onShowPath: (id: string) => void
}

export function ChatPane({ messages, streaming, activeId, userId, resolve, onSend, onStop, onCite, onShowPath }: Props) {
  const [draft, setDraft] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const stick = useRef(true)

  useEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [messages])

  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    if (!draft) {
      ta.style.height = ''
      return
    }
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
  }, [draft])

  const submit = (text = draft) => {
    const t = text.trim()
    if (!t || streaming) return
    onSend(t)
    setDraft('')
    stick.current = true
  }

  return (
    <section className="pane chat-pane" aria-label="Chat">
      <div
        className="chat-scroll"
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
        }}
      >
        {messages.length === 0 ? (
          <div className="chat-empty">
            <div className="chat-empty-mark">
              <Icon name="sparkle" size={18} />
            </div>
            <h2>Ask the company brain</h2>
            <p>
              Questions about people, code, and decisions across <span className="mono">FalkorDB</span> repos. Answers cite graph nodes, and the
              agents remember you between sessions.
            </p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="example" onClick={() => submit(ex)}>
                  <span className="example-kind">{ex.startsWith('Triage') ? 'Triage' : ex.startsWith('Who') ? 'Experts' : ex.startsWith('Why') ? 'Decision' : 'Blast radius'}</span>
                  <span className="example-text">{ex}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="msg msg-user">
                <div className="bubble">{m.text}</div>
              </div>
            ) : (
              <div key={m.id} className={`msg msg-assistant ${activeId === m.id ? 'is-active' : ''}`}>
                <StepTimeline steps={m.steps} agents={m.agents} streaming={m.status === 'streaming'} elapsed={m.endedAt ? m.endedAt - m.startedAt : undefined} />
                {m.text ? (
                  <AnswerText text={m.text} streaming={m.status === 'streaming'} resolve={resolve} onCite={onCite} />
                ) : m.status === 'streaming' ? (
                  <div className="thinking">
                    <span className="shimmer">{m.steps.length ? m.steps[m.steps.length - 1].summary || 'Working…' : 'Routing your question…'}</span>
                  </div>
                ) : null}
                {m.status === 'error' && <div className="error-box">{m.error}</div>}
                {m.status !== 'streaming' && (m.highlight.length > 0 || m.citations.length > 0) && (
                  <div className="msg-foot">
                    <button className={`btn btn-ghost btn-sm ${activeId === m.id ? 'is-on' : ''}`} onClick={() => onShowPath(m.id)} title="Highlight this answer's path in the graph">
                      <Icon name="path" /> {activeId === m.id ? 'Path shown' : 'Show path'}
                    </button>
                    {m.citations.length > 0 && (
                      <span className="muted small">
                        {m.citations.length} source{m.citations.length === 1 ? '' : 's'}
                      </span>
                    )}
                    {m.hasTriage && <span className="tag tag-accent">triage plan</span>}
                  </div>
                )}
                {(m.memory.written.length > 0 || m.memory.recalled.length > 0) && m.status !== 'streaming' && (
                  <div className="memory-note">
                    <Icon name="memory" size={12} />
                    {m.memory.recalled.length > 0 && <span>Recalled {m.memory.recalled.length}</span>}
                    {m.memory.written.length > 0 && (
                      <span>
                        Remembered: <em>{m.memory.written.map((w) => w.text).join('; ')}</em>
                      </span>
                    )}
                  </div>
                )}
                {m.status !== 'streaming' && m.citations.length > 0 && !/\[\[/.test(m.text) && (
                  <div className="cite-row">
                    {m.citations.map((c) => (
                      <CitationChip key={c.id} k={c.id} resolve={resolve} onCite={onCite} />
                    ))}
                  </div>
                )}
              </div>
            ),
          )
        )}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <textarea
          ref={taRef}
          value={draft}
          rows={1}
          placeholder={`Ask as ${userId}…`}
          title="Enter to send, Shift+Enter for a newline"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
          aria-label="Message"
        />
        {streaming ? (
          <button type="button" className="btn btn-icon btn-stop" onClick={onStop} aria-label="Stop">
            <Icon name="stop" />
          </button>
        ) : (
          <button type="submit" className="btn btn-icon btn-primary" disabled={!draft.trim()} aria-label="Send">
            <Icon name="send" />
          </button>
        )}
      </form>
    </section>
  )
}
