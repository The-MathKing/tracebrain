import { useCallback, useEffect, useRef, useState } from 'react'

export function useColorScheme(): 'dark' | 'light' {
  const q = '(prefers-color-scheme: light)'
  const [light, setLight] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches)
  useEffect(() => {
    const m = window.matchMedia(q)
    const on = () => setLight(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return light ? 'light' : 'dark'
}

export function useElementSize<T extends HTMLElement>() {
  const [el, setEl] = useState<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width: Math.floor(width), height: Math.floor(height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  const ref = useCallback((node: T | null) => setEl(node), [])
  return [ref, size] as const
}

export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn)
  saved.current = fn
  useEffect(() => {
    if (ms == null) return
    saved.current()
    const id = setInterval(() => saved.current(), ms)
    return () => clearInterval(id)
  }, [ms])
}

export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw != null ? (JSON.parse(raw) as T) : initial
    } catch {
      return initial
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* storage unavailable */
    }
  }, [key, value])
  return [value, setValue] as const
}
