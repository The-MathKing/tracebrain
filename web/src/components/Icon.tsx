// Tiny inline icon set (stroke icons, 16px) so we don't pull in an icon library.
const PATHS: Record<string, string> = {
  path: 'M3 12a2 2 0 1 0 0-.01M13 4a2 2 0 1 0 0-.01M5 11l6.5-6M13 12a2 2 0 1 0 0-.01M5 12h6',
  fit: 'M2 5V2h3M11 2h3v3M14 11v3h-3M5 14H2v-3',
  clear: 'M3 4h10M6 4V2.5h4V4M4.5 4l.7 9.5h5.6l.7-9.5',
  trash: 'M3 4h10M6 4V2.5h4V4M4.5 4l.7 9.5h5.6l.7-9.5',
  close: 'M4 4l8 8M12 4l-8 8',
  external: 'M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5',
  expand: 'M8 3v10M3 8h10',
  copy: 'M5.5 5.5h7v7h-7zM3.5 10.5v-7h7',
  check: 'M3 8.5l3 3 7-7',
  chevron: 'M4 6l4 4 4-4',
  send: 'M2.5 8h9M8 4l4 4-4 4',
  stop: 'M4.5 4.5h7v7h-7z',
  plus: 'M8 3v10M3 8h10',
  focus: 'M8 2v3M8 11v3M2 8h3M11 8h3M8 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3',
  user: 'M8 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3 13.5c.8-2.2 2.7-3.5 5-3.5s4.2 1.3 5 3.5',
  memory: 'M4 2.5h8v11H4zM6 5.5h4M6 8h4M6 10.5h2.5',
  agents: 'M3 4.5h4v3H3zM9 8.5h4v3H9zM5 7.5v2.5h4',
  triage: 'M8 2l6 11H2zM8 6.5v3M8 11.2v.3',
  chat: 'M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z',
  graph: 'M4 12a1.5 1.5 0 1 0 0-.01M12 4a1.5 1.5 0 1 0 0-.01M12 12a1.5 1.5 0 1 0 0-.01M5.2 11l5.6-6M5.5 12h5',
  sparkle: 'M8 2.5l1.3 3.2 3.2 1.3-3.2 1.3L8 11.5 6.7 8.3 3.5 7l3.2-1.3z',
}

export function Icon({ name, size = 14 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={PATHS[name] ?? ''} />
    </svg>
  )
}
