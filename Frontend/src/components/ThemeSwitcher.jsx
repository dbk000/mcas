import { useState, useEffect } from 'react'
import { Palette } from 'lucide-react'
import { STORAGE_KEYS } from '../config/storageKeys'

const THEMES = [
  { id: '', label: 'Default' },
  { id: 'midnight', label: 'Midnight' },
  { id: 'arctic', label: 'Arctic' },
  { id: 'ember', label: 'Ember' },
  { id: 'forest', label: 'Forest' },
  { id: 'ocean', label: 'Ocean' },
  { id: 'sand', label: 'Sand' },
  { id: 'neon', label: 'Neon' },
  { id: 'slate', label: 'Slate' },
  { id: 'copper', label: 'Copper' },
  { id: 'ultraviolet', label: 'Ultraviolet' },
]

function applyTheme(themeId) {
  if (themeId) {
    document.documentElement.setAttribute('data-theme', themeId)
  } else {
    document.documentElement.removeAttribute('data-theme')
  }
}

export default function ThemeSwitcher() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEYS.THEME) || ''
    } catch {
      return ''
    }
  })
  const [open, setOpen] = useState(false)

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  function handleSelect(themeId) {
    setTheme(themeId)
    applyTheme(themeId)
    try {
      if (themeId) {
        localStorage.setItem(STORAGE_KEYS.THEME, themeId)
      } else {
        localStorage.removeItem(STORAGE_KEYS.THEME)
      }
    } catch { /* noop */ }
    setOpen(false)
  }

  return (
    <div className="fixed right-4 top-4 z-[9998]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex size-9 items-center justify-center rounded-lg border border-terminal-700 bg-terminal-800 text-terminal-300 shadow-lg transition-colors hover:border-amber-500/50 hover:text-amber-300"
        title="Theme switcher"
      >
        <Palette className="size-4" />
      </button>

      {open && (
        <div className="absolute right-0 top-11 w-40 overflow-hidden rounded-lg border border-terminal-700 bg-terminal-800 shadow-xl">
          {THEMES.map((t) => (
            <button
              key={t.id}
              onClick={() => handleSelect(t.id)}
              className={[
                'flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors',
                theme === t.id
                  ? 'bg-amber-500/20 text-amber-300'
                  : 'text-terminal-300 hover:bg-terminal-700 hover:text-terminal-100',
              ].join(' ')}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
