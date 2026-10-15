import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d'
import type { VLink, VNode } from '../hooks/useEvidenceGraph'
import { LABEL_ORDER, labelColor, shortTitle } from '../theme'
import { useColorScheme, useElementSize } from '../hooks/useDom'
import type { Overview } from '../types'
import { NodeCard } from './NodeCard'
import { Icon } from './Icon'

interface Props {
  data: { nodes: VNode[]; links: VLink[] }
  highlight: string[]
  cited: string[]
  focus: { key: string; nonce: number } | null
  fitNonce: number
  selectedKey: string | null
  expanding: string | null
  overview: Overview | null
  streaming: boolean
  onNodeClick: (n: VNode) => void
  onFocus: (key: string) => void
  onCloseCard: () => void
  onExpand: (key: string) => void
  onClear: () => void
}

const RADIUS: Record<string, number> = {
  Person: 6.5, Decision: 6.5, Repo: 7.5, Module: 5.5, Topic: 5, File: 4.2, Issue: 4.6, PullRequest: 4.6, Label: 3.2,
}

function palette(dark: boolean) {
  return dark
    ? { text: '#e7e9ee', muted: 'rgba(200,206,220,0.55)', line: 'rgba(150,160,185,0.22)', lineDim: 'rgba(150,160,185,0.06)', accent: '#8b97ff', labelBg: 'rgba(13,15,21,0.86)', ring: '#ffffff' }
    : { text: '#151924', muted: 'rgba(60,68,85,0.6)', line: 'rgba(70,80,100,0.22)', lineDim: 'rgba(70,80,100,0.07)', accent: '#4f5bd5', labelBg: 'rgba(255,255,255,0.92)', ring: '#151924' }
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)

export function GraphPane(props: Props) {
  const { data, highlight, cited, focus, fitNonce, selectedKey } = props
  const fgRef = useRef<ForceGraphMethods<VNode, VLink> | undefined>(undefined)
  const [wrapRef, size] = useElementSize<HTMLDivElement>()
  const dark = useColorScheme() === 'dark'
  const pal = useMemo(() => palette(dark), [dark])
  const [hover, setHover] = useState<string | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [pathOnly, setPathOnly] = useState(false)

  const hl = useMemo(() => new Set(highlight), [highlight])
  const citedSet = useMemo(() => new Set(cited), [cited])
  // nodes the camera should frame after new evidence: the answer's path + citations
  const activeFitRef = useRef<Set<string>>(new Set())
  const fitPendingRef = useRef(false)
  activeFitRef.current = hl.size >= 2 ? hl : citedSet

  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>()
    for (const l of data.links) {
      if (!m.has(l.s)) m.set(l.s, new Set())
      if (!m.has(l.t)) m.set(l.t, new Set())
      m.get(l.s)!.add(l.t)
      m.get(l.t)!.add(l.s)
    }
    return m
  }, [data])

  // "Active" nodes stay bright; everything else fades. Hover temporarily wins.
  const active = useMemo(() => {
    if (hover) return new Set([hover, ...(adjacency.get(hover) ?? [])])
    if (hl.size) return new Set([...hl, ...citedSet, ...(selectedKey ? [selectedKey, ...(adjacency.get(selectedKey) ?? [])] : [])])
    return null
  }, [hover, adjacency, hl, citedSet, selectedKey])


  // Like zoomToFit, but leaves room for the path strip + legend overlaid at the bottom.
  const sizeRef = useRef(size)
  sizeRef.current = size
  const fitTo = useCallback((keep: Set<string>, ms: number) => {
    const fg = fgRef.current
    const { width, height } = sizeRef.current
    if (!fg || !width || !height) return
    const filter = keep.size >= 2 ? (n: VNode) => keep.has(n.id) : undefined
    const bb = fg.getGraphBbox(filter)
    if (!bb || !Number.isFinite(bb.x[0])) return
    const bottom = hlRef.current ? Math.min(150, height * 0.3) : 50
    const pad = 56
    const bw = Math.max(bb.x[1] - bb.x[0], 40)
    const bh = Math.max(bb.y[1] - bb.y[0], 40)
    const k = Math.min((width - pad * 2) / bw, (height - pad - bottom - 20) / bh, 4)
    const cx = (bb.x[0] + bb.x[1]) / 2
    const cy = (bb.y[0] + bb.y[1]) / 2 + (bottom - pad + 20) / 2 / k
    fg.centerAt(cx, cy, ms)
    fg.zoom(k, ms)
  }, [])
  const hlRef = useRef(false)
  hlRef.current = hl.size > 0

  const isPathLink = useCallback((l: VLink) => hl.has(l.s) && hl.has(l.t), [hl])

  // Forces: a little more spread than the defaults, distance shorter for structural edges.
  useEffect(() => {
    const fg = fgRef.current
    if (!fg) return
    const charge = fg.d3Force('charge')
    charge?.strength?.(-150)
    charge?.distanceMax?.(320)
    const link = fg.d3Force('link')
    link?.distance?.((l: VLink) => (l.type === 'IN_MODULE' || l.type === 'IN_REPO' || l.type === 'HAS_LABEL' ? 32 : 52))
  }, [size.width > 0])

  // Fit after new evidence arrives.
  useEffect(() => {
    if (!fitNonce) return
    fitPendingRef.current = true
    const t = setTimeout(() => {
      const keep = activeFitRef.current
      fitTo(keep, 600)
    }, 650)
    return () => clearTimeout(t)
  }, [fitNonce])

  // Re-frame when the canvas is resized (window resize, or the pane becoming visible on narrow layouts).
  useEffect(() => {
    if (!size.width || !size.height) return
    const t = setTimeout(() => {
      const keep = activeFitRef.current
      fitTo(keep, 400)
    }, 250)
    return () => clearTimeout(t)
  }, [size.width, size.height])

  // Focus a node (citation click, path chip click).
  useEffect(() => {
    if (!focus) return
    fitPendingRef.current = false
    let tries = 0
    const go = () => {
      const n = data.nodes.find((x) => x.id === focus.key)
      if (n && n.x != null && n.y != null) {
        const k = Math.max(2.4, fgRef.current?.zoom() ?? 1)
        // shift left so the node isn't hidden under the details card
        const dx = size.width > 720 ? 170 / k : 0
        fgRef.current?.centerAt(n.x + dx, n.y, 700)
        fgRef.current?.zoom(k, 700)
      } else if (tries++ < 8) {
        setTimeout(go, 150)
      }
    }
    go()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce])

  const drawNode = useCallback(
    (node: VNode, ctx: CanvasRenderingContext2D, scale: number) => {
      const x = node.x ?? 0
      const y = node.y ?? 0
      const r = RADIUS[node.label] ?? 4.5
      const color = labelColor(node.label)
      const isHl = hl.has(node.id)
      const isSel = node.id === selectedKey
      const isFocus = focus?.key === node.id
      const isHover = node.id === hover
      const dim = active ? !active.has(node.id) : false

      ctx.save()
      ctx.globalAlpha = dim ? 0.16 : 1
      if (isHl && !dim) {
        ctx.beginPath()
        ctx.arc(x, y, r + 5 / Math.sqrt(scale) + 2, 0, 2 * Math.PI)
        ctx.fillStyle = pal.accent + '33'
        ctx.fill()
      }
      ctx.beginPath()
      ctx.arc(x, y, r, 0, 2 * Math.PI)
      ctx.fillStyle = color
      ctx.fill()
      if (node.label === 'Decision') {
        // small inner diamond marks decisions
        ctx.fillStyle = dark ? 'rgba(10,12,18,0.55)' : 'rgba(255,255,255,0.7)'
        ctx.beginPath()
        ctx.moveTo(x, y - r * 0.5)
        ctx.lineTo(x + r * 0.5, y)
        ctx.lineTo(x, y + r * 0.5)
        ctx.lineTo(x - r * 0.5, y)
        ctx.closePath()
        ctx.fill()
      }
      if (isHl || isSel || isFocus) {
        ctx.lineWidth = (isSel || isFocus ? 2 : 1.4) / Math.min(scale, 3) + 0.4
        ctx.strokeStyle = isSel || isFocus ? pal.ring : pal.accent
        ctx.stroke()
      }

      const showLabel = !dim && (isHl || isSel || isFocus || isHover || citedSet.has(node.id) || scale > 2.6 || (active?.has(node.id) && !!hover))
      if (showLabel) {
        const text = shortTitle(node)
        const fs = Math.max(10 / scale, 2.2)
        ctx.font = `${isHl || isSel ? 600 : 500} ${fs}px Inter, ui-sans-serif, system-ui, sans-serif`
        const w = ctx.measureText(text).width
        const pad = 3 / scale
        const ty = y + r + fs * 0.9 + pad
        ctx.fillStyle = pal.labelBg
        const h = fs + pad * 1.6
        const rx = x - w / 2 - pad * 1.5
        const ry = ty - fs / 2 - pad * 0.8
        const rw = w + pad * 3
        ctx.beginPath()
        ctx.roundRect?.(rx, ry, rw, h, h / 2)
        ctx.fill()
        ctx.fillStyle = isHl || isSel ? pal.text : pal.muted
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(text, x, ty)
      }
      ctx.restore()
    },
    [hl, selectedKey, focus?.key, hover, active, citedSet, pal, dark],
  )

  const drawPointer = useCallback((node: VNode, color: string, ctx: CanvasRenderingContext2D) => {
    const r = (RADIUS[node.label] ?? 4.5) + 3
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(node.x ?? 0, node.y ?? 0, r, 0, 2 * Math.PI)
    ctx.fill()
  }, [])

  // Relationship type label drawn on path edges.
  const drawLinkLabel = useCallback(
    (link: VLink, ctx: CanvasRenderingContext2D, scale: number) => {
      if (!isPathLink(link) || scale < 0.8 || hover) return
      const s = link.source as VNode
      const t = link.target as VNode
      if (s.x == null || t.x == null || s.y == null || t.y == null) return
      const fs = Math.max(8 / scale, 1.6)
      ctx.save()
      ctx.font = `600 ${fs}px "JetBrains Mono", ui-monospace, monospace`
      ctx.fillStyle = pal.accent
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      const mx = (s.x + t.x) / 2
      const my = (s.y + t.y) / 2
      let ang = Math.atan2(t.y - s.y, t.x - s.x)
      if (ang > Math.PI / 2 || ang < -Math.PI / 2) ang += Math.PI
      ctx.translate(mx, my)
      ctx.rotate(ang)
      ctx.fillText(link.type, 0, -fs * 0.7)
      ctx.restore()
    },
    [isPathLink, pal, hover],
  )

  const linkColor = useCallback(
    (l: VLink) => {
      if (hover) return l.s === hover || l.t === hover ? pal.accent : pal.lineDim
      if (isPathLink(l)) return pal.accent
      if (active && !(active.has(l.s) && active.has(l.t))) return pal.lineDim
      return pal.line
    },
    [hover, isPathLink, active, pal],
  )

  const visibleNode = useCallback(
    (n: VNode) => !hidden.has(n.label) && (!pathOnly || !hl.size || hl.has(n.id) || citedSet.has(n.id)),
    [hidden, pathOnly, hl, citedSet],
  )
  const nodeById = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data])
  const visibleLink = useCallback(
    (l: VLink) => {
      const s = nodeById.get(l.s)
      const t = nodeById.get(l.t)
      return !!s && !!t && visibleNode(s) && visibleNode(t)
    },
    [nodeById, visibleNode],
  )

  const legend = useMemo(() => {
    const counts = new Map<string, number>()
    for (const n of data.nodes) counts.set(n.label, (counts.get(n.label) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => LABEL_ORDER.indexOf(a[0]) - LABEL_ORDER.indexOf(b[0]))
  }, [data])

  const pathChips = highlight.map((k) => nodeById.get(k)).filter(Boolean) as VNode[]
  const selected = selectedKey ? nodeById.get(selectedKey) ?? null : null
  const empty = data.nodes.length === 0

  return (
    <section className="pane graph-pane" aria-label="Evidence graph">
      <div className="graph-toolbar">
        <div className="graph-title">
          <span className="eyebrow">Evidence graph</span>
          <span className="graph-count mono">
            {data.nodes.length} nodes · {data.links.length} edges
          </span>
          {props.streaming && <span className="live-pill"><span className="pulse" />tracing</span>}
        </div>
        <div className="graph-actions">
          <button className={`btn btn-ghost btn-sm ${pathOnly ? 'is-on' : ''}`} onClick={() => setPathOnly((v) => !v)} disabled={!hl.size} title="Show only the path behind the answer">
            <Icon name="path" /> Path only
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => fitTo(new Set(), 500)} disabled={empty} title="Fit to view">
            <Icon name="fit" /> Fit
          </button>
          <button className="btn btn-ghost btn-sm" onClick={props.onClear} disabled={empty} title="Clear the evidence graph">
            <Icon name="clear" /> Clear
          </button>
        </div>
      </div>

      <div className="graph-canvas" ref={wrapRef}>
        {size.width > 0 && size.height > 0 && (
          <ForceGraph2D<VNode, VLink>
            ref={fgRef}
            width={size.width}
            height={size.height}
            graphData={data}
            backgroundColor="rgba(0,0,0,0)"
            nodeId="id"
            nodeVisibility={visibleNode}
            linkVisibility={visibleLink}
            nodeCanvasObject={drawNode}
            nodePointerAreaPaint={drawPointer}
            nodeLabel={(n: VNode) => `<div class="fg-tip"><b>${esc(n.label)}</b> ${esc(n.title || n.id)}<div class="mono">${esc(n.id)}</div></div>`}
            linkColor={linkColor}
            linkWidth={(l: VLink) => (isPathLink(l) ? 2.6 : hover && (l.s === hover || l.t === hover) ? 1.6 : 0.8)}
            linkDirectionalArrowLength={(l: VLink) => (isPathLink(l) ? 4 : 0)}
            linkDirectionalArrowRelPos={0.92}
            linkDirectionalArrowColor={() => pal.accent}
            linkDirectionalParticles={(l: VLink) => (isPathLink(l) ? 3 : 0)}
            linkDirectionalParticleSpeed={0.007}
            linkDirectionalParticleWidth={3}
            linkDirectionalParticleColor={() => pal.accent}
            linkCanvasObjectMode={() => 'after'}
            linkCanvasObject={drawLinkLabel}
            linkLabel={(l: VLink) => `<div class="fg-tip mono">${esc(l.type)}</div>`}
            onNodeHover={(n) => setHover(n ? (n as VNode).id : null)}
            onNodeClick={(n) => props.onNodeClick(n as VNode)}
            onBackgroundClick={props.onCloseCard}
            onEngineStop={() => {
              if (!fitPendingRef.current) return
              fitPendingRef.current = false
              const keep = activeFitRef.current
              fitTo(keep, 500)
            }}
            cooldownTicks={180}
            d3AlphaDecay={0.035}
            d3VelocityDecay={0.32}
            minZoom={0.3}
            maxZoom={8}
          />
        )}

        {empty && (
          <div className="graph-empty">
            <div className="graph-empty-art" aria-hidden>
              <svg viewBox="0 0 120 80" width="120" height="80">
                <g stroke="currentColor" strokeWidth="1.2" opacity="0.5" fill="none">
                  <path d="M20 60 L50 25 L85 45 L105 18" />
                  <path d="M50 25 L60 68" strokeDasharray="3 3" />
                </g>
                <circle cx="20" cy="60" r="5" fill="#f2a65a" />
                <circle cx="50" cy="25" r="6" fill="var(--accent)" />
                <circle cx="85" cy="45" r="5" fill="#5aa9e6" />
                <circle cx="105" cy="18" r="4" fill="#4fd1c5" />
                <circle cx="60" cy="68" r="3.5" fill="#b28dff" />
              </svg>
            </div>
            <h3>Every answer shows its path</h3>
            <p>Ask a question and the agents' graph traversals stream in here: people, files, PRs and decisions, with the path behind the answer highlighted.</p>
            {props.overview && (
              <div className="overview-stats">
                {Object.entries(props.overview.counts)
                  .filter(([l]) => ['Person', 'File', 'PullRequest', 'Issue', 'Decision', 'Repo'].includes(l))
                  .sort((a, b) => LABEL_ORDER.indexOf(a[0]) - LABEL_ORDER.indexOf(b[0]))
                  .map(([label, n]) => (
                    <div key={label} className="stat">
                      <span className="dot" style={{ background: labelColor(label) }} />
                      <span className="stat-n mono">{n.toLocaleString()}</span>
                      <span className="stat-l">{label === 'PullRequest' ? 'PRs' : label === 'Person' ? 'People' : label + 's'}</span>
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}

        {selected && (
          <NodeCard
            node={selected}
            degree={adjacency.get(selected.id)?.size ?? 0}
            expanding={props.expanding === selected.id}
            onClose={props.onCloseCard}
            onExpand={() => props.onExpand(selected.id)}
            onFocus={() => props.onFocus(selected.id)}
          />
        )}

        {!empty && (
          <div className="graph-bottom">
            {pathChips.length > 0 && (
              <div className="path-strip" aria-label="Path behind the answer">
                <span className="eyebrow">Path behind the answer</span>
                <div className="path-chips">
                  {pathChips.map((n, i) => (
                    <span key={n.id} className="path-step">
                      {i > 0 && <span className="path-arrow" aria-hidden>→</span>}
                      <button className="chip chip-node" onClick={() => props.onFocus(n.id)} title={n.id}>
                        <span className="dot" style={{ background: labelColor(n.label) }} />
                        {shortTitle(n)}
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="legend">
              {legend.map(([label, n]) => (
                <button
                  key={label}
                  className={`legend-item ${hidden.has(label) ? 'is-off' : ''}`}
                  onClick={() =>
                    setHidden((h) => {
                      const next = new Set(h)
                      if (next.has(label)) next.delete(label)
                      else next.add(label)
                      return next
                    })
                  }
                  title={hidden.has(label) ? `Show ${label}` : `Hide ${label}`}
                >
                  <span className="dot" style={{ background: labelColor(label) }} />
                  {label}
                  <span className="mono legend-n">{n}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
