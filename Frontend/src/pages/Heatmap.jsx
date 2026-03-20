import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { LayoutGrid, TrendingUp, TrendingDown, BarChart3, AlertCircle } from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  Skeleton,
  EmptyState,
  Select
} from '../components/ui'

const POLL_INTERVAL_MS = 60_000

const PERIOD_OPTIONS = [
  { value: '1h', label: '1 Hour' },
  { value: '4h', label: '4 Hours' },
  { value: '24h', label: '24 Hours' },
]

function getHeatColor(pct) {
  if (pct > 5) return 'bg-green-500 text-green-950'
  if (pct > 2) return 'bg-green-600/80 text-green-100'
  if (pct > 0) return 'bg-green-800/60 text-green-200'
  if (pct < -5) return 'bg-red-500 text-red-950'
  if (pct < -2) return 'bg-red-600/80 text-red-100'
  if (pct < 0) return 'bg-red-800/60 text-red-200'
  return 'bg-terminal-700 text-terminal-300'
}

function formatPct(pct) {
  if (pct == null) return '--'
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

function formatPrice(price) {
  if (price == null) return '--'
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (price >= 1) return price.toFixed(4)
  return price.toPrecision(4)
}

export default function Heatmap() {
  const { get, isConnected } = useApi()
  const { addToast } = useToast()
  const navigate = useNavigate()

  const [tiles, setTiles] = useState([])
  const [tileOrder, setTileOrder] = useState(null) // locked sort order from initial load
  const [meta, setMeta] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [period, setPeriod] = useState('24h')
  const [hoveredTile, setHoveredTile] = useState(null)
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 })
  const tooltipRef = useRef(null)

  const fetchHeatmap = useCallback(async (showLoading = false) => {
    if (!isConnected) return

    if (showLoading) setLoading(true)
    setError(null)

    try {
      const result = await get(`/v1/universe/heatmap?period=${period}&tf=1h`)
      const data = result?.data || result

      if (data?.tiles) {
        setTiles(data.tiles)
        // Lock tile positions on initial load or period change (showLoading=true).
        // Poll updates (showLoading=false) only update values/colours, not positions.
        if (showLoading) {
          const order = [...data.tiles]
            .sort((a, b) => Math.abs(b.pct_change) - Math.abs(a.pct_change))
            .map(t => t.symbol)
          setTileOrder(order)
        }
        setMeta({
          symbol_count: data.symbol_count,
          period: data.period,
          tf: data.tf,
        })
      }
    } catch (err) {
      const msg = err?.message || 'Failed to load heatmap'
      setError(msg)
      if (showLoading) addToast(msg, 'error')
    } finally {
      setLoading(false)
    }
  }, [get, isConnected, period, addToast])

  // Initial fetch and refetch on period change
  useEffect(() => {
    fetchHeatmap(true)
  }, [fetchHeatmap])

  // Poll every 60s
  useEffect(() => {
    if (!isConnected) return undefined

    const interval = setInterval(() => {
      fetchHeatmap(false)
    }, POLL_INTERVAL_MS)

    return () => clearInterval(interval)
  }, [fetchHeatmap, isConnected])

  // Stable tile order: positions locked at initial load, only values/colours update on poll
  const sortedTiles = useMemo(() => {
    if (!tileOrder) {
      return [...tiles].sort((a, b) => Math.abs(b.pct_change) - Math.abs(a.pct_change))
    }
    const tileMap = Object.fromEntries(tiles.map(t => [t.symbol, t]))
    const ordered = tileOrder.map(sym => tileMap[sym]).filter(Boolean)
    // Append any new symbols not in the locked order
    const orderSet = new Set(tileOrder)
    const newTiles = tiles.filter(t => !orderSet.has(t.symbol))
    return [...ordered, ...newTiles]
  }, [tiles, tileOrder])

  // Summary stats
  const stats = useMemo(() => {
    if (tiles.length === 0) return null

    const gainers = tiles.filter(t => t.pct_change > 0).length
    const losers = tiles.filter(t => t.pct_change < 0).length
    const flat = tiles.filter(t => t.pct_change === 0).length
    const avgChange = tiles.reduce((sum, t) => sum + t.pct_change, 0) / tiles.length

    return { gainers, losers, flat, avgChange, total: tiles.length }
  }, [tiles])

  const handleTileClick = useCallback((symbol) => {
    navigate(`/app/markets/${symbol}`)
  }, [navigate])

  const handleMouseMove = useCallback((e) => {
    setTooltipPos({ x: e.clientX, y: e.clientY })
  }, [])

  return (
    <div className="space-y-6" data-operator-surface>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Crypto Heatmap</h1>
          <p className="mt-1 text-sm text-terminal-400">
            Visual overview of {meta?.symbol_count || '~300'} monitored symbols by % change
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-terminal-400">Period:</span>
          <Select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            options={PERIOD_OPTIONS}
            className="w-32"
          />
        </div>
      </div>

      {/* Summary stats bar */}
      {stats && !loading && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-6 py-3">
            <div className="flex items-center gap-2">
              <LayoutGrid className="size-4 text-terminal-400" />
              <span className="text-sm text-terminal-400">Symbols:</span>
              <span className="font-mono text-sm font-medium text-terminal-100">{stats.total}</span>
            </div>
            <div className="flex items-center gap-2">
              <TrendingUp className="size-4 text-green-400" />
              <span className="text-sm text-terminal-400">Gainers:</span>
              <span className="font-mono text-sm font-medium text-green-400">{stats.gainers}</span>
            </div>
            <div className="flex items-center gap-2">
              <TrendingDown className="size-4 text-red-400" />
              <span className="text-sm text-terminal-400">Losers:</span>
              <span className="font-mono text-sm font-medium text-red-400">{stats.losers}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-terminal-400">Avg Change:</span>
              <span className={`font-mono text-sm font-medium ${
                stats.avgChange > 0 ? 'text-green-400' : stats.avgChange < 0 ? 'text-red-400' : 'text-terminal-300'
              }`}>
                {formatPct(stats.avgChange)}
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Heatmap grid */}
      {loading ? (
        <Card>
          <CardContent>
            <div className="grid grid-cols-4 gap-2 md:grid-cols-8 lg:grid-cols-12">
              {Array(48).fill(0).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded" />
              ))}
            </div>
          </CardContent>
        </Card>
      ) : error ? (
        <Card>
          <CardContent>
            <EmptyState
              icon={AlertCircle}
              title="Failed to load heatmap"
              description={error}
            />
          </CardContent>
        </Card>
      ) : sortedTiles.length === 0 ? (
        <Card>
          <CardContent>
            <EmptyState
              icon={BarChart3}
              title="No heatmap data"
              description="No monitored symbols returned for this period"
            />
          </CardContent>
        </Card>
      ) : (
        <div className="relative">
          <div
            className="grid grid-cols-4 gap-1 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 xl:grid-cols-12"
            style={{ gridAutoRows: 'minmax(64px, auto)' }}
          >
            {sortedTiles.map((tile) => (
              <HeatmapTile
                key={tile.symbol}
                tile={tile}
                onClick={handleTileClick}
                onMouseEnter={setHoveredTile}
                onMouseLeave={() => setHoveredTile(null)}
                onMouseMove={handleMouseMove}
              />
            ))}
          </div>

          {/* Floating tooltip */}
          {hoveredTile && (
            <div
              ref={tooltipRef}
              className="pointer-events-none fixed z-50 rounded border border-terminal-600 bg-terminal-800 px-3 py-2 shadow-lg"
              style={{
                left: tooltipPos.x + 12,
                top: tooltipPos.y - 60,
              }}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm font-bold text-terminal-100">
                  {hoveredTile.symbol}
                </span>
                {hoveredTile.stale && (
                  <Badge variant="warning" className="text-[10px]">Stale</Badge>
                )}
              </div>
              <div className="mt-1 space-y-0.5 text-xs">
                <div className="flex justify-between gap-4">
                  <span className="text-terminal-400">Price:</span>
                  <span className="font-mono text-terminal-100">{formatPrice(hoveredTile.close)}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-terminal-400">Change:</span>
                  <span className={`font-mono ${
                    hoveredTile.pct_change > 0 ? 'text-green-400' : hoveredTile.pct_change < 0 ? 'text-red-400' : 'text-terminal-300'
                  }`}>
                    {formatPct(hoveredTile.pct_change)}
                  </span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-terminal-400">Vol Z:</span>
                  <span className="font-mono text-terminal-100">{hoveredTile.volume_z?.toFixed(2) ?? '--'}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function HeatmapTile({ tile, onClick, onMouseEnter, onMouseLeave, onMouseMove }) {
  const colorClass = getHeatColor(tile.pct_change)
  const isLarge = tile.volume_z >= 2.0
  const isMedium = tile.volume_z >= 1.0 && tile.volume_z < 2.0

  // Strip "USDT" suffix for display
  const displaySymbol = tile.symbol.replace(/USDT$/, '')

  return (
    <button
      className={`
        relative flex flex-col items-center justify-center overflow-hidden rounded
        transition-colors duration-500 ease-in-out
        ${colorClass}
        ${tile.stale ? 'opacity-50' : ''}
        ${isLarge ? 'col-span-2 row-span-2' : isMedium ? 'col-span-1 row-span-1' : 'col-span-1 row-span-1'}
        cursor-pointer border border-transparent hover:border-terminal-100/30 hover:brightness-110
      `}
      onClick={() => onClick(tile.symbol)}
      onMouseEnter={() => onMouseEnter(tile)}
      onMouseLeave={onMouseLeave}
      onMouseMove={onMouseMove}
      title={`${tile.symbol} ${formatPct(tile.pct_change)}`}
    >
      <span className={`font-mono text-xs font-bold leading-tight ${isLarge ? 'text-sm' : 'text-[10px]'}`}>
        {displaySymbol}
      </span>
      <span className={`font-mono leading-tight ${isLarge ? 'text-xs' : 'text-[9px]'}`}>
        {formatPct(tile.pct_change)}
      </span>
    </button>
  )
}
