import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, streamChat } from './api'
import type { ChatEvent, ChatMessage, GNode, Health, Overview, Task, TriagePlan } from './types'
import { useEvidenceGraph, type VNode } from './hooks/useEvidenceGraph'
import { useInterval, useLocalStorage } from './hooks/useDom'
import { Header } from './components/Header'
import { ChatPane, EXAMPLES } from './components/ChatPane'
import { GraphPane } from './components/GraphPane'
import { RightPanel, type RightTab } from './components/RightPanel'
import { Icon } from './components/Icon'

const uid = () => Math.random().toString(36).slice(2, 10)
type View = 'chat' | 'graph' | 'panel'

export default function App() {
  const [userId, setUserId] = useLocalStorage('tracebrain.user', 'aryan')
  const [users, setUsers] = useLocalStorage<string[]>('tracebrain.users', ['aryan', 'demo'])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const graph = useEvidenceGraph()
  const [focus, setFocus] = useState<{ key: string; nonce: number } | null>(null)
  const [fitNonce, setFitNonce] = useState(0)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [expanding, setExpanding] = useState<string | null>(null)

  const [tab, setTab] = useState<RightTab>('memory')
  const [triage, setTriage] = useState<TriagePlan | null>(null)
  const [triageUnseen, setTriageUnseen] = useState(false)
  const [memTick, setMemTick] = useState(0)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const [recalled, setRecalled] = useState<Set<string>>(new Set())
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [tasksError, setTasksError] = useState<string | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState(false)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [view, setView] = useState<View>('chat')

  // --- polling -------------------------------------------------------------
  const loadTasks = useCallback(() => {
    api
      .tasks(50)
      .then((t) => {
        setTasks(Array.isArray(t) ? t : [])
        setTasksError(null)
      })
      .catch((e) => setTasksError(String(e.message || e)))
  }, [])
  useInterval(loadTasks, 2000)
  useInterval(() => {
    api
      .health()
      .then((h) => {
        setHealth(h)
        setHealthError(false)
      })
      .catch(() => setHealthError(true))
  }, 10000)
  useEffect(() => {
    if (!health?.ok) return
    api.overview().then(setOverview).catch(() => setOverview(null))
  }, [health?.ok])

  useEffect(() => {
    if (tab === 'triage') setTriageUnseen(false)
    if (tab === 'memory') {
      const t = setTimeout(() => setFresh(new Set()), 8000)
      return () => clearTimeout(t)
    }
  }, [tab, memTick])

  // --- derived -------------------------------------------------------------
  const active = messages.find((m) => m.id === activeId)
  const highlight = useMemo(() => active?.highlight ?? [], [active?.highlight])
  const cited = useMemo(() => (active?.citations ?? []).map((c) => c.id), [active?.citations])

  const resolve = useCallback(
    (key: string): GNode | undefined => {
      const n = graph.get(key)
      if (n) return n
      for (const m of messages) {
        const c = m.citations.find((x) => x.id === key)
        if (c) return c
      }
      return undefined
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph.version, messages],
  )

  // --- graph actions -------------------------------------------------------
  const protect = useCallback(() => [...highlight, ...cited], [highlight, cited])

  const expand = useCallback(
    async (key: string) => {
      setExpanding(key)
      try {
        const r = await api.node(key, 1)
        graph.merge(r.nodes ?? [], r.edges ?? [], [...protect(), key])
        graph.markExpanded(key)
      } catch (e) {
        console.warn('expand failed', key, e)
      } finally {
        setExpanding(null)
      }
    },
    [graph, protect],
  )

  const focusKey = useCallback(
    async (key: string) => {
      if (window.matchMedia('(max-width: 1099px)').matches) setView('graph')
      if (!graph.get(key)) {
        try {
          const r = await api.node(key, 1)
          graph.merge(r.nodes ?? [], r.edges ?? [], [...protect(), key])
          graph.markExpanded(key)
        } catch {
          const n = resolve(key)
          if (n) graph.merge([n], [])
        }
      }
      setSelectedKey(key)
      setFocus({ key, nonce: Date.now() })
    },
    [graph, protect, resolve],
  )

  const onNodeClick = useCallback(
    (n: VNode) => {
      setSelectedKey(n.id)
      if (!n.expanded) expand(n.id)
    },
    [expand],
  )

  // --- chat ----------------------------------------------------------------
  const send = useCallback(
    async (text: string) => {
      if (streaming) return
      const aid = uid()
      const now = Date.now()
      const user: ChatMessage = { id: uid(), role: 'user', text, steps: [], agents: [], highlight: [], citations: [], memory: { written: [], recalled: [] }, status: 'done', startedAt: now }
      const asst: ChatMessage = { id: aid, role: 'assistant', text: '', steps: [], agents: [], highlight: [], citations: [], memory: { written: [], recalled: [] }, status: 'streaming', startedAt: now }
      setMessages((m) => [...m, user, asst])
      setActiveId(aid)
      setSelectedKey(null)
      setStreaming(true)
      const update = (fn: (m: ChatMessage) => ChatMessage) => setMessages((ms) => ms.map((m) => (m.id === aid ? fn(m) : m)))
      const turnHl: string[] = []
      const turnCites: string[] = []
      const addAgent = (a: string | undefined) => {
        if (!a) return
        const name = a.toLowerCase()
        update((m) => (m.agents.includes(name) ? m : { ...m, agents: [...m.agents, name] }))
      }

      const onEvent = (ev: ChatEvent) => {
        switch (ev.type) {
          case 'task':
            addAgent(ev.task?.agent)
            loadTasks()
            break
          case 'step':
            addAgent(ev.agent)
            update((m) => ({ ...m, steps: [...m.steps, { agent: (ev.agent ?? '').toLowerCase(), tool: ev.tool, input: ev.input, summary: ev.summary, at: Date.now() }] }))
            break
          case 'subgraph': {
            for (const k of ev.highlight ?? []) if (!turnHl.includes(k)) turnHl.push(k)
            graph.merge(ev.nodes ?? [], ev.edges ?? [], [...turnHl, ...turnCites])
            update((m) => ({ ...m, highlight: [...turnHl] }))
            setFitNonce((n) => n + 1)
            break
          }
          case 'token':
            update((m) => ({ ...m, text: m.text + (ev.text ?? '') }))
            break
          case 'answer': {
            const cites = ev.citations ?? []
            turnCites.push(...cites.map((c) => c.id))
            // citations not already in the graph are fetched with their neighbours on click (focusKey),
            // rather than added here as disconnected nodes that would wreck the layout
            update((m) => ({ ...m, text: ev.text ?? m.text, citations: cites }))
            break
          }
          case 'triage':
            setTriage(ev.plan)
            setTab('triage')
            setTriageUnseen(true)
            update((m) => ({ ...m, hasTriage: true }))
            break
          case 'memory': {
            const written = ev.written ?? []
            const rec = ev.recalled ?? []
            if (written.length) setFresh((s) => new Set([...s, ...written.map((w) => w.key)]))
            if (rec.length) setRecalled(new Set(rec.map((r) => r.key)))
            update((m) => ({ ...m, memory: { written: [...m.memory.written, ...written], recalled: [...m.memory.recalled, ...rec] } }))
            setMemTick((t) => t + 1)
            break
          }
          case 'error':
            update((m) => ({ ...m, status: 'error', error: ev.message }))
            break
          case 'done':
            if (ev.session_id) setSessionId(ev.session_id)
            break
        }
      }

      const ctrl = new AbortController()
      abortRef.current = ctrl
      try {
        await streamChat({ user_id: userId, session_id: sessionId, message: text }, onEvent, ctrl.signal)
        update((m) => ({ ...m, status: m.status === 'error' ? 'error' : 'done', endedAt: Date.now() }))
      } catch (e) {
        const aborted = ctrl.signal.aborted
        const msg = aborted ? 'Stopped.' : `Couldn't reach the TraceBrain backend (${(e as Error).message}). Start it, or run \`npm run mock\`.`
        update((m) => ({ ...m, status: aborted ? 'done' : 'error', error: aborted ? undefined : msg, text: aborted && !m.text ? '_Stopped._' : m.text, endedAt: Date.now() }))
      } finally {
        abortRef.current = null
        setStreaming(false)
        loadTasks()
      }
    },
    [streaming, userId, sessionId, graph, loadTasks],
  )

  const stop = () => abortRef.current?.abort()

  const newSession = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setActiveId(null)
    setSessionId(null)
    setSelectedKey(null)
    setTriage(null)
    setTriageUnseen(false)
    setFresh(new Set())
    setRecalled(new Set())
    graph.clear()
    setView('chat')
  }, [graph])

  const switchUser = (u: string) => {
    if (!users.includes(u)) setUsers([...users, u])
    if (u !== userId) {
      setUserId(u)
      newSession()
    }
  }

  return (
    <div className="app" data-view={view}>
      <Header health={health} healthError={healthError} userId={userId} users={users} onUser={switchUser} onNewSession={newSession} sessionId={sessionId} />
      <main className="main">
        <ChatPane
          messages={messages}
          streaming={streaming}
          activeId={activeId}
          userId={userId}
          resolve={resolve}
          onSend={send}
          onStop={stop}
          onCite={focusKey}
          onShowPath={(id) => {
            setActiveId(id)
            setFitNonce((n) => n + 1)
            if (window.matchMedia('(max-width: 1099px)').matches) setView('graph')
          }}
        />
        <GraphPane
          data={graph.data}
          highlight={highlight}
          cited={cited}
          focus={focus}
          fitNonce={fitNonce}
          selectedKey={selectedKey}
          expanding={expanding}
          overview={overview}
          streaming={streaming}
          onNodeClick={onNodeClick}
          onFocus={focusKey}
          onCloseCard={() => setSelectedKey(null)}
          onExpand={expand}
          onClear={() => {
            graph.clear()
            setSelectedKey(null)
          }}
        />
        <RightPanel
          tab={tab}
          setTab={setTab}
          userId={userId}
          memTick={memTick}
          fresh={fresh}
          recalled={recalled}
          tasks={tasks}
          tasksError={tasksError}
          triage={triage}
          triageUnseen={triageUnseen}
          onFocus={focusKey}
          onTryTriage={() => send(EXAMPLES[3])}
        />
      </main>
      <nav className="mobile-nav" aria-label="Views">
        {(
          [
            ['chat', 'Chat', 'chat'],
            ['graph', 'Graph', 'graph'],
            ['panel', 'Insights', 'memory'],
          ] as const
        ).map(([v, label, icon]) => (
          <button key={v} className={view === v ? 'is-active' : ''} onClick={() => setView(v)}>
            <Icon name={icon} size={16} />
            {label}
            {v === 'graph' && streaming && <span className="pulse" />}
          </button>
        ))}
      </nav>
    </div>
  )
}
