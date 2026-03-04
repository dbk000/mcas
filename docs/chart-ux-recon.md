# Chart UX Recon: Expired Opportunities

Date: 2026-03-04  
Scope: MCAS expired-opportunity curve review + forward-blind survey UX.

## 1) High-Impact Feature Shortlist (Max 10)

### Must-Have (v1)

| Feature | What / Why | Implementation Concept (original) | Dependency Cost / Bloat | Licensing Notes |
|---|---|---|---|---|
| Forward-blind global reveal | Trains pattern recognition without future leakage. | Global offset-minutes slider from entry; per-row reveal timestamp clamps to each row window; chart x-axis fit only to revealed candles. | Low. Uses existing chart lib + state logic. | No code reuse; original implementation. |
| Per-opp curve viewer | Fast forensic review of one expired setup. | Modal/drawer with reusable chart component, vertical entry/exit markers, horizontal entry/stop/TP lines. | Low. Reuses chart component. | No code reuse; original implementation. |
| Virtualized stack view | Keeps 100+ curve review smooth. | Fixed-row windowing with overscan; render only visible rows; optional multi-column presentation layout. | Low-medium. No new package needed. | No code reuse; original implementation. |
| Prediction commit gating | Enforces deliberate hypothesis before forward reveal. | Local prediction store keyed by opp_id + confidence + reveal offset at commit; forward controls disabled until commit. | Low. LocalStorage only. | No code reuse; original implementation. |
| Outcome-mode batching | Enables focused study sets (winners/losers/mixed). | Backend outcome-enriched opportunity query + batch/page controls + optional random sampling. | Low-medium. Small SQL extension. | No code reuse; original implementation. |

### High-Value (v1.1)

| Feature | What / Why | Implementation Concept (original) | Dependency Cost / Bloat | Licensing Notes |
|---|---|---|---|---|
| Persist predictions to DB | Cross-session analytics + leaderboard + audit. | Add `opp_visual_predictions` table; write commit events with outcome join for post-hoc scoring. | Medium. 1 table + 1 API. | No code reuse; original implementation. |
| Session metrics / confusion matrix | Turns review into measurable training loop. | Aggregate TP/TN/FP/FN + confidence calibration by mode/setup/timeframe. | Medium. Backend aggregation endpoints. | No code reuse; original implementation. |
| Keyboard-first review mode | Dramatically increases throughput for power users. | Hotkeys for commit class/confidence/reveal step/next batch. | Low-medium. UI event handling only. | No code reuse; original implementation. |

### Later

| Feature | What / Why | Implementation Concept (original) | Dependency Cost / Bloat | Licensing Notes |
|---|---|---|---|---|
| Multi-timeframe synchronized panes | Improves context (micro + structural trend). | Reuse same reveal offset, render 5m + 1h panes per row with synchronized entry anchor. | Medium-high. More queries + rendering cost. | No code reuse; original implementation. |
| Annotation and replay packs | Institutionalize pattern libraries. | Save user annotations + replay scripts as shareable study packs. | High. State/versioning + collaboration. | No code reuse; original implementation. |

## 2) Library Recommendation

### Evaluated
- TradingView `lightweight-charts`
- Apache ECharts
- Plotly.js

### Recommendation: `lightweight-charts` (keep as primary)

Rationale:
- Purpose fit: financial OHLC workflows are first-class.
- Performance profile: canvas-based, small bundle, strong responsiveness for repeated small chart instances.
- Existing stack fit: already used in MCAS frontend; minimal migration risk.
- Extensibility: custom overlay primitives / DOM overlays are enough for unsnapped vertical event markers and blind-reveal UI.

Alternatives summary:
- ECharts: very capable and strong progressive rendering options for huge datasets, but adds more option-surface complexity for the specific compact-row pattern trainer use case.
- Plotly: flexible ecosystem, but default SVG path and heavier bundle profile are less aligned with rendering many simultaneous tiny candlestick panels.

Decision:
- Keep `lightweight-charts` for v1/v1.1.
- Revisit ECharts only if we need multi-million-point composite analytics in a single viewport (not current survey requirement).

## 3) Repo Farming Methodology + Findings

### Method
- Prioritized primary sources: official docs, official repos, release notes/changelogs, and issue threads with reproducible cases.
- Focused extraction: performance behavior under many candles/markers, marker correctness under updates/zoom, plugin lifecycle, virtualization patterns for long lists.
- Converted findings into implementation guardrails (no direct code copy).

### Repos Surveyed
- TradingView Lightweight Charts: https://github.com/tradingview/lightweight-charts
- Apache ECharts: https://github.com/apache/echarts
- Plotly.js: https://github.com/plotly/plotly.js
- TanStack Virtual: https://github.com/TanStack/virtual
- Apache ECharts docs repo: https://github.com/apache/echarts-doc
- Apache Superset (library migration context): https://github.com/apache/superset

### Key Evidence Links (Docs / Issues / Changelogs)
- Lightweight Charts docs: time scale API and marker guidance  
  https://tradingview.github.io/lightweight-charts/docs/time-scale  
  https://tradingview.github.io/lightweight-charts/docs/api/interfaces/ITimeScaleApi  
  https://tradingview.github.io/lightweight-charts/tutorials/how_to/series-markers
- Lightweight Charts issue learnings:  
  https://github.com/tradingview/lightweight-charts/issues/1808  
  https://github.com/tradingview/lightweight-charts/issues/1990  
  https://github.com/tradingview/lightweight-charts/issues/2000  
  https://github.com/tradingview/lightweight-charts/issues/1791
- ECharts candlestick and changelog references:  
  https://raw.githubusercontent.com/apache/echarts-doc/master/en/option/series/candlestick.md  
  https://echarts.apache.org/en/changelog.html  
  https://github.com/apache/echarts/issues/14144
- Plotly references:  
  https://plotly.com/javascript/candlestick-charts/  
  https://github.com/plotly/plotly.js/issues/5790
- Virtualization references:  
  https://github.com/TanStack/virtual  
  https://tanstack.com/virtual/latest/docs/introduction  
  https://github.com/bvaughn/react-window  
  https://web.dev/articles/virtualize-long-lists-react-window

### Notable UX Patterns
- Keep chart rendering primitive and stateless per row; move heavy logic to batch/global controls.
- Use uniform reveal control as relative offset from entry across all rows.
- Virtualize row DOM aggressively; never render all row charts at once for large sets.
- Prefer update-by-delta workflows over full data replacement when streaming or stepping.

### Anti-Patterns
- Marker-heavy overlays on very large series without profiling.
- Replacing full series data repeatedly (`setData`) in hot paths.
- Showing future “ghost” candles or padded empty future area in blind mode.
- Treating timezone conversion as an afterthought in per-row review tools.

### Commit / Issue Lessons (Applied)
- `lightweight-charts` marker scaling/perf pitfalls on large datasets and marker edge cases under updates/zoom (issues #1808, #1990, #2000) suggest minimizing marker primitives and keeping row datasets clipped.
- `lightweight-charts` v5 upgrade notes (#1791) highlight API/plugin shifts; keep our chart wrapper isolated to reduce migration risk.
- ECharts candlestick docs/changelog show explicit `large` / `progressive` pathways and defaults; confirms alternative strength for ultra-large single-chart scenarios.
- Plotly performance docs/issues show SVG default + large-data and WebGL constraints; supports avoiding Plotly for dense, many-panel candlestick training layouts.

## 4) Licensing and Reuse Policy

- Default stance: no direct code reuse from surveyed OSS projects.
- Current implementation: original code in MCAS only; no copied snippets.
- Compatibility notes (if reuse is ever proposed):
  - `lightweight-charts`: Apache-2.0
  - Apache ECharts: Apache-2.0
  - Plotly.js: MIT
  - TanStack Virtual: MIT
- If direct reuse is proposed later, perform file-level attribution + NOTICE/license inclusion review before merge.

## 5) Practical Roadmap Fit for MCAS

- v1 now: per-opp viewer + forward-blind survey stack + prediction gating + virtualization + presentation layout.
- v1.1 next: DB-persisted predictions + scorer endpoints + keyboard workflows.
- Later: multi-timeframe synchronized stacks and shareable annotation/replay packs.
