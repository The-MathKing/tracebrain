import type { ReactNode } from 'react'
import type { Task, TriagePlan } from '../types'
import { AgentsTab } from './AgentsTab'
import { MemoryTab } from './MemoryTab'
import { TriageTab } from './TriageTab'
import { Icon } from './Icon'

export type RightTab = 'memory' | 'agents' | 'triage'

export function RightPanel(props: {
  tab: RightTab
  setTab: (t: RightTab) => void
  userId: string
  memTick: number
  fresh: Set<string>
  recalled: Set<string>
  tasks: Task[] | null
  tasksError: string | null
  triage: TriagePlan | null
  triageUnseen: boolean
  onFocus: (key: string) => void
  onTryTriage: () => void
}) {
  const running = (props.tasks ?? []).filter((t) => t.status === 'running' || t.status === 'queued').length
  const tabs: { id: RightTab; label: string; icon: string; badge?: ReactNode }[] = [
    { id: 'memory', label: 'Memory', icon: 'memory', badge: props.fresh.size ? <span className="tab-dot" /> : null },
    { id: 'agents', label: 'Agents', icon: 'agents', badge: running ? <span className="tab-count mono">{running}</span> : null },
    { id: 'triage', label: 'Triage', icon: 'triage', badge: props.triageUnseen ? <span className="tab-dot" /> : null },
  ]
  return (
    <section className="pane right-pane" aria-label="Insights">
      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} role="tab" aria-selected={props.tab === t.id} className={`tab ${props.tab === t.id ? 'is-active' : ''}`} onClick={() => props.setTab(t.id)}>
            <Icon name={t.icon} />
            {t.label}
            {t.badge}
          </button>
        ))}
      </div>
      <div className="tab-scroll">
        {props.tab === 'memory' && <MemoryTab userId={props.userId} tick={props.memTick} fresh={props.fresh} recalled={props.recalled} />}
        {props.tab === 'agents' && <AgentsTab tasks={props.tasks} error={props.tasksError} />}
        {props.tab === 'triage' && <TriageTab plan={props.triage} onFocus={props.onFocus} onTry={props.onTryTriage} />}
      </div>
    </section>
  )
}
