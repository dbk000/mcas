import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { useQueueStatus, useMarkets, useTimeframes, usePaperStatus, useDataAvailability } from '../hooks/useData'
import { getLogger } from '../utils/logger'
import {
  Card, CardHeader, CardTitle, CardContent,
  Badge, StatusBadge, Input, Select, Toggle,
  Spinner, EmptyState, RefreshControl, Tabs
} from '../components/ui'
import { formatDateTime, formatRelativeTime } from '../utils/formatters'
import {
  Play, Square, Zap, Clock, Database,
  TrendingUp, AlertCircle, Loader2, Send
} from 'lucide-react'

const log = getLogger('control_plane')

// Form field component
function FormField({ label, children, hint }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-terminal-300">{label}</label>
      {children}
      {hint && <p className="text-xs text-terminal-500">{hint}</p>}
    </div>
  )
}

// Queue item component
function QueueItem({ item, onCancel }) {
  const typeColors = {
    SCAN: 'info',
    BACKTEST: 'warning',
    PAPER: 'success',
  }
  
  return (
    <div className="flex items-center justify-between p-3 bg-terminal-800/50 rounded-lg border border-terminal-700/50">
      <div className="flex items-center gap-3">
        <Badge variant={typeColors[item.run_type] || 'default'}>
          {item.run_type}
        </Badge>
        <div>
          <div className="font-medium text-terminal-100">{item.run_name}</div>
          <div className="text-xs text-terminal-500 font-mono">
            {item.run_id.slice(0, 8)}
          </div>
        </div>
      </div>
      
      <div className="flex items-center gap-4">
        <div className="text-right">
          <div className="text-sm">
            <StatusBadge status={item.status} />
          </div>
          <div className="text-xs text-terminal-500">
            {formatRelativeTime(item.queued_at)}
          </div>
        </div>
        
        {(item.status === 'QUEUED' || item.status === 'CLAIMED') && (
          <button
            onClick={() => onCancel(item.run_id)}
            className="p-1.5 text-terminal-400 hover:text-red-400 hover:bg-red-400/10 rounded transition-colors"
            title="Cancel"
          >
            <Square className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  )
}

// Scan form component
function ScanForm({ onSubmit, loading, markets, timeframes }) {
  const [name, setName] = useState('')
  const [setupIds, setSetupIds] = useState(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'])
  const [topN, setTopN] = useState(10)
  const [priority, setPriority] = useState(0)
  
  const handleSubmit = (e) => {
    e.preventDefault()
    onSubmit({
      name: name || undefined,
      setup_ids: setupIds,
      top_n: topN,
      priority: priority,
    })
  }
  
  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormField label="Run Name (optional)" hint="Leave blank for auto-generated name">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="scan_manual_20240115"
        />
      </FormField>
      
      <FormField label="Setups">
        <div className="flex flex-wrap gap-3">
          {[
            { id: 'S1', label: 'S1 (BB Squeeze)' },
            { id: 'S2', label: 'S2 (Golden Cross)' },
            { id: 'S3', label: 'S3 (RSI Divergence)' },
            { id: 'S4', label: 'S4 (VWAP Reversion)' },
            { id: 'S5', label: 'S5 (S/R Breakout)' },
            { id: 'S6', label: 'S6 (Trend Pullback)' },
            { id: 'S7', label: 'S7 (Donchian Breakout)' },
          ].map(s => (
            <label key={s.id} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={setupIds.includes(s.id)}
                onChange={(e) => {
                  if (e.target.checked) setSetupIds([...setupIds, s.id])
                  else setSetupIds(setupIds.filter(x => x !== s.id))
                }}
                className="rounded border-terminal-600 bg-terminal-800 text-amber-500 focus:ring-amber-500"
              />
              <span className="text-sm">{s.label}</span>
            </label>
          ))}
        </div>
      </FormField>

      <div className="grid grid-cols-2 gap-4">
        <FormField label="Top N Signals" hint="Max signals to return">
          <Input
            type="number"
            min={1}
            max={100}
            value={topN}
            onChange={(e) => setTopN(parseInt(e.target.value) || 10)}
          />
        </FormField>
        
        <FormField label="Priority" hint="0=lowest, 10=highest">
          <Input
            type="number"
            min={0}
            max={10}
            value={priority}
            onChange={(e) => setPriority(parseInt(e.target.value) || 0)}
          />
        </FormField>
      </div>
      
      <button
        type="submit"
        disabled={loading || setupIds.length === 0}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-amber-500 hover:bg-amber-400 disabled:bg-terminal-700 text-terminal-900 font-medium rounded-lg transition-colors"
      >
        {loading ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            Creating...
          </>
        ) : (
          <>
            <Zap className="w-4 h-4" />
            Run Scan
          </>
        )}
      </button>
    </form>
  )
}

// P5-26: Symbol multi-select with typeahead
function SymbolMultiSelect({ selected, onChange, markets }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)

  const allSymbols = (markets?.markets || markets || [])
    .map(m => typeof m === 'string' ? m : m.symbol)
    .filter(Boolean)
    .sort()

  const filtered = query
    ? allSymbols.filter(s => s.toLowerCase().includes(query.toLowerCase()))
    : allSymbols

  const toggle = (sym) => {
    if (selected.includes(sym)) {
      onChange(selected.filter(s => s !== sym))
    } else {
      onChange([...selected, sym])
    }
  }

  const remove = (sym) => onChange(selected.filter(s => s !== sym))

  return (
    <div className="relative">
      {/* Selected chips */}
      <div
        className="flex flex-wrap gap-1.5 p-2 bg-terminal-800 border border-terminal-500 rounded min-h-[42px] cursor-text focus-within:border-amber-500 focus-within:ring-1 focus-within:ring-amber-500/20"
        onClick={() => setOpen(true)}
      >
        {selected.map(sym => (
          <span
            key={sym}
            className="inline-flex items-center gap-1 px-2 py-0.5 bg-amber-500/20 text-amber-400 text-xs font-mono rounded"
          >
            {sym}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); remove(sym) }}
              className="hover:text-amber-200"
            >
              &times;
            </button>
          </span>
        ))}
        <input
          type="text"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 200)}
          placeholder={selected.length === 0 ? 'Type to search symbols...' : ''}
          className="flex-1 min-w-[120px] bg-transparent text-terminal-100 placeholder-terminal-400 outline-none text-sm"
        />
      </div>

      {/* Dropdown */}
      {open && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto bg-terminal-800 border border-terminal-600 rounded-lg shadow-xl">
          {filtered.slice(0, 50).map(sym => (
            <button
              key={sym}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { toggle(sym); setQuery('') }}
              className={`w-full text-left px-3 py-1.5 text-sm font-mono hover:bg-terminal-700 ${
                selected.includes(sym) ? 'text-amber-400 bg-amber-500/10' : 'text-terminal-200'
              }`}
            >
              {sym}
              {selected.includes(sym) && <span className="float-right text-amber-500">&#10003;</span>}
            </button>
          ))}
          {filtered.length > 50 && (
            <div className="px-3 py-1.5 text-xs text-terminal-500">
              {filtered.length - 50} more — type to narrow
            </div>
          )}
        </div>
      )}

      {open && allSymbols.length === 0 && (
        <div className="absolute z-50 mt-1 w-full bg-terminal-800 border border-terminal-600 rounded-lg shadow-xl p-3 text-sm text-terminal-400">
          No markets loaded. Check API connection.
        </div>
      )}
    </div>
  )
}

// P5-27: Data availability indicator
function DataAvailabilityInfo({ symbols, timeframes, startDate, endDate }) {
  const { data } = useDataAvailability(symbols, timeframes, {
    skip: !symbols?.length || !timeframes?.length || !startDate || !endDate,
  })

  if (!data?.availability || !symbols?.length) return null

  const avail = data.availability
  const missing = []
  const partial = []

  for (const sym of symbols) {
    for (const tf of timeframes) {
      const key = `${sym}_${tf}`
      const info = avail[key]
      if (!info || info.bar_count === 0) {
        missing.push(key)
      } else if (info.first_bar > startDate || info.last_bar < endDate) {
        partial.push({ key, first: info.first_bar?.slice(0, 10), last: info.last_bar?.slice(0, 10), bars: info.bar_count })
      }
    }
  }

  if (missing.length === 0 && partial.length === 0) {
    return (
      <div className="p-2.5 bg-green-500/10 border border-green-500/30 rounded text-sm text-green-400">
        Data available for all {symbols.length} symbol(s) across {timeframes.length} timeframe(s).
      </div>
    )
  }

  return (
    <div className="p-2.5 bg-amber-500/10 border border-amber-500/30 rounded text-sm space-y-1">
      {missing.length > 0 && (
        <div className="text-amber-400">
          Missing data: {missing.slice(0, 5).join(', ')}
          {missing.length > 5 && ` +${missing.length - 5} more`}
        </div>
      )}
      {partial.length > 0 && (
        <div className="text-amber-300">
          Partial coverage: {partial.slice(0, 3).map(p =>
            `${p.key} (${p.first} to ${p.last}, ${p.bars} bars)`
          ).join('; ')}
          {partial.length > 3 && ` +${partial.length - 3} more`}
        </div>
      )}
      <div className="text-xs text-terminal-500">
        Use the data ingest API to fill gaps before running the backtest.
      </div>
    </div>
  )
}

// Backtest form component
function BacktestForm({ onSubmit, loading, markets }) {
  const [name, setName] = useState('')
  const [symbols, setSymbols] = useState(['BTCUSDT', 'ETHUSDT'])
  const [timeframeList, setTimeframeList] = useState('1h,4h')
  const [startDate, setStartDate] = useState('2024-01-01')
  // P5-25: Default end date to yesterday
  const [endDate, setEndDate] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 1)
    return d.toISOString().slice(0, 10)
  })
  const [setupIds, setSetupIds] = useState(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'])
  const [slippageBps, setSlippageBps] = useState(5)
  const [feeBps, setFeeBps] = useState(10)

  const parsedTimeframes = timeframeList.split(',').map(t => t.trim()).filter(Boolean)

  const handleSubmit = (e) => {
    e.preventDefault()
    onSubmit({
      name: name || undefined,
      symbols: symbols,
      timeframes: parsedTimeframes,
      start_date: startDate,
      end_date: endDate,
      setup_ids: setupIds,
      slippage_bps: slippageBps,
      fee_bps: feeBps,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormField label="Run Name (optional)">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="backtest_btc_2024"
        />
      </FormField>

      <FormField label="Symbols" hint="Search and select from available markets">
        <SymbolMultiSelect
          selected={symbols}
          onChange={setSymbols}
          markets={markets}
        />
      </FormField>
      
      <FormField label="Timeframes" hint="Comma-separated list">
        <Input
          value={timeframeList}
          onChange={(e) => setTimeframeList(e.target.value)}
          placeholder="1h,4h"
        />
      </FormField>
      
      <div className="grid grid-cols-2 gap-4">
        <FormField label="Start Date">
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </FormField>
        
        <FormField label="End Date">
          <Input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </FormField>
      </div>
      
      {/* P5-27: Data availability indicator */}
      <DataAvailabilityInfo
        symbols={symbols}
        timeframes={parsedTimeframes}
        startDate={startDate}
        endDate={endDate}
      />

      <FormField label="Setups">
        <div className="flex flex-wrap gap-3">
          {['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'].map(s => (
            <label key={s} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={setupIds.includes(s)}
                onChange={(e) => {
                  if (e.target.checked) setSetupIds([...setupIds, s])
                  else setSetupIds(setupIds.filter(x => x !== s))
                }}
                className="rounded border-terminal-600 bg-terminal-800 text-amber-500 focus:ring-amber-500"
              />
              <span className="text-sm">{s}</span>
            </label>
          ))}
        </div>
      </FormField>

      <div className="grid grid-cols-2 gap-4">
        <FormField label="Slippage (bps)">
          <Input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={slippageBps}
            onChange={(e) => setSlippageBps(parseFloat(e.target.value) || 5)}
          />
        </FormField>
        
        <FormField label="Fees (bps)">
          <Input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={feeBps}
            onChange={(e) => setFeeBps(parseFloat(e.target.value) || 10)}
          />
        </FormField>
      </div>
      
      <button
        type="submit"
        disabled={loading || setupIds.length === 0 || symbols.length === 0}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-amber-500 hover:bg-amber-400 disabled:bg-terminal-700 text-terminal-900 font-medium rounded-lg transition-colors"
      >
        {loading ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            Creating...
          </>
        ) : (
          <>
            <Database className="w-4 h-4" />
            Run Backtest
          </>
        )}
      </button>
    </form>
  )
}

// Paper trading form component
function PaperForm({ onSubmit, onStop, loading, paperStatus }) {
  const [name, setName] = useState('')
  const [setupIds, setSetupIds] = useState(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'])
  const [initialCapital, setInitialCapital] = useState(10000)
  const [riskPerTrade, setRiskPerTrade] = useState(1.0)
  const [maxPositions, setMaxPositions] = useState(3)
  const [autoTrade, setAutoTrade] = useState(true)
  const [autoTradeTopN, setAutoTradeTopN] = useState(3)
  const [minScore, setMinScore] = useState(0)
  
  const isActive = paperStatus?.active
  
  const handleSubmit = (e) => {
    e.preventDefault()
    onSubmit({
      name: name || undefined,
      setup_ids: setupIds,
      initial_capital: initialCapital,
      risk_per_trade_pct: riskPerTrade,
      max_positions: maxPositions,
      auto_trade: autoTrade,
      auto_trade_top_n: autoTradeTopN,
      min_score: minScore,
    })
  }
  
  if (isActive) {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-green-500/10 border border-green-500/30 rounded-lg">
          <div className="flex items-center gap-2 text-green-400 mb-2">
            <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
            Paper Trading Active
          </div>
          <div className="text-sm text-terminal-300">
            <div>Run: <span className="font-mono text-terminal-100">{paperStatus.run_name}</span></div>
            <div>Started: {formatDateTime(paperStatus.started_at)}</div>
          </div>
        </div>
        
        <button
          onClick={() => onStop()}
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-red-500 hover:bg-red-400 disabled:bg-terminal-700 text-white font-medium rounded-lg transition-colors"
        >
          {loading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Stopping...
            </>
          ) : (
            <>
              <Square className="w-4 h-4" />
              Stop Paper Trading
            </>
          )}
        </button>
      </div>
    )
  }
  
  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormField label="Run Name (optional)">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="paper_session_001"
        />
      </FormField>
      
      <FormField label="Setups">
        <div className="flex flex-wrap gap-3">
          {['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'].map(s => (
            <label key={s} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={setupIds.includes(s)}
                onChange={(e) => {
                  if (e.target.checked) setSetupIds([...setupIds, s])
                  else setSetupIds(setupIds.filter(x => x !== s))
                }}
                className="rounded border-terminal-600 bg-terminal-800 text-amber-500 focus:ring-amber-500"
              />
              <span className="text-sm">{s}</span>
            </label>
          ))}
        </div>
      </FormField>

      <div className="grid grid-cols-3 gap-4">
        <FormField label="Initial Capital">
          <Input
            type="number"
            min={100}
            value={initialCapital}
            onChange={(e) => setInitialCapital(parseFloat(e.target.value) || 10000)}
          />
        </FormField>
        
        <FormField label="Risk % / Trade">
          <Input
            type="number"
            min={0.1}
            max={5}
            step={0.1}
            value={riskPerTrade}
            onChange={(e) => setRiskPerTrade(parseFloat(e.target.value) || 1.0)}
          />
        </FormField>
        
        <FormField label="Max Positions">
          <Input
            type="number"
            min={1}
            max={10}
            value={maxPositions}
            onChange={(e) => setMaxPositions(parseInt(e.target.value) || 3)}
          />
        </FormField>
      </div>
      
      <div className="p-3 bg-terminal-800/50 rounded-lg space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Auto-Trade from Signals</span>
          <Toggle enabled={autoTrade} onChange={setAutoTrade} />
        </div>
        
        {autoTrade && (
          <div className="grid grid-cols-2 gap-4 pt-2 border-t border-terminal-700">
            <FormField label="Take Top N Signals">
              <Input
                type="number"
                min={1}
                max={10}
                value={autoTradeTopN}
                onChange={(e) => setAutoTradeTopN(parseInt(e.target.value) || 3)}
              />
            </FormField>
            
            <FormField label="Minimum Score">
              <Input
                type="number"
                min={0}
                max={100}
                step={5}
                value={minScore}
                onChange={(e) => setMinScore(parseFloat(e.target.value) || 0)}
              />
            </FormField>
          </div>
        )}
      </div>
      
      <button
        type="submit"
        disabled={loading || setupIds.length === 0}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-green-500 hover:bg-green-400 disabled:bg-terminal-700 text-white font-medium rounded-lg transition-colors"
      >
        {loading ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            Starting...
          </>
        ) : (
          <>
            <Play className="w-4 h-4" />
            Start Paper Trading
          </>
        )}
      </button>
    </form>
  )
}

// Main page component
export default function ControlPlane() {
  const { post } = useApi()
  const { addToast } = useToast()
  const navigate = useNavigate()
  
  const [activeTab, setActiveTab] = useState('scan')
  const [loading, setLoading] = useState(false)
  
  const { data: queue, refetch: refetchQueue } = useQueueStatus()
  const { data: markets } = useMarkets()
  const { data: timeframes } = useTimeframes()
  const { data: paperStatus, refetch: refetchPaper } = usePaperStatus()
  
  const handleScan = async (data) => {
    setLoading(true)
    try {
      const result = await post('/v1/control/runs/scan', data)
      log.info('Scan queued', { event: 'scan_queued', run_id: result.run_id })
      addToast(`Scan queued: ${result.fingerprint?.slice(0, 8) || result.run_id?.slice(0, 8)}`, 'success')
      refetchQueue()
      navigate(`/runs/${result.run_id}`)
    } catch (err) {
      log.error('Scan queue failed', { event: 'scan_queue_error', error_message: err.message })
      addToast(`Failed to create scan: ${err.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }
  
  const handleBacktest = async (data) => {
    setLoading(true)
    try {
      const result = await post('/v1/control/runs/backtest', data)
      log.info('Backtest queued', { event: 'backtest_queued', run_id: result.run_id })
      addToast(`Backtest queued: ${result.fingerprint?.slice(0, 8) || result.run_id?.slice(0, 8)}`, 'success')
      refetchQueue()
      navigate(`/runs/${result.run_id}`)
    } catch (err) {
      log.error('Backtest queue failed', { event: 'backtest_queue_error', error_message: err.message })
      addToast(`Failed to create backtest: ${err.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }
  
  const handlePaperStart = async (data) => {
    setLoading(true)
    try {
      const result = await post('/v1/control/runs/paper/start', data)
      log.info('Paper started', { event: 'paper_start' })
      addToast('Paper trading started', 'success')
      refetchPaper()
      navigate('/paper')
    } catch (err) {
      log.error('Paper start failed', { event: 'paper_start_error', error_message: err.message })
      addToast(`Failed to start paper trading: ${err.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }
  
  const handlePaperStop = async () => {
    setLoading(true)
    try {
      await post('/v1/control/runs/paper/stop', {})
      log.info('Paper stopped', { event: 'paper_stop' })
      addToast('Paper trading stopped', 'success')
      refetchPaper()
    } catch (err) {
      log.error('Paper stop failed', { event: 'paper_stop_error', error_message: err.message })
      addToast(`Failed to stop paper trading: ${err.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }
  
  const handleCancelRun = async (runId) => {
    try {
      await post(`/v1/control/runs/${runId}/cancel`, {})
      log.info('Run canceled', { event: 'run_canceled', run_id: runId })
      addToast('Run canceled', 'success')
      refetchQueue()
    } catch (err) {
      log.error('Run cancel failed', { event: 'run_cancel_error', run_id: runId, error_message: err.message })
      addToast(`Failed to cancel: ${err.message}`, 'error')
    }
  }
  
  const tabs = [
    { value: 'scan', label: 'Scan', icon: Zap },
    { value: 'backtest', label: 'Backtest', icon: Database },
    { value: 'paper', label: 'Paper Trade', icon: TrendingUp },
  ]
  
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Control Plane</h1>
          <p className="text-terminal-400">Create and manage trading runs</p>
        </div>
      </div>
      
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Run creation forms */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <Tabs
                tabs={tabs}
                activeTab={activeTab}
                onChange={setActiveTab}
              />
            </CardHeader>
            <CardContent>
              {activeTab === 'scan' && (
                <ScanForm
                  onSubmit={handleScan}
                  loading={loading}
                  markets={markets}
                  timeframes={timeframes}
                />
              )}
              {activeTab === 'backtest' && (
                <BacktestForm
                  onSubmit={handleBacktest}
                  loading={loading}
                  markets={markets}
                />
              )}
              {activeTab === 'paper' && (
                <PaperForm
                  onSubmit={handlePaperStart}
                  onStop={handlePaperStop}
                  loading={loading}
                  paperStatus={paperStatus}
                />
              )}
            </CardContent>
          </Card>
        </div>
        
        {/* Queue status */}
        <div>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Clock className="w-4 h-4 text-amber-500" />
                Run Queue
              </CardTitle>
              <RefreshControl
                lastUpdated={null}
                onRefresh={refetchQueue}
              />
            </CardHeader>
            <CardContent>
              {!queue?.queue || queue.queue.length === 0 ? (
                <EmptyState
                  icon={Clock}
                  title="Queue empty"
                  description="No pending or running jobs"
                />
              ) : (
                <div className="space-y-2">
                  {queue.queue.map((item) => (
                    <QueueItem
                      key={item.run_id}
                      item={item}
                      onCancel={handleCancelRun}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
