import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { averageDailyPF, groupPnLByDay, profitFactor } from '../src/utils/pf.js'

const metricsDemo = JSON.parse(
  readFileSync(new URL('../public/curation/metrics-demo.v1.json', import.meta.url), 'utf8')
)

test('profitFactor returns a finite ratio when profit and loss are both present', () => {
  assert.equal(profitFactor(240, 120), 2)
})

test('profitFactor returns Infinity for profitable zero-loss days', () => {
  assert.equal(profitFactor(120, 0), Infinity)
})

test('averageDailyPF returns null for empty input', () => {
  const result = averageDailyPF([], 'exit_ts', 'pnl_net')

  assert.equal(result.value, null)
  assert.equal(result.totalTradingDays, 0)
  assert.equal(result.daysCountedFinite, 0)
  assert.equal(result.daysExcludedInfiniteProfitable, 0)
})

test('groupPnLByDay groups multiple trades into day buckets', () => {
  const grouped = groupPnLByDay(metricsDemo.run_detail.trades, 'exit_ts', 'pnl_net')

  assert.equal(grouped.length, 4)
  assert.deepEqual(grouped[0], {
    day: '2026-03-10',
    grossProfit: 120,
    grossLossAbs: 50,
    trades: 2,
  })
})

test('averageDailyPF matches the run-detail fixture expectations', () => {
  const result = averageDailyPF(
    metricsDemo.run_detail.trades,
    metricsDemo.run_detail.expected.timestamp_field,
    metricsDemo.run_detail.expected.pnl_field,
  )

  assert.equal(Number(result.value.toFixed(2)), metricsDemo.run_detail.expected.avg_daily_pf)
  assert.equal(result.daysCountedFinite, metricsDemo.run_detail.expected.days_counted_finite)
  assert.equal(result.daysExcludedInfiniteProfitable, metricsDemo.run_detail.expected.days_excluded_infinite_profitable)
})

test('averageDailyPF matches the simulation fixture expectations with timestamp fallback', () => {
  const normalized = metricsDemo.simulation_results.results.map((item) => ({
    ...item,
    resolved_ts: item.exit_ts || item.created_at,
  }))

  const result = averageDailyPF(
    normalized,
    'resolved_ts',
    metricsDemo.simulation_results.expected.pnl_field,
  )

  assert.equal(Number(result.value.toFixed(2)), metricsDemo.simulation_results.expected.avg_daily_pf)
  assert.equal(result.daysCountedFinite, metricsDemo.simulation_results.expected.days_counted_finite)
  assert.equal(result.daysExcludedInfiniteProfitable, metricsDemo.simulation_results.expected.days_excluded_infinite_profitable)
})
