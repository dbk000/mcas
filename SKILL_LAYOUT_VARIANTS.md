# Skill: 10x Layout Variants for Any Frontend Page

Generate 10 visual layout variants of a specified page or section, all sharing
the same data and copy, differing only in arrangement and visual hierarchy.

## When to use

User says something like "give me 10 versions of the landing page" or "I want
to try different layouts for the dashboard" or "show me options for how the
metrics section could look."

## Architecture

```
src/pages/[page]-layouts/
├── shared.jsx           ← All sub-components extracted here
├── use[Page]Data.js     ← Shared data hook (API calls, state)
├── layout-01-name.jsx   ← Thin composition (~30-60 lines)
├── layout-02-name.jsx
├── ...
└── layout-10-name.jsx

src/pages/[Page].jsx     ← Switcher shell (lazy-loads variants)
```

## Step 1: Read the target page

Read the full source file. Identify:
- **Data**: hooks, API calls, state — extract into `use[Page]Data.js`
- **Sections**: visually distinct blocks — extract each as a named component in `shared.jsx`
- **Content**: text strings, labels, copy — keep in shared components (single source of truth)

The goal: the original page should be expressible as a ~40-line composition of
shared components. If it can't, the extraction isn't complete.

## Step 2: Extract shared.jsx

Every visual block becomes a named export:

```jsx
// shared.jsx
export function HeroHeadline({ className }) { ... }
export function MetricsGrid({ data }) { ... }
export function GlassBoxReplay({ data }) { ... }
export function CTAButtons({ href, label }) { ... }
```

Rules:
- Components receive data via props, not internal hooks
- Layout classes (grid, flex, width) stay OUT of shared components — those
  are the layout's job
- Visual styling (colors, borders, padding within a card) stays IN shared
  components — those are consistent across layouts
- Each component is self-contained: renders its own card/section wrapper

## Step 3: Extract the data hook

```jsx
// use[Page]Data.js
export default function use[Page]Data() {
  // All API calls, subscriptions, computed values
  return { data1, data2, ready, ...actions }
}
```

Every layout calls this same hook. No data fetching in layout files.

## Step 4: Create 10 layouts

Each layout file:
```jsx
import { HeroHeadline, MetricsGrid, ... } from './shared'
import useData from './use[Page]Data'

export default function Layout01() {
  const { data1, data2, ready } = useData()
  if (!ready) return null

  return (
    <div data-operator-surface>
      {/* Different arrangement of the SAME components */}
    </div>
  )
}
```

**How to make them actually different** (not just 10 shades of gray):
- Change the grid structure (1-col vs 2-col vs 3-col, different ratios)
- Move sections above/below the fold
- Change what's beside what (metrics beside hero vs below hero)
- Try centered vs left-aligned vs split layouts
- Try horizontal ticker vs grid vs stacked list for the same data
- Try full-width hero vs contained hero
- Try sticky/fixed elements (CTA bar at bottom)
- Try progressive disclosure (minimal above fold, detail below)
- Try "fake app preview" (dashboard mockup as a visual element)
- Try storyteller/scroll (one section at a time with generous spacing)

## Step 5: Wire the switcher

Replace the original page with a shell:

```jsx
import { lazy, Suspense, useState } from 'react'
import DefaultLayout from './[page]-layouts/layout-01-name'

const layouts = [
  { id: 'name', label: '01 Name', load: null },  // default, eagerly loaded
  { id: 'other', label: '02 Other', load: lazy(() => import('./[page]-layouts/layout-02-other')) },
  // ... 8 more, all lazy
]

export default function Page() {
  const [layoutId, setLayoutId] = useState('name')
  const selected = layouts.find(l => l.id === layoutId)
  const Layout = selected?.load || DefaultLayout

  return (
    <>
      {selected?.load ? (
        <Suspense fallback={<div>Loading...</div>}>
          <Layout />
        </Suspense>
      ) : (
        <DefaultLayout />
      )}

      {import.meta.env.DEV && (
        <div className="fixed bottom-4 right-4 z-[9999] rounded-lg border border-terminal-700 bg-terminal-800 p-2 shadow-xl">
          <select
            value={layoutId}
            onChange={e => setLayoutId(e.target.value)}
            className="rounded bg-terminal-900 px-2 py-1 text-xs text-terminal-100"
          >
            {layouts.map(l => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
        </div>
      )}
    </>
  )
}
```

Key details:
- Default layout is **eagerly imported** (no flash on first load)
- All others are **lazy** (code-split, ~1KB each)
- Switcher is **dev-only** (`import.meta.env.DEV`)
- Once user picks a winner, delete the others and collapse back to a single file

## Step 6: Build and budget check

```bash
npm run build 2>&1 | tail -20
```

Expect ~1KB per lazy layout chunk. The shared.jsx adds to the parent route
chunk. If budget is tight, the total overhead is roughly:
- shared.jsx extraction: net zero (same code, different file)
- 10 layout files: ~10KB total (lazy, not in initial bundle)
- Switcher shell: ~1KB

If over budget, bump `maximum_total_js_kb` by 15KB in `bundle-budget.json`
and note the reason.

## Step 7: User picks a winner

After the user reviews all 10 and picks one:

1. Move the winning layout's code into the main page file
2. Delete the `[page]-layouts/` directory
3. Keep `shared.jsx` sub-components if they improve readability
4. Remove the switcher from the page
5. Revert any budget bump

## Parallelisation

Use 2 agents to create layouts 1-5 and 6-10 in parallel. Give each agent:
- The list of shared component names and their props
- The layout description (arrangement, not content)
- The instruction to keep files under 60 lines

## Anti-patterns

- DO NOT duplicate content/copy across layout files — it goes in shared.jsx
- DO NOT put data fetching in layout files — it goes in the data hook
- DO NOT make layouts that only differ in padding or font size — that's theming, not layout
- DO NOT include the switcher in production builds
- DO NOT create new components for one layout only — if it's unique to one layout, inline it
