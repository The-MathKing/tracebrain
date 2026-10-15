import { useState } from 'react'
import type { Step } from '../types'
import { agentColor, cap } from '../theme'
import { Icon } from './Icon'

export function AgentBadge({ agent }: { agent: string }) {
  return (
    <span className="agent-badge" style={{ ['--c' as string]: agentColor(agent) }}>
      {cap(agent)}
    </span>
  )
}

function inputPreview(input: unknown): string {
  if (input == null) return ''
  if (typeof input === 'string') return input
  try {
    const s = JSON.stringify(input)
    return s.length > 140 ? s.slice(0, 138) + '…' : s
  } catch {
    return ''
  }
}

export function StepTimeline({ steps, agents, streaming, elapsed }: { steps: Step[]; agents: string[]; streaming: boolean; elapsed?: number }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const open = userOpen ?? streaming
  if (!steps.length && !agents.length) return null
  const tools = steps.filter((s) => s.tool && s.tool !== 'handoff').length
  return (
    <div className={`timeline ${open ? 'is-open' : ''}`}>
      <button className="timeline-head" onClick={() => setUserOpen(!open)} aria-expanded={open}>
        <span className={`chev ${open ? 'open' : ''}`}>
          <Icon name="chevron" size={12} />
        </span>
        <span className="agent-chain">
          {agents.map((a, i) => (
            <span key={a + i} className="chain-item">
              {i > 0 && <span className="chain-arrow">→</span>}
              <AgentBadge agent={a} />
            </span>
          ))}
        </span>
        <span className="timeline-meta mono">
          {tools} tool call{tools === 1 ? '' : 's'}
          {elapsed != null && ` · ${(elapsed / 1000).toFixed(1)}s`}
        </span>
        {streaming && <span className="pulse" />}
      </button>
      {open && (
        <ol className="steps">
          {steps.map((s, i) => (
            <li key={i} className={`step ${s.tool === 'handoff' ? 'is-handoff' : ''} ${streaming && i === steps.length - 1 ? 'is-live' : ''}`}>
              <span className="step-rail" style={{ ['--c' as string]: agentColor(s.agent) }} />
              <div className="step-body">
                <div className="step-head">
                  <AgentBadge agent={s.agent} />
                  {s.tool && <code className="tool mono">{s.tool}</code>}
                </div>
                {s.summary && <div className="step-summary">{s.summary}</div>}
                {s.input != null && s.tool !== 'handoff' && <div className="step-input mono" title={inputPreview(s.input)}>{inputPreview(s.input)}</div>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
