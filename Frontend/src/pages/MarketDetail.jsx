import { startTransition, useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Clock, Activity, Target, TrendingUp, Calendar } from 'lucide-react'
import { useTimeframes, useLiveOHLCV, useSnapshot, useSqueezeState, useBtcSqueezeStatus } from '../hooks/useData'
import { useApi } from '../context/ApiContext'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  Skeleton,
  Select,
  DataRow,
  RefreshControl
} from '../components/ui'
import {
  formatPrice,
  formatNumber,
  formatRelativeTime
} from '../utils/formatters'
import OpportunityCurveChart from '../components/opportunities/OpportunityCurveChart'
import { MARKET_LIVE_COLLECTOR_CHART_PROPS } from '../components/opportunities/chartPresets'
import { getTfSeconds } from '../config/governed'

const EMPTY_MARKERS = Object.freeze({})

function timeframeToMs(tf) {
  const key = String(tf || '').toLowerCase()
  const tfSeconds = getTfSeconds()
  if (tfSeconds[key]) return tfSeconds[key] * 1000

  const parsed = key.match(/^(\d+)([mhdw])$/)
  if (!parsed) return 300_000
  const qty = Number(parsed[1])
  const unit = parsed[2]
  if (!Number.isFinite(qty) || qty <= 0) return 300_000
  if (unit === 'm') return qty * 60_000
  if (unit === 'h') return qty * 3_600_000
  if (unit === 'd') return qty * 86_400_000
  if (unit === 'w') return qty * 604_800_000
  return 300_000
}

function toCurveCandle(candle) {
  if (!candle) return null
  const t = Number(candle.timestamp ?? Date.parse(candle.candle_ts))
  if (!Number.isFinite(t)) return null
  return {
    t,
    o: Number(candle.open),
    h: Number(candle.high),
    l: Number(candle.low),
    c: Number(candle.close),
    v: Number(candle.volume),
  }
}

export default function MarketDetail() {
  const { symbol } = useParams()
  const { data: tfData } = useTimeframes()
  
  const [selectedTf, setSelectedTf] = useState('1m')
  const [candleLimit, setCandleLimit] = useState(100)

  const timeframes = tfData?.timeframes || ['1m', '5m', '15m', '1h', '4h', '1d']
  const handleTimeframeChange = (event) => {
    const nextTimeframe = event.target.value
    startTransition(() => {
      setSelectedTf(nextTimeframe)
    })
  }
  const handleLimitChange = (event) => {
    const nextLimit = Number.parseInt(event.target.value, 10)
    startTransition(() => {
      setCandleLimit(nextLimit)
    })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/app/markets" className="btn btn-ghost btn-sm">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-mono text-2xl font-bold text-terminal-100">{symbol}</h1>
            <p className="mt-1 text-sm text-terminal-400">Market Analysis</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Select
            value={selectedTf}
            onChange={handleTimeframeChange}
            options={timeframes.map(tf => ({ value: tf, label: tf.toUpperCase() }))}
            className="w-24"
          />
          <Select
            value={candleLimit}
            onChange={handleLimitChange}
            options={[
              { value: 100, label: '100' },
              { value: 200, label: '200' },
              { value: 500, label: '500' },
            ]}
            className="w-24"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Chart - 2 columns */}
        <div className="space-y-6 lg:col-span-2">
          <CandlestickChart symbol={symbol} timeframe={selectedTf} limit={candleLimit} />
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Live snapshot */}
          <LiveSnapshot symbol={symbol} />
          
          {/* Squeeze state */}
          <SqueezeStatePanel symbol={symbol} timeframe={selectedTf} />
          
          {/* BTC correlation */}
          <BtcCorrelationPanel timeframe={selectedTf} />
        </div>
      </div>

      {/* Seasonality */}
      <SeasonalityPanel symbol={symbol} />
    </div>
  )
}

function CandlestickChart({ symbol, timeframe, limit }) {
  const pollMs = timeframe === '1m' ? 2000 : 5000
  const { data, loading, error, refreshing, lastUpdated, refetch } = useLiveOHLCV(
    symbol,
    timeframe,
    limit,
    pollMs,
    true
  )

  const candles = useMemo(
    () => (data?.candles || []).map(toCurveCandle).filter(Boolean),
    [data?.candles]
  )
  const deferredCandles = useDeferredValue(candles)
  const gapMs = useMemo(() => timeframeToMs(timeframe), [timeframe])
  const chartRenderingLag = deferredCandles !== candles

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4 text-amber-400" />
          Collector Chart
        </CardTitle>
        <RefreshControl onRefresh={refetch} loading={loading || refreshing} />
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <Skeleton className="h-[400px]" />
        ) : error ? (
          <div className="flex h-[400px] items-center justify-center text-terminal-400">
            Failed to load chart data
          </div>
        ) : (
          <div className="space-y-3">
            <OpportunityCurveChart
              candles={deferredCandles}
              markers={EMPTY_MARKERS}
              {...MARKET_LIVE_COLLECTOR_CHART_PROPS}
              expectedGapMs={gapMs}
              gapLabel={`${timeframe} collector data`}
            />
            <div className="flex items-center justify-between text-xs text-terminal-500">
              <span>
                Source: {data?.source || 'db_ohlcv'} • Collector lag: {data?.collector_lag_s ?? 'n/a'}s
              </span>
              <span>
                {chartRenderingLag
                  ? 'Rendering latest bars...'
                  : (lastUpdated ? `Polled ${formatRelativeTime(lastUpdated)}` : 'Waiting for first poll')}
              </span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function LiveSnapshot({ symbol }) {
  const { data, loading, lastUpdated } = useSnapshot(symbol, true)
  const [stale, setStale] = useState(false)

  // Check for staleness
  useEffect(() => {
    if (!lastUpdated) return
    
    const checkStale = () => {
      const diff = Date.now() - new Date(lastUpdated).getTime()
      setStale(diff > 20000) // >20s = stale
    }
    
    checkStale()
    const interval = setInterval(checkStale, 5000)
    return () => clearInterval(interval)
  }, [lastUpdated])

  const snapshot = data?.snapshot

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Clock className="size-4 text-amber-400" />
          Live Snapshot
        </CardTitle>
        {stale && (
          <Badge variant="warning" className="text-xs">Stale</Badge>
        )}
      </CardHeader>
      <CardContent>
        {loading && !snapshot ? (
          <div className="space-y-3">
            <Skeleton className="h-8" />
            <Skeleton />
            <Skeleton />
          </div>
        ) : snapshot ? (
          <div className="space-y-4">
            <div className="rounded-lg bg-terminal-900 p-4 text-center">
              <p className="font-mono text-3xl font-bold text-terminal-100">
                {formatPrice(snapshot.close)}
              </p>
              <p className="mt-1 text-xs text-terminal-500">
                Last price
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <DataRow label="Open" value={formatPrice(snapshot.open)} />
              <DataRow label="High" value={<span className="text-signal-long">{formatPrice(snapshot.high)}</span>} />
              <DataRow label="Low" value={<span className="text-signal-short">{formatPrice(snapshot.low)}</span>} />
              <DataRow label="Volume" value={formatNumber(snapshot.volume, 0)} />
            </div>

            {lastUpdated && (
              <p className="border-t border-terminal-600 pt-2 text-xs text-terminal-500">
                Updated {formatRelativeTime(lastUpdated)}
              </p>
            )}
          </div>
        ) : (
          <p className="py-4 text-center text-terminal-400">No data</p>
        )}
      </CardContent>
    </Card>
  )
}

function SqueezeStatePanel({ symbol, timeframe }) {
  const { data, loading, error } = useSqueezeState(symbol, timeframe)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Target className="size-4 text-amber-400" />
          Squeeze State (S1)
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-3">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </div>
        ) : error || !data ? (
          <p className="text-sm text-terminal-400">Not computed yet</p>
        ) : (
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-terminal-400">Squeeze Active</span>
              <Badge variant={data.squeeze_on ? 'warning' : 'default'}>
                {data.squeeze_on ? 'YES' : 'NO'}
              </Badge>
            </div>
            
            {data.squeeze_age !== undefined && (
              <DataRow label="Squeeze Age" value={`${data.squeeze_age} bars`} />
            )}
            
            {data.box_high && (
              <>
                <DataRow label="Box High" value={formatPrice(data.box_high)} />
                <DataRow label="Box Low" value={formatPrice(data.box_low)} />
              </>
            )}
            
            {data.direction && (
              <div className="flex items-center justify-between">
                <span className="text-terminal-400">Direction</span>
                <Badge variant={data.direction === 'long' ? 'long' : data.direction === 'short' ? 'short' : 'default'}>
                  {data.direction.toUpperCase()}
                </Badge>
              </div>
            )}

            {data.ripe !== undefined && (
              <div className="flex items-center justify-between">
                <span className="text-terminal-400">Ripe</span>
                <Badge variant={data.ripe ? 'success' : 'default'}>
                  {data.ripe ? 'YES' : 'NO'}
                </Badge>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function BtcCorrelationPanel({ timeframe }) {
  const { data, loading, error } = useBtcSqueezeStatus(timeframe)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingUp className="size-4 text-amber-400" />
          BTC Correlation
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-3">
            <Skeleton />
            <Skeleton />
          </div>
        ) : error || !data ? (
          <p className="text-sm text-terminal-400">Not computed yet</p>
        ) : (
          <div className="space-y-3 text-sm">
            {data.status && (
              <div className="flex items-center justify-between">
                <span className="text-terminal-400">Status</span>
                <Badge>{data.status}</Badge>
              </div>
            )}
            
            {data.breakout_direction && (
              <div className="flex items-center justify-between">
                <span className="text-terminal-400">Breakout</span>
                <Badge variant={data.breakout_direction === 'up' ? 'long' : 'short'}>
                  {data.breakout_direction.toUpperCase()}
                </Badge>
              </div>
            )}

            {data.box_high && (
              <>
                <DataRow label="Box High" value={formatPrice(data.box_high)} />
                <DataRow label="Box Low" value={formatPrice(data.box_low)} />
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function SeasonalityPanel({ symbol }) {
  const { get, isConnected } = useApi()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    if (!isConnected || !symbol) return
    try {
      setLoading(true)
      const result = await get(`/v1/symbols/${encodeURIComponent(symbol)}/seasonality`, { years: 3 })
      setData(result?.data ?? result)
      setError(null)
    } catch (err) {
      setError(err.message || 'Failed to fetch')
    } finally {
      setLoading(false)
    }
  }, [get, isConnected, symbol])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const months = data?.monthly_returns || []
  const maxAbs = useMemo(
    () => months.reduce((m, row) => Math.max(m, Math.abs(row.avg_return_pct || 0)), 1),
    [months]
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Calendar className="size-4 text-amber-400" />
          Seasonal Patterns
        </CardTitle>
        {data?.data_months != null && (
          <p className="mt-1 text-xs text-terminal-500">
            Based on {data.data_months} months of data ({data.years_requested}y requested)
          </p>
        )}
      </CardHeader>
      <CardContent>
        {data?.warning && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-700/50 bg-amber-900/20 px-3 py-2 text-xs text-amber-300">
            {data.warning}
          </div>
        )}

        {loading && !data ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-5" />
            ))}
          </div>
        ) : error ? (
          <p className="py-4 text-center text-sm text-terminal-400">Unable to load seasonality data</p>
        ) : months.length > 0 ? (
          <div className="space-y-1.5">
            {months.map((row) => {
              const avg = row.avg_return_pct ?? 0
              const barWidth = Math.min(Math.abs(avg) / maxAbs * 100, 100)
              const isPositive = avg >= 0
              return (
                <div key={row.month} className="flex items-center gap-2">
                  <span className="w-8 shrink-0 text-xs text-terminal-400">{row.month_name}</span>
                  <div className="relative h-5 flex-1 rounded bg-terminal-800">
                    <div
                      className={`h-full rounded ${isPositive ? 'bg-green-600' : 'bg-red-600'}`}
                      style={{ width: `${barWidth}%` }}
                    />
                  </div>
                  <span className={`w-24 shrink-0 text-right font-mono text-xs ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
                    {avg >= 0 ? '+' : ''}{avg.toFixed(1)}% (n={row.sample_count})
                  </span>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-terminal-400">No seasonality data</p>
        )}
      </CardContent>
    </Card>
  )
}
