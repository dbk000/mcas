import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ageLabel,
  isStale,
  normalizeCandles,
  number,
  screenUrl,
  validSelection,
} from '../src/pages/trading-lab/model.js'

test('missing values stay missing; zero is a real observation', () => {
  for (const value of [null, undefined, '', true, false, 'invalid', Infinity])
    assert.equal(number(value), null)
  assert.equal(number(0), 0)
  assert.equal(number('1.25'), 1.25)
})

test('source age supports seconds, milliseconds and ISO; unknown and future clocks are not current', () => {
  const now = Date.parse('2026-09-25T14:00:00Z')
  assert.equal(ageLabel((now - 30000) / 1000, now), '30s ago')
  assert.equal(ageLabel(new Date(now - 120000).toISOString(), now), '2m ago')
  assert.equal(isStale(null, 60, now), true)
  assert.equal(isStale(now - 61000, 60, now), true)
  assert.equal(isStale(now + 61000, 60, now), true)
  assert.equal(isStale(now - 59000, 60, now), false)
})

test('chart drops forming, missing-finality and corrupt bars; sorts and deduplicates timestamps', () => {
  const bar = {
    timestamp: 1700000000000,
    open: 10,
    high: 12,
    low: 9,
    close: 11,
    volume: 10,
    is_final: true,
  }
  const result = normalizeCandles([
    { ...bar, timestamp: bar.timestamp + 60000 },
    bar,
    { ...bar, close: 10.5 },
    { ...bar, timestamp: bar.timestamp + 120000, is_final: false },
    { ...bar, timestamp: bar.timestamp + 180000, is_final: undefined },
    { ...bar, timestamp: bar.timestamp + 240000, high: 5 },
    { ...bar, timestamp: bar.timestamp + 300000, close: null },
  ])
  assert.equal(result.length, 2)
  assert.equal(result[0].close, 10.5)
  assert.ok(result[0].time < result[1].time)
})

test('selection messages are scoped and validated; screen links carry no credential', () => {
  const value = { scope: 'backend-a', symbol: 'BTCUSDT', tf: '1h', sentAt: 1 }
  assert.equal(validSelection(value, 'backend-a'), true)
  assert.equal(validSelection(value, 'backend-b'), false)
  assert.equal(
    validSelection({ ...value, symbol: '<script>' }, 'backend-a'),
    false
  )
  const url = screenUrl(
    {
      id: 'wall-b',
      name: 'Wall B',
      view: 'investigation',
      symbol: 'ETHUSDT',
      follow: false,
    },
    { demo: false, tf: '1h', scale: 150, theme: 'light' }
  )
  const parsed = new URL(url, 'http://localhost')
  assert.equal(parsed.pathname, '/app/lab')
  assert.equal(parsed.searchParams.get('symbol'), 'ETHUSDT')
  assert.equal(parsed.searchParams.get('follow'), '0')
  assert.equal(parsed.searchParams.has('apiKey'), false)
})
