import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, RefreshCw, Clock, Activity, Target, TrendingUp, TrendingDown } from 'lucide-react'
import { createChart, CrosshairMode } from 'lightweight-charts'
import { useTimeframes, useOHLCV, useSnapshot, useSqueezeState, useBtcSqueezeStatus } from '../hooks/useData'
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
  formatDateTime,
  formatRelativeTime
} from '../utils/formatters'

export default function MarketDetail() {
  const { symbol } = useParams()
  const { data: tfData } = useTimeframes()
  
  const [selectedTf, setSelectedTf] = useState('1h')
  const [candleLimit, setCandleLimit] = useState(100)

  const timeframes = tfData?.timeframes || ['1m', '5m', '15m', '1h', '4h', '1d']

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/markets" className="btn btn-ghost btn-sm">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-terminal-100 font-mono">{symbol}</h1>
            <p className="text-sm text-terminal-400 mt-1">Market Analysis</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Select
            value={selectedTf}
            onChange={(e) => setSelectedTf(e.target.value)}
            options={timeframes.map(tf => ({ value: tf, label: tf.toUpperCase() }))}
            className="w-24"
          />
          <Select
            value={candleLimit}
            onChange={(e) => setCandleLimit(parseInt(e.target.value))}
            options={[
              { value: 100, label: '100' },
              { value: 200, label: '200' },
              { value: 500, label: '500' },
            ]}
            className="w-24"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Chart - 2 columns */}
        <div className="lg:col-span-2 space-y-6">
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
    </div>
  )
}

function CandlestickChart({ symbol, timeframe, limit }) {
  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  
  const { data, loading, error, refetch } = useOHLCV(symbol, timeframe, limit)

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: '#111115' },
        textColor: '#c5c5d3',
      },
      grid: {
        vertLines: { color: '#1f1f26' },
        horzLines: { color: '#1f1f26' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#f59e0b', style: 2, width: 1 },
        horzLine: { color: '#f59e0b', style: 2, width: 1 },
      },
      timeScale: {
        borderColor: '#27272f',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#27272f',
      },
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })

    chartRef.current = chart
    seriesRef.current = candleSeries

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [])

  // Update data
  useEffect(() => {
    if (!seriesRef.current || !data?.candles) return

    const chartData = data.candles.map(c => ({
      time: Math.floor(c.timestamp / 1000),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))

    seriesRef.current.setData(chartData)
    chartRef.current?.timeScale().fitContent()
  }, [data])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-amber-400" />
          OHLCV Chart
        </CardTitle>
        <RefreshControl onRefresh={refetch} loading={loading} />
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <Skeleton className="h-[400px]" />
        ) : error ? (
          <div className="h-[400px] flex items-center justify-center text-terminal-400">
            Failed to load chart data
          </div>
        ) : (
          <div 
            ref={chartContainerRef} 
            className="h-[400px] w-full"
          />
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
          <Clock className="w-4 h-4 text-amber-400" />
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
            <div className="text-center p-4 bg-terminal-900 rounded-lg">
              <p className="text-3xl font-bold font-mono text-terminal-100">
                {formatPrice(snapshot.close)}
              </p>
              <p className="text-xs text-terminal-500 mt-1">
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
              <p className="text-xs text-terminal-500 pt-2 border-t border-terminal-600">
                Updated {formatRelativeTime(lastUpdated)}
              </p>
            )}
          </div>
        ) : (
          <p className="text-terminal-400 text-center py-4">No data</p>
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
          <Target className="w-4 h-4 text-amber-400" />
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
          <p className="text-terminal-400 text-sm">Not computed yet</p>
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
          <TrendingUp className="w-4 h-4 text-amber-400" />
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
          <p className="text-terminal-400 text-sm">Not computed yet</p>
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
