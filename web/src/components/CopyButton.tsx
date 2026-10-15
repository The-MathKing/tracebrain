import { useState } from 'react'
import { Icon } from './Icon'

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  }
}

export function CopyButton({ text, label, small }: { text: string; label?: string; small?: boolean }) {
  const [done, setDone] = useState(false)
  return (
    <button
      className={small ? 'icon-btn' : 'btn btn-sm'}
      onClick={async () => {
        if (await copy(text)) {
          setDone(true)
          setTimeout(() => setDone(false), 1400)
        }
      }}
      title={done ? 'Copied' : 'Copy'}
      aria-label={label ?? 'Copy'}
    >
      <Icon name={done ? 'check' : 'copy'} />
      {label && <span>{done ? 'Copied' : label}</span>}
    </button>
  )
}
