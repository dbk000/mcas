import { useState, useEffect, useCallback, useMemo } from 'react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Plus, Trash2, Play, Pause, AlertTriangle, ChevronDown, ChevronRight,
  Zap, TrendingUp, Activity, BarChart3, Eye, Wand2, X,
} from 'lucide-react'

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

const TEMPLATES = [
  {
    name: 'Oversold Bounce',
    description: 'Finds symbols where momentum has become extremely bearish while volume confirms interest.',
    icon: TrendingUp,
    conditions: [
      { field: 'rsi', op: 'crosses_below', value: 30 },
      { field: 'volume_z', op: '>', value: 1.5 },
    ],
    timeframes: ['1h', '4h'],
    cooldown_minutes: 60,
  },
  {
    name: 'Momentum Breakout',
    description: 'Bollinger Band squeeze with volume confirmation and bullish bias.',
    icon: Zap,
    conditions: [
      { field: 'bb_width', op: '<', value: 0.03 },
      { field: 'volume_z', op: '>', value: 2.0 },
      { field: 'rsi', op: '>', value: 50 },
    ],
    timeframes: ['1h'],
    cooldown_minutes: 120,
  },
  {
    name: 'Trend Continuation',
    description: 'Price above long-term trend, momentum positive but not overbought.',
    icon: Activity,
    conditions: [
      { field: 'close', op: '>', ref_field: 'sma_200' },
      { field: 'rsi', op: '>', value: 40 },
      { field: 'rsi', op: '<', value: 70 },
      { field: 'macd_histogram', op: '>', value: 0 },
    ],
    timeframes: ['4h'],
    cooldown_minutes: 240,
  },
  {
    name: 'Volume Spike Alert',
    description: 'Simple volume anomaly detector. Attention flag, not a directional signal.',
    icon: BarChart3,
    conditions: [
      { field: 'volume_z', op: '>', value: 3.0 },
    ],
    timeframes: ['15m', '1h'],
    cooldown_minutes: 30,
  },
  {
    name: 'Dormancy Break',
    description: "Uses MCAS's proprietary dormancy detection \u2014 simplified from D1's 8-component state machine.",
    icon: Eye,
    conditions: [
      { field: 'dormancy_score', op: 'crosses_above', value: 0.7 },
      { field: 'volume_z', op: '>', value: 1.5 },
      { field: 'bb_width', op: '<', value: 0.04 },
    ],
    timeframes: ['1h', '4h'],
    cooldown_minutes: 120,
  },
]

const ALL_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d']

const OPERATORS = [
  { value: '>', label: '>' },
  { value: '<', label: '<' },
  { value: '>=', label: '>=' },
  { value: '<=', label: '<=' },
  { value: 'crosses_above', label: 'Crosses Above' },
  { value: 'crosses_below', label: 'Crosses Below' },
]

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConditionRow({ condition, index, onChange, onRemove, indicatorMenu }) {
  const popularFields = useMemo(
    () => (indicatorMenu || []).filter((f) => f.popularity_rank).sort((a, b) => a.popularity_rank - b.popularity_rank),
    [indicatorMenu],
  )
  const categories = useMemo(() => {
    const cats = {}
    for (const item of indicatorMenu || []) {
      if (!item.popularity_rank) {
        const cat = item.category || 'Other'
        if (!cats[cat]) cats[cat] = []
        cats[cat].push(item)
      }
    }
    return cats
  }, [indicatorMenu])

  const isRefMode = condition.ref_field !== undefined && condition.ref_field !== null

  const updateField = (key, value) => {
    const updated = { ...condition, [key]: value }
    // Toggle between value and ref_field mode
    if (key === '_mode') {
      if (value === 'ref') {
        delete updated.value
        updated.ref_field = updated.ref_field || ''
      } else {
        delete updated.ref_field
        updated.value = updated.value ?? ''
      }
      delete updated._mode
    }
    onChange(index, updated)
  }

  return (
    <div className="flex items-center gap-2 rounded-lg border border-terminal-700/60 bg-terminal-900/40 px-3 py-2">
      <span className="shrink-0 text-xs font-medium text-terminal-500 tabular-nums w-5">
        {index + 1}
      </span>

      {/* Field selector */}
      <select
        className="rounded border border-terminal-700 bg-terminal-800 px-2 py-1.5 text-sm text-terminal-100 min-w-[140px]"
        value={condition.field || ''}
        onChange={(e) => updateField('field', e.target.value)}
      >
        <option value="">Select field...</option>
        {popularFields.length > 0 && (
          <optgroup label="Popular">
            {popularFields.map((f) => (
              <option key={f.field} value={f.field}>{f.field}</option>
            ))}
          </optgroup>
        )}
        {Object.entries(categories).map(([cat, fields]) => (
          <optgroup key={cat} label={cat}>
            {fields.map((f) => (
              <option key={f.field} value={f.field}>{f.field}</option>
            ))}
          </optgroup>
        ))}
      </select>

      {/* Operator */}
      <select
        className="rounded border border-terminal-700 bg-terminal-800 px-2 py-1.5 text-sm text-terminal-100"
        value={condition.op || '>'}
        onChange={(e) => updateField('op', e.target.value)}
      >
        {OPERATORS.map((op) => (
          <option key={op.value} value={op.value}>{op.label}</option>
        ))}
      </select>

      {/* Mode toggle (value vs indicator) */}
      <button
        type="button"
        className={`rounded px-2 py-1.5 text-xs font-medium ${
          isRefMode
            ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
            : 'bg-terminal-700/40 text-terminal-400 border border-terminal-700/60'
        }`}
        onClick={() => updateField('_mode', isRefMode ? 'value' : 'ref')}
        title={isRefMode ? 'Comparing vs indicator' : 'Comparing vs fixed value'}
      >
        {isRefMode ? 'vs Field' : 'vs Value'}
      </button>

      {/* Value or ref_field input */}
      {isRefMode ? (
        <select
          className="rounded border border-terminal-700 bg-terminal-800 px-2 py-1.5 text-sm text-terminal-100 min-w-[140px]"
          value={condition.ref_field || ''}
          onChange={(e) => updateField('ref_field', e.target.value)}
        >
          <option value="">Select field...</option>
          {popularFields.map((f) => (
            <option key={f.field} value={f.field}>{f.field}</option>
          ))}
          {Object.entries(categories).map(([cat, fields]) => (
            <optgroup key={cat} label={cat}>
              {fields.map((f) => (
                <option key={f.field} value={f.field}>{f.field}</option>
              ))}
            </optgroup>
          ))}
        </select>
      ) : (
        <input
          type="number"
          step="any"
          className="w-24 rounded border border-terminal-700 bg-terminal-800 px-2 py-1.5 text-sm text-terminal-100"
          placeholder="Value"
          value={condition.value ?? ''}
          onChange={(e) => updateField('value', e.target.value === '' ? '' : Number(e.target.value))}
        />
      )}

      <button
        type="button"
        className="ml-auto text-terminal-500 hover:text-red-400"
        onClick={() => onRemove(index)}
      >
        <Trash2 size={14} />
      </button>
    </div>
  )
}

function HealthCheckBadge({ health }) {
  if (!health) return null

  const { would_fire_now, symbols_sampled, estimated_fire_rate_pct, warning } = health

  const color = warning
    ? estimated_fire_rate_pct >= 50 ? 'text-red-400 bg-red-500/10' : 'text-amber-400 bg-amber-500/10'
    : 'text-green-400 bg-green-500/10'

  return (
    <div className={`rounded-lg border border-terminal-700/60 px-3 py-2 text-sm ${color}`}>
      <div className="flex items-center gap-2">
        {warning && <AlertTriangle size={14} />}
        <span className="font-medium">
          {would_fire_now} of {symbols_sampled} symbols match ({estimated_fire_rate_pct}%)
        </span>
      </div>
      {warning && <p className="mt-1 text-xs opacity-80">{warning}</p>}
    </div>
  )
}

function StrategyCard({ strategy, onTogglePause, onDelete }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-lg border border-terminal-700/60 bg-terminal-900/40 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => setExpanded(!expanded)} className="text-terminal-400">
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </button>
          <div>
            <h3 className="text-sm font-semibold text-terminal-100">{strategy.name}</h3>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-terminal-500">
                {strategy.conditions.length} condition{strategy.conditions.length !== 1 ? 's' : ''}
              </span>
              <span className="text-xs text-terminal-600">&middot;</span>
              <span className="text-xs text-terminal-500">
                {(strategy.timeframes || []).join(', ')}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Hit counts */}
          <div className="text-right">
            <div className="text-sm font-medium text-terminal-100 tabular-nums">{strategy.hits_24h || 0}</div>
            <div className="text-xs text-terminal-500">24h hits</div>
          </div>
          <div className="text-right">
            <div className="text-sm font-medium text-terminal-100 tabular-nums">{strategy.hits_7d || 0}</div>
            <div className="text-xs text-terminal-500">7d hits</div>
          </div>

          {/* Status badges */}
          {strategy.auto_paused && (
            <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-xs font-medium text-amber-400">
              Auto-paused
            </span>
          )}

          {/* Actions */}
          <button
            className="rounded p-1.5 text-terminal-400 hover:bg-terminal-700/40 hover:text-terminal-200"
            onClick={() => onTogglePause(strategy)}
            title={strategy.auto_paused ? 'Resume' : 'Pause'}
          >
            {strategy.auto_paused ? <Play size={14} /> : <Pause size={14} />}
          </button>
          <button
            className="rounded p-1.5 text-terminal-400 hover:bg-red-500/10 hover:text-red-400"
            onClick={() => onDelete(strategy)}
            title="Delete"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 border-t border-terminal-700/40 pt-3 space-y-1">
          {strategy.conditions.map((c, i) => (
            <div key={i} className="text-xs text-terminal-400 font-mono">
              {c.field} {c.op} {c.ref_field ? c.ref_field : c.value}
            </div>
          ))}
          <div className="text-xs text-terminal-500 mt-2">
            Cooldown: {strategy.cooldown_minutes}m &middot; Filter: {strategy.symbol_filter || 'all_monitored'}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function StrategyBuilder() {
  const { get, post, request, isConnected } = useApi()
  const { addToast } = useToast()

  // State
  const [strategies, setStrategies] = useState([])
  const [indicatorMenu, setIndicatorMenu] = useState([])
  const [loading, setLoading] = useState(true)
  const [showComposer, setShowComposer] = useState(false)

  // Composer state
  const [name, setName] = useState('')
  const [conditions, setConditions] = useState([{ field: '', op: '>', value: '' }])
  const [timeframes, setTimeframes] = useState(['1h', '4h'])
  const [cooldown, setCooldown] = useState(60)
  const [symbolFilter, setSymbolFilter] = useState('all_monitored')
  const [saving, setSaving] = useState(false)
  const [healthCheck, setHealthCheck] = useState(null)

  // Fetch strategies and indicator menu
  const loadData = useCallback(async () => {
    if (!isConnected) return
    try {
      const [stratRes, menuRes] = await Promise.all([
        get('/v1/user-strategies'),
        get('/v1/indicators/menu'),
      ])
      setStrategies(stratRes?.data || [])
      setIndicatorMenu(menuRes?.data || [])
    } catch (err) {
      /* silent */
    } finally {
      setLoading(false)
    }
  }, [get, isConnected])

  useEffect(() => { loadData() }, [loadData])

  // Condition management
  const addCondition = () => {
    setConditions([...conditions, { field: '', op: '>', value: '' }])
  }
  const removeCondition = (idx) => {
    setConditions(conditions.filter((_, i) => i !== idx))
  }
  const updateCondition = (idx, updated) => {
    setConditions(conditions.map((c, i) => (i === idx ? updated : c)))
  }

  // Apply template
  const applyTemplate = (template) => {
    setName(template.name)
    setConditions(template.conditions.map((c) => ({ ...c })))
    setTimeframes([...template.timeframes])
    setCooldown(template.cooldown_minutes)
    setSymbolFilter('all_monitored')
    setHealthCheck(null)
    setShowComposer(true)
  }

  // Save strategy
  const saveStrategy = async () => {
    setSaving(true)
    try {
      const body = {
        name,
        conditions: conditions.map((c) => {
          const clean = { field: c.field, op: c.op }
          if (c.ref_field !== undefined && c.ref_field !== null && c.ref_field !== '') {
            clean.ref_field = c.ref_field
          } else {
            clean.value = Number(c.value)
          }
          return clean
        }),
        timeframes,
        cooldown_minutes: cooldown,
        symbol_filter: symbolFilter,
      }

      const res = await post('/v1/user-strategies', body)
      if (res?.data) {
        setHealthCheck(res.data.health_check)
        addToast({ type: 'success', message: `Strategy "${name}" created` })
        setShowComposer(false)
        resetComposer()
        loadData()
      }
    } catch (err) {
      addToast({ type: 'error', message: err?.message || 'Failed to create strategy' })
    } finally {
      setSaving(false)
    }
  }

  const resetComposer = () => {
    setName('')
    setConditions([{ field: '', op: '>', value: '' }])
    setTimeframes(['1h', '4h'])
    setCooldown(60)
    setSymbolFilter('all_monitored')
    setHealthCheck(null)
  }

  // Toggle pause
  const togglePause = async (strategy) => {
    try {
      await request('/v1/strategies/' + strategy.id, {
        method: 'PUT',
        body: { auto_paused: !strategy.auto_paused },
      })
      addToast({
        type: 'success',
        message: strategy.auto_paused ? 'Strategy resumed' : 'Strategy paused',
      })
      loadData()
    } catch (err) {
      addToast({ type: 'error', message: err?.message || 'Failed to update strategy' })
    }
  }

  // Delete strategy
  const deleteStrategy = async (strategy) => {
    try {
      await request('/v1/strategies/' + strategy.id, { method: 'DELETE' })
      addToast({ type: 'success', message: `Strategy "${strategy.name}" deleted` })
      loadData()
    } catch (err) {
      addToast({ type: 'error', message: err?.message || 'Failed to delete strategy' })
    }
  }

  // Timeframe toggle
  const toggleTimeframe = (tf) => {
    setTimeframes((prev) =>
      prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf],
    )
  }

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="size-8 animate-spin rounded-full border-2 border-terminal-600 border-t-amber-400" />
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-terminal-100">Strategy Builder</h1>
          <p className="text-sm text-terminal-500">
            Compose custom scanning rules from {indicatorMenu.length} indicators
          </p>
        </div>
        <button
          className="flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-terminal-900 hover:bg-amber-400"
          onClick={() => { resetComposer(); setShowComposer(true) }}
        >
          <Plus size={16} />
          New Strategy
        </button>
      </div>

      {/* Composer Modal */}
      {showComposer && (
        <div className="rounded-lg border border-amber-500/30 bg-terminal-900/80 p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-terminal-100">
              {name ? `Editing: ${name}` : 'New Strategy'}
            </h2>
            <button onClick={() => setShowComposer(false)} className="text-terminal-500 hover:text-terminal-300">
              <X size={16} />
            </button>
          </div>

          {/* Name */}
          <input
            type="text"
            className="w-full rounded border border-terminal-700 bg-terminal-800 px-3 py-2 text-sm text-terminal-100 placeholder:text-terminal-600"
            placeholder="Strategy name..."
            value={name}
            onChange={(e) => setName(e.target.value)}
          />

          {/* Conditions */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium uppercase tracking-wider text-terminal-500">
                Conditions (AND)
              </span>
              <button
                className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300"
                onClick={addCondition}
              >
                <Plus size={12} /> Add
              </button>
            </div>
            {conditions.map((cond, i) => (
              <ConditionRow
                key={i}
                condition={cond}
                index={i}
                onChange={updateCondition}
                onRemove={removeCondition}
                indicatorMenu={indicatorMenu}
              />
            ))}
          </div>

          {/* Settings row */}
          <div className="flex flex-wrap items-end gap-4">
            {/* Timeframes */}
            <div>
              <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-terminal-500">
                Timeframes
              </label>
              <div className="flex gap-1">
                {ALL_TIMEFRAMES.map((tf) => (
                  <button
                    key={tf}
                    className={`rounded px-2.5 py-1 text-xs font-medium ${
                      timeframes.includes(tf)
                        ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                        : 'bg-terminal-800 text-terminal-500 border border-terminal-700/60'
                    }`}
                    onClick={() => toggleTimeframe(tf)}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>

            {/* Cooldown */}
            <div>
              <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-terminal-500">
                Cooldown (min)
              </label>
              <input
                type="number"
                min={5}
                className="w-20 rounded border border-terminal-700 bg-terminal-800 px-2 py-1.5 text-sm text-terminal-100"
                value={cooldown}
                onChange={(e) => setCooldown(Math.max(5, Number(e.target.value)))}
              />
            </div>

            {/* Symbol filter */}
            <div>
              <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-terminal-500">
                Symbols
              </label>
              <input
                type="text"
                className="w-48 rounded border border-terminal-700 bg-terminal-800 px-2 py-1.5 text-sm text-terminal-100"
                placeholder="all_monitored"
                value={symbolFilter}
                onChange={(e) => setSymbolFilter(e.target.value)}
              />
            </div>
          </div>

          {/* Health check */}
          {healthCheck && <HealthCheckBadge health={healthCheck} />}

          {/* Actions */}
          <div className="flex gap-2 pt-2">
            <button
              className="flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-terminal-900 hover:bg-amber-400 disabled:opacity-50"
              onClick={saveStrategy}
              disabled={saving || !name || conditions.length === 0 || timeframes.length === 0}
            >
              {saving ? 'Saving...' : 'Create Strategy'}
            </button>
            <button
              className="rounded-lg border border-terminal-700 px-4 py-2 text-sm text-terminal-400 hover:text-terminal-200"
              onClick={() => setShowComposer(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Templates */}
      {strategies.length === 0 && !showComposer && (
        <div className="space-y-4">
          <div className="rounded-lg border border-terminal-700/40 bg-terminal-900/30 p-4 text-center">
            <Wand2 className="mx-auto mb-2 text-amber-400" size={24} />
            <p className="text-sm text-terminal-300">
              No strategies yet. Start from a template or build your own.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {TEMPLATES.map((t) => (
              <button
                key={t.name}
                className="rounded-lg border border-terminal-700/60 bg-terminal-900/40 p-4 text-left hover:border-amber-500/40 hover:bg-terminal-800/40 transition-colors"
                onClick={() => applyTemplate(t)}
              >
                <div className="flex items-center gap-2 mb-2">
                  <t.icon size={16} className="text-amber-400" />
                  <h3 className="text-sm font-semibold text-terminal-100">{t.name}</h3>
                </div>
                <p className="text-xs text-terminal-500 line-clamp-2">{t.description}</p>
                <div className="mt-2 flex gap-1">
                  {t.conditions.map((c, i) => (
                    <span
                      key={i}
                      className="rounded bg-terminal-800 px-1.5 py-0.5 text-[10px] font-mono text-terminal-400"
                    >
                      {c.field}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Strategy list */}
      {strategies.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-xs font-medium uppercase tracking-wider text-terminal-500">
            Your Strategies ({strategies.length})
          </h2>
          {strategies.map((s) => (
            <StrategyCard
              key={s.id}
              strategy={s}
              onTogglePause={togglePause}
              onDelete={deleteStrategy}
            />
          ))}
        </div>
      )}

      {/* Templates (when strategies exist) */}
      {strategies.length > 0 && !showComposer && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium uppercase tracking-wider text-terminal-500">
            Templates
          </h2>
          <div className="flex flex-wrap gap-2">
            {TEMPLATES.map((t) => (
              <button
                key={t.name}
                className="flex items-center gap-1.5 rounded-lg border border-terminal-700/60 bg-terminal-900/40 px-3 py-1.5 text-xs text-terminal-400 hover:border-amber-500/40 hover:text-terminal-200"
                onClick={() => applyTemplate(t)}
              >
                <t.icon size={12} />
                {t.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
