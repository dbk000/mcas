import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts'

const FIVE_MINUTES_MS = 5 * 60 * 1000

function toChartPoint(candle) {
  return {
    time: Math.floor(candle.t / 1000),
    open: candle.o,
    high: candle.h,
    low: candle.l,
    close: candle.c,
  }
}

function calcGapCount(candles, expectedGapMs) {
  let gaps = 0
  for (let i = 1; i < candles.length; i += 1) {
    if ((candles[i].t - candles[i - 1].t) > expectedGapMs) {
      gaps += 1
    }
  }
  return gaps
}

function formatLevelPrice(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  let decimals = 4
  if (Math.abs(n) >= 1000) decimals = 2
  else if (Math.abs(n) >= 1) decimals = 3
  else if (Math.abs(n) >= 0.01) decimals = 5
  else decimals = 7
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export default function OpportunityCurveChart({
  candles = [],
  markers = {},
  revealTs = null,
  hideExitUntilReveal = false,
  hideLevels = false,
  height = 320,
  compact = false,
  expectedGapMs = FIVE_MINUTES_MS,
  gapLabel = '5m data',
  showGapBadge = true,
  className = '',
}) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const priceLinesRef = useRef([])
  const [markerCoords, setMarkerCoords] = useState({ entry: null, exit: null })

  const clippedCandles = useMemo(() => {
    if (!Array.isArray(candles) || candles.length === 0) return []
    if (revealTs == null) return candles
    return candles.filter((c) => c.t <= revealTs)
  }, [candles, revealTs])

  const gapCount = useMemo(
    () => calcGapCount(clippedCandles, expectedGapMs),
    [clippedCandles, expectedGapMs]
  )
  const hideExit = hideExitUntilReveal && revealTs != null && markers?.exit_ts != null && revealTs < markers.exit_ts
  const levelLegend = useMemo(() => {
    if (hideLevels) return []

    const items = []
    if (markers?.entry_px != null) {
      items.push({ key: 'entry', label: 'ENTRY', price: markers.entry_px, className: 'text-amber-300 border-amber-500/60 bg-amber-500/15' })
    }
    if (markers?.stop_px != null) {
      items.push({ key: 'stop', label: 'STOP', price: markers.stop_px, className: 'text-red-300 border-red-500/60 bg-red-500/15' })
    }
    if (Array.isArray(markers?.tp_px)) {
      markers.tp_px.forEach((price, idx) => {
        if (price == null || Number.isNaN(Number(price))) return
        items.push({ key: `tp-${idx}`, label: `TP${idx + 1}`, price, className: 'text-emerald-300 border-emerald-500/60 bg-emerald-500/15' })
      })
    }
    if (!hideExit && markers?.exit_px != null) {
      items.push({ key: 'exit', label: 'EXIT', price: markers.exit_px, className: 'text-sky-300 border-sky-500/60 bg-sky-500/15' })
    }
    return items
  }, [markers, hideLevels, hideExit])

  const syncMarkerCoords = useCallback(() => {
    const chart = chartRef.current
    if (!chart || !markers) return

    const timeScale = chart.timeScale()
    const entryX = markers.entry_ts ? timeScale.timeToCoordinate(Math.floor(markers.entry_ts / 1000)) : null
    const exitX = (!hideExit && markers.exit_ts)
      ? timeScale.timeToCoordinate(Math.floor(markers.exit_ts / 1000))
      : null

    setMarkerCoords({
      entry: Number.isFinite(entryX) ? entryX : null,
      exit: Number.isFinite(exitX) ? exitX : null,
    })
  }, [markers, hideExit])

  useEffect(() => {
    if (!containerRef.current) return undefined

    const chart = createChart(containerRef.current, {
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
        vertLine: { color: '#f59e0b', style: LineStyle.Dashed, width: 1 },
        horzLine: { color: '#f59e0b', style: LineStyle.Dashed, width: 1 },
      },
      timeScale: {
        borderColor: '#27272f',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 0,
      },
      rightPriceScale: {
        borderColor: '#27272f',
      },
      width: containerRef.current.clientWidth,
      height,
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
      priceLineVisible: false,
      lastValueVisible: !compact,
    })

    chartRef.current = chart
    seriesRef.current = candleSeries

    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current || !chartRef.current) return
      chartRef.current.applyOptions({
        width: containerRef.current.clientWidth,
        height,
      })
      syncMarkerCoords()
    })
    resizeObserver.observe(containerRef.current)

    const handleVisibleRangeChange = () => syncMarkerCoords()
    chart.timeScale().subscribeVisibleTimeRangeChange(handleVisibleRangeChange)

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(handleVisibleRangeChange)
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height, compact, syncMarkerCoords])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const series = seriesRef.current
    const chart = chartRef.current

    const chartData = clippedCandles.map(toChartPoint)
    series.setData(chartData)
    chart.timeScale().fitContent()
    requestAnimationFrame(syncMarkerCoords)
  }, [clippedCandles, syncMarkerCoords])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return

    for (const line of priceLinesRef.current) {
      try {
        series.removePriceLine(line)
      } catch {
        // Ignore stale handles after rapid rerenders.
      }
    }
    priceLinesRef.current = []

    if (hideLevels || clippedCandles.length === 0 || !markers) return

    const addLine = (price, color, title, lineStyle = LineStyle.Solid) => {
      if (price == null || Number.isNaN(Number(price))) return
      const line = series.createPriceLine({
        price: Number(price),
        color,
        lineWidth: 2,
        lineStyle,
        axisLabelVisible: true,
        title,
      })
      priceLinesRef.current.push(line)
    }

    addLine(markers.entry_px, '#f59e0b', 'ENTRY')
    addLine(markers.stop_px, '#ef4444', 'STOP', LineStyle.Dashed)
    if (Array.isArray(markers.tp_px)) {
      markers.tp_px.forEach((px, idx) => addLine(px, '#22c55e', `TP${idx + 1}`, LineStyle.Dashed))
    }
    if (!hideExit) {
      addLine(markers.exit_px, '#38bdf8', 'EXIT')
    }
  }, [markers, hideLevels, hideExit, clippedCandles.length, compact])

  if (clippedCandles.length === 0) {
    return (
      <div
        className={`w-full rounded border border-terminal-600 bg-terminal-900/50 flex items-center justify-center text-terminal-400 text-sm ${className}`}
        style={{ height }}
      >
        No candles in current reveal window.
      </div>
    )
  }

  return (
    <div className={`relative w-full rounded border border-terminal-600 overflow-hidden ${className}`} style={{ height }}>
      <div ref={containerRef} className="h-full w-full" />

      <div className="pointer-events-none absolute inset-0">
        {markerCoords.entry !== null && (
          <div className="absolute top-0 bottom-0" style={{ left: `${markerCoords.entry}px` }}>
            <div className="absolute top-0 bottom-0 w-0.5 bg-amber-300 shadow-[0_0_10px_rgba(251,191,36,0.9)]" />
            <span className="absolute top-1 left-1 px-1 py-0.5 text-[9px] font-bold tracking-wide rounded border border-amber-500/70 bg-amber-500/25 text-amber-200">
              ENTRY
            </span>
          </div>
        )}
        {markerCoords.exit !== null && (
          <div className="absolute top-0 bottom-0" style={{ left: `${markerCoords.exit}px` }}>
            <div className="absolute top-0 bottom-0 w-0.5 bg-sky-300 shadow-[0_0_10px_rgba(125,211,252,0.9)]" />
            <span className="absolute top-1 left-1 px-1 py-0.5 text-[9px] font-bold tracking-wide rounded border border-sky-500/70 bg-sky-500/25 text-sky-200">
              EXIT
            </span>
          </div>
        )}
      </div>

      {!hideLevels && levelLegend.length > 0 && (
        <div className="pointer-events-none absolute top-2 left-2 right-2 flex flex-wrap gap-1">
          {levelLegend.map((item) => (
            <span
              key={item.key}
              className={`px-1.5 py-0.5 rounded border text-[10px] font-mono ${item.className}`}
            >
              {item.label} {formatLevelPrice(item.price)}
            </span>
          ))}
        </div>
      )}

      {showGapBadge && gapCount > 0 && (
        <div className="pointer-events-none absolute bottom-2 left-2 px-2 py-1 rounded bg-terminal-900/90 text-[11px] text-amber-300 border border-amber-700/60">
          Gappy {gapLabel} ({gapCount} gap{gapCount > 1 ? 's' : ''})
        </div>
      )}
    </div>
  )
}
