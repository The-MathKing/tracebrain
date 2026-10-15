import { useCallback, useMemo, useRef, useState } from 'react'
import type { GEdge, GNode } from '../types'

export interface VNode extends GNode {
  addedAt: number
  expanded?: boolean
  x?: number
  y?: number
  fx?: number
  fy?: number
}

export interface VLink {
  id: string
  s: string
  t: string
  type: string
  // react-force-graph replaces these with node objects after the first tick
  source: string | VNode
  target: string | VNode
}

/** Hard cap on nodes kept in the evidence graph so it never turns into a hairball. */
export const MAX_NODES = 140

/**
 * Accumulating graph store. Node/link objects are kept stable across merges because
 * the force engine stores positions on them; recreating them would make the layout jump.
 */
export function useEvidenceGraph() {
  const nodes = useRef(new Map<string, VNode>())
  const links = useRef(new Map<string, VLink>())
  const [version, setVersion] = useState(0)
  const seq = useRef(0)

  const merge = useCallback((inNodes: GNode[], inEdges: GEdge[], protect: Iterable<string> = []) => {
    let changed = false
    for (const n of inNodes ?? []) {
      if (!n?.id) continue
      const cur = nodes.current.get(n.id)
      if (cur) {
        const nextProps = { ...cur.props, ...(n.props ?? {}) }
        cur.props = nextProps
        cur.title = n.title || cur.title
        cur.url = n.url ?? cur.url
        cur.label = n.label || cur.label
      } else {
        // seed new nodes near an existing neighbour so they don't fly in from the origin
        const anchorEdge = (inEdges ?? []).find((e) => (e.source === n.id && nodes.current.has(e.target)) || (e.target === n.id && nodes.current.has(e.source)))
        const anchor = anchorEdge ? nodes.current.get(anchorEdge.source === n.id ? anchorEdge.target : anchorEdge.source) : undefined
        const v: VNode = { ...n, props: n.props ?? {}, addedAt: ++seq.current }
        if (anchor?.x != null && anchor?.y != null) {
          v.x = anchor.x + (Math.random() - 0.5) * 30
          v.y = anchor.y + (Math.random() - 0.5) * 30
        }
        nodes.current.set(n.id, v)
        changed = true
      }
    }
    for (const e of inEdges ?? []) {
      if (!e?.source || !e?.target) continue
      const id = `${e.source}|${e.type}|${e.target}`
      if (links.current.has(id)) continue
      if (!nodes.current.has(e.source) || !nodes.current.has(e.target)) continue
      links.current.set(id, { id, s: e.source, t: e.target, type: e.type, source: e.source, target: e.target })
      changed = true
    }
    // evict the oldest unprotected nodes beyond the cap
    if (nodes.current.size > MAX_NODES) {
      const keep = new Set(protect)
      for (const n of inNodes ?? []) keep.add(n.id)
      const victims = [...nodes.current.values()]
        .filter((n) => !keep.has(n.id))
        .sort((a, b) => a.addedAt - b.addedAt)
        .slice(0, nodes.current.size - MAX_NODES)
      for (const v of victims) nodes.current.delete(v.id)
      for (const [id, l] of links.current) {
        if (!nodes.current.has(l.s) || !nodes.current.has(l.t)) links.current.delete(id)
      }
      changed = true
    }
    if (changed) setVersion((v) => v + 1)
    return changed
  }, [])

  const markExpanded = useCallback((key: string) => {
    const n = nodes.current.get(key)
    if (n) n.expanded = true
  }, [])

  const clear = useCallback(() => {
    nodes.current.clear()
    links.current.clear()
    setVersion((v) => v + 1)
  }, [])

  const get = useCallback((key: string) => nodes.current.get(key), [])

  const data = useMemo(
    () => ({ nodes: [...nodes.current.values()], links: [...links.current.values()] }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  )

  return { data, merge, clear, get, markExpanded, version }
}

export type EvidenceGraph = ReturnType<typeof useEvidenceGraph>
