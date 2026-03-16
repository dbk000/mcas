import { useState, useEffect } from 'react'
import { Palette } from 'lucide-react'
import { STORAGE_KEYS } from '../config/storageKeys'

const THEMES = [
  { id: '', label: 'Default (Ember)', tag: 'warm light', group: 'core' },
  { id: 'terminal', label: 'Terminal', tag: 'dark', group: 'core' },
  { id: 'midnight', label: 'Midnight', tag: 'dark', group: 'core' },
  { id: 'arctic', label: 'Arctic', tag: 'light', group: 'core' },
  { id: 'ember', label: 'Ember', tag: 'parchment', group: 'ember' },
  { id: 'ember-2', label: 'Dusk Linen', tag: 'soft warm', group: 'ember' },
  { id: 'ember-3', label: 'Tobacco', tag: 'deep warm', group: 'ember' },
  { id: 'ember-4', label: 'Clay', tag: 'earthy', group: 'ember' },
  { id: 'ember-5', label: 'Oat Milk', tag: 'lightest', group: 'ember' },
  { id: 'ember-6', label: 'Walnut', tag: 'darkest', group: 'ember' },
  { id: 'ember-7', label: 'Sandstone', tag: 'desaturated', group: 'ember' },
  { id: 'ember-8', label: 'Chai', tag: 'golden', group: 'ember' },
  { id: 'ember-9', label: 'Smoked Oak', tag: 'gray-warm', group: 'ember' },
  { id: 'ember-10', label: 'Antique', tag: 'dimmest', group: 'ember' },
  { id: 'forest', label: 'Forest', tag: 'dark', group: 'core' },
  { id: 'ocean', label: 'Ocean', tag: 'mid', group: 'core' },
  { id: 'sand', label: 'Sand', tag: 'mid warm', group: 'core' },
  { id: 'neon', label: 'Neon', tag: 'AMOLED', group: 'core' },
  { id: 'slate', label: 'Slate', tag: 'light', group: 'core' },
  { id: 'copper', label: 'Copper', tag: 'high-contrast', group: 'core' },
  { id: 'ultraviolet', label: 'Ultraviolet', tag: 'dark', group: 'core' },
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
        <div className="absolute right-0 top-11 max-h-[70vh] w-44 overflow-y-auto rounded-lg border border-terminal-700 bg-terminal-800 shadow-xl">
          {THEMES.map((t, i) => {
            const prev = i > 0 ? THEMES[i - 1] : null
            const showSep = prev && prev.group !== t.group
            return (
              <div key={t.id}>
                {showSep && <div className="mx-2 border-t border-terminal-600" />}
                <button
                  onClick={() => handleSelect(t.id)}
                  className={[
                    'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors',
                    theme === t.id
                      ? 'bg-amber-500/20 text-amber-300'
                      : 'text-terminal-300 hover:bg-terminal-700 hover:text-terminal-100',
                  ].join(' ')}
                >
                  <span>{t.label}</span>
                  <span className="ml-auto text-[9px] uppercase tracking-wider opacity-50">{t.tag}</span>
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
