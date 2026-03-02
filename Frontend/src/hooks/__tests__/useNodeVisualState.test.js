/**
 * Unit tests for resolveVisualState — the pure function that determines
 * how every pipeline node should look given its state dimensions.
 *
 * Run with: npx vitest run src/hooks/__tests__/useNodeVisualState.test.js
 */
import { describe, it, expect } from 'vitest'
import { resolveVisualState } from '../useNodeVisualState'

describe('resolveVisualState', () => {
  it('returns default state with no inputs', () => {
    const result = resolveVisualState()
    expect(result.primary).toBe('default')
    expect(result.badges).toEqual([])
  })

  it('returns default state with all false inputs', () => {
    const result = resolveVisualState({
      isSelected: false,
      isHighlighted: false,
      isDimmed: false,
      isAlert: false,
    })
    expect(result.primary).toBe('default')
  })

  // --- Priority stack tests ---

  it('selected beats everything', () => {
    const result = resolveVisualState({
      isSelected: true,
      isAlert: true,
      isHighlighted: true,
      isDimmed: true,
      isSearchMatch: true,
    })
    expect(result.primary).toBe('selected')
  })

  it('alert beats highlighted and dimmed', () => {
    const result = resolveVisualState({
      isAlert: true,
      isHighlighted: true,
      isDimmed: true,
    })
    expect(result.primary).toBe('alert')
  })

  it('search-match beats highlighted and dimmed', () => {
    const result = resolveVisualState({
      isSearchMatch: true,
      isHighlighted: true,
      isDimmed: true,
    })
    expect(result.primary).toBe('search-match')
  })

  it('highlighted beats dimmed', () => {
    const result = resolveVisualState({
      isHighlighted: true,
      isDimmed: true,
    })
    expect(result.primary).toBe('highlighted')
  })

  it('dimmed is lowest priority', () => {
    const result = resolveVisualState({ isDimmed: true })
    expect(result.primary).toBe('dimmed')
  })

  // --- Badge tests ---

  it('confirmed_stale produces staleness-critical badge', () => {
    const result = resolveVisualState({ stalenessState: 'confirmed_stale' })
    expect(result.badges).toContain('staleness-critical')
  })

  it('potentially_stale produces staleness-warning badge', () => {
    const result = resolveVisualState({ stalenessState: 'potentially_stale' })
    expect(result.badges).toContain('staleness-warning')
  })

  it('clean staleness produces no staleness badge', () => {
    const result = resolveVisualState({ stalenessState: 'clean' })
    expect(result.badges).not.toContain('staleness-critical')
    expect(result.badges).not.toContain('staleness-warning')
  })

  it('failing health produces health-failing badge', () => {
    const result = resolveVisualState({ healthStatus: 'failing' })
    expect(result.badges).toContain('health-failing')
  })

  it('degraded health produces health-degraded badge', () => {
    const result = resolveVisualState({ healthStatus: 'degraded' })
    expect(result.badges).toContain('health-degraded')
  })

  it('composition membership produces badge', () => {
    const result = resolveVisualState({ inComposition: true })
    expect(result.badges).toContain('composition-member')
  })

  it('experiment membership produces badge', () => {
    const result = resolveVisualState({ inExperiment: true })
    expect(result.badges).toContain('experiment-member')
  })

  it('draft membership produces badge', () => {
    const result = resolveVisualState({ inDraft: true })
    expect(result.badges).toContain('draft-member')
  })

  it('pressure wave produces badge', () => {
    const result = resolveVisualState({ pressureWaveActive: true })
    expect(result.badges).toContain('pressure-wave')
  })

  // --- Combined state tests ---

  it('selected node with staleness and composition membership', () => {
    const result = resolveVisualState({
      isSelected: true,
      stalenessState: 'confirmed_stale',
      inComposition: true,
    })
    expect(result.primary).toBe('selected')
    expect(result.badges).toContain('staleness-critical')
    expect(result.badges).toContain('composition-member')
  })

  it('dimmed node still shows badges', () => {
    const result = resolveVisualState({
      isDimmed: true,
      stalenessState: 'potentially_stale',
      inExperiment: true,
    })
    expect(result.primary).toBe('dimmed')
    expect(result.badges).toContain('staleness-warning')
    expect(result.badges).toContain('experiment-member')
  })

  it('badges accumulate independently', () => {
    const result = resolveVisualState({
      stalenessState: 'confirmed_stale',
      healthStatus: 'failing',
      inComposition: true,
      inExperiment: true,
      inDraft: true,
      pressureWaveActive: true,
    })
    expect(result.badges).toHaveLength(6)
  })
})
