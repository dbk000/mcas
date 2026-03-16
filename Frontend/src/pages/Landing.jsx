import { lazy, Suspense, useState } from 'react'
import { usePageMeta } from '../utils/pageMeta'

// Default layout (current design)
import LayoutBloomberg from './landing-layouts/layout-01-bloomberg'

const layouts = [
  { id: 'bloomberg', label: '01 Bloomberg', load: null },
  { id: 'hero-full', label: '02 Hero Full', load: lazy(() => import('./landing-layouts/layout-02-hero-full')) },
  { id: 'split-60-40', label: '03 Split 60/40', load: lazy(() => import('./landing-layouts/layout-03-split-60-40')) },
  { id: 'metrics-first', label: '04 Metrics First', load: lazy(() => import('./landing-layouts/layout-04-metrics-first')) },
  { id: 'centered', label: '05 Centered', load: lazy(() => import('./landing-layouts/layout-05-centered')) },
  { id: 'card-wall', label: '06 Card Wall', load: lazy(() => import('./landing-layouts/layout-06-card-wall')) },
  { id: 'minimal', label: '07 Minimal', load: lazy(() => import('./landing-layouts/layout-07-minimal')) },
  { id: 'magazine', label: '08 Magazine', load: lazy(() => import('./landing-layouts/layout-08-magazine')) },
  { id: 'dashboard', label: '09 Dashboard', load: lazy(() => import('./landing-layouts/layout-09-dashboard')) },
  { id: 'storyteller', label: '10 Storyteller', load: lazy(() => import('./landing-layouts/layout-10-storyteller')) },
]

export default function Landing() {
  usePageMeta('landing')
  const [layoutId, setLayoutId] = useState('bloomberg')

  const selected = layouts.find((l) => l.id === layoutId)
  const LayoutComponent = selected?.load || LayoutBloomberg

  return (
    <>
      {selected?.load ? (
        <Suspense fallback={<div className="p-8 text-center text-terminal-400">Loading layout...</div>}>
          <LayoutComponent />
        </Suspense>
      ) : (
        <LayoutBloomberg />
      )}

      {(
        <div className="fixed bottom-4 right-4 z-[9999] rounded-lg border border-terminal-700 bg-terminal-800 p-2 shadow-xl">
          <select
            value={layoutId}
            onChange={(e) => setLayoutId(e.target.value)}
            className="rounded bg-terminal-900 px-2 py-1 text-xs text-terminal-100"
          >
            {layouts.map((l) => (
              <option key={l.id} value={l.id}>{l.label}</option>
            ))}
          </select>
        </div>
      )}
    </>
  )
}
