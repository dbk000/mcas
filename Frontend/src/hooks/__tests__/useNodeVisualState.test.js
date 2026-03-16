import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveVisualState } from '../useNodeVisualState.js'

test('returns default state with no inputs', () => {
  const result = resolveVisualState()

  assert.equal(result.primary, 'default')
  assert.deepEqual(result.badges, [])
})

test('returns default state with all false inputs', () => {
  const result = resolveVisualState({
    isSelected: false,
    isHighlighted: false,
    isDimmed: false,
    isAlert: false,
  })

  assert.equal(result.primary, 'default')
})

test('selected beats everything', () => {
  const result = resolveVisualState({
    isSelected: true,
    isAlert: true,
    isHighlighted: true,
    isDimmed: true,
    isSearchMatch: true,
  })

  assert.equal(result.primary, 'selected')
})

test('alert beats highlighted and dimmed', () => {
  const result = resolveVisualState({
    isAlert: true,
    isHighlighted: true,
    isDimmed: true,
  })

  assert.equal(result.primary, 'alert')
})

test('search-match beats highlighted and dimmed', () => {
  const result = resolveVisualState({
    isSearchMatch: true,
    isHighlighted: true,
    isDimmed: true,
  })

  assert.equal(result.primary, 'search-match')
})

test('highlighted beats dimmed', () => {
  const result = resolveVisualState({
    isHighlighted: true,
    isDimmed: true,
  })

  assert.equal(result.primary, 'highlighted')
})

test('dimmed is lowest priority', () => {
  const result = resolveVisualState({ isDimmed: true })

  assert.equal(result.primary, 'dimmed')
})

test('confirmed_stale produces staleness-critical badge', () => {
  const result = resolveVisualState({ stalenessState: 'confirmed_stale' })

  assert.ok(result.badges.includes('staleness-critical'))
})

test('potentially_stale produces staleness-warning badge', () => {
  const result = resolveVisualState({ stalenessState: 'potentially_stale' })

  assert.ok(result.badges.includes('staleness-warning'))
})

test('clean staleness produces no staleness badge', () => {
  const result = resolveVisualState({ stalenessState: 'clean' })

  assert.ok(!result.badges.includes('staleness-critical'))
  assert.ok(!result.badges.includes('staleness-warning'))
})

test('failing health produces health-failing badge', () => {
  const result = resolveVisualState({ healthStatus: 'failing' })

  assert.ok(result.badges.includes('health-failing'))
})

test('degraded health produces health-degraded badge', () => {
  const result = resolveVisualState({ healthStatus: 'degraded' })

  assert.ok(result.badges.includes('health-degraded'))
})

test('composition membership produces badge', () => {
  const result = resolveVisualState({ inComposition: true })

  assert.ok(result.badges.includes('composition-member'))
})

test('experiment membership produces badge', () => {
  const result = resolveVisualState({ inExperiment: true })

  assert.ok(result.badges.includes('experiment-member'))
})

test('draft membership produces badge', () => {
  const result = resolveVisualState({ inDraft: true })

  assert.ok(result.badges.includes('draft-member'))
})

test('pressure wave produces badge', () => {
  const result = resolveVisualState({ pressureWaveActive: true })

  assert.ok(result.badges.includes('pressure-wave'))
})

test('selected node with staleness and composition membership', () => {
  const result = resolveVisualState({
    isSelected: true,
    stalenessState: 'confirmed_stale',
    inComposition: true,
  })

  assert.equal(result.primary, 'selected')
  assert.ok(result.badges.includes('staleness-critical'))
  assert.ok(result.badges.includes('composition-member'))
})

test('dimmed node still shows badges', () => {
  const result = resolveVisualState({
    isDimmed: true,
    stalenessState: 'potentially_stale',
    inExperiment: true,
  })

  assert.equal(result.primary, 'dimmed')
  assert.ok(result.badges.includes('staleness-warning'))
  assert.ok(result.badges.includes('experiment-member'))
})

test('badges accumulate independently', () => {
  const result = resolveVisualState({
    stalenessState: 'confirmed_stale',
    healthStatus: 'failing',
    inComposition: true,
    inExperiment: true,
    inDraft: true,
    pressureWaveActive: true,
  })

  assert.equal(result.badges.length, 6)
})
