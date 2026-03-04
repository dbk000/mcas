import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts'

const FIVE_MINUTES_MS = 5 * 60 * 1000
const MA_COLORS = ['#7dd3fc', '#a3e635', '#ef4444']

const CHART_VARIANTS = {
  terminal: {
    background: '#111115',
    text: '#c5c5d3',
    grid: '#1f1f26',
    border: '#27272f',
    up: '#10b981',
    down: '#ef4444',
    crosshair: '#f59e0b',
  },
  classic: {
    background: '#170c1d',
    text: '#cfc6de',
    grid: '#2a2130',
    border: '#352c3c',
    up: '#7bd88f',
    down: '#f97066',
    crosshair: '#f5d76e',
  },
}

function toChartPoint(candle) {
  return {
    time: Math.floor(candle.t / 1000),
    open: Number(candle.o),
    high: Number(candle.h),
    low: Number(candle.l),
    close: Number(candle.c),
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

function isRenderablePrice(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0
}

function getVariantStyle(variant) {
  return CHART_VARIANTS[variant] || CHART_VARIANTS.terminal
}

function normalizePeriods(periods) {
  if (!Array.isArray(periods)) return []
  return periods
    .map((p) => Number(p))
    .filter((p) => Number.isFinite(p) && p >= 2)
    .map((p) => Math.floor(p))
    .slice(0, 3)
}

function toMovingAverageData(candles, period) {
  if (!Array.isArray(candles) || candles.length < period) return []

  const points = []
  let running = 0
  for (let i = 0; i < candles.length; i += 1) {
    running += Number(candles[i].c)
    if (i >= period) running -= Number(candles[i - period].c)
    if (i >= period - 1) {
      points.push({
        time: Math.floor(candles[i].t / 1000),
        value: running / period,
      })
    }
  }

  return points
}

function pickBarSpacing(barCount, table, fallback) {
  if (!Array.isArray(table) || table.length === 0) return fallback
  for (const row of table) {
    const maxBars = Number(row?.maxBars)
    const spacing = Number(row?.spacing)
    if (!Number.isFinite(maxBars) || !Number.isFinite(spacing)) continue
    if (barCount <= maxBars) return spacing
  }
  return fallback
}

function pickBarSpacingByPrice(price, table, fallback) {
  if (!Number.isFinite(price)) return fallback
  if (!Array.isArray(table) || table.length === 0) return fallback

  const sorted = [...table]
    .map((row) => ({
      maxPrice: Number(row?.maxPrice),
      spacing: Number(row?.spacing),
    }))
    .filter((row) => Number.isFinite(row.maxPrice) && Number.isFinite(row.spacing))
    .sort((a, b) => a.maxPrice - b.maxPrice)

  for (const row of sorted) {
    if (price <= row.maxPrice) return row.spacing
  }
  return fallback
}

const DEFAULT_PRICE_FORMAT = {
  type: 'price',
  precision: 8,
  minMove: 0.00000001,
}

function exitPointColor(label, isFinal) {
  if (isFinal) return '#38bdf8'
  const labelLower = String(label || '').toLowerCase()
  if (labelLower.includes('tp')) return '#22c55e'
  if (labelLower.includes('stop') || labelLower.includes('sl')) return '#ef4444'
  return '#7dd3fc'
}

function normalizeExitPoints(markers, hideExitUntilReveal, revealTs) {
  const points = []
  const seen = new Set()

  const add = (rawTs, rawLabel, isFinal = false) => {
    const ts = Number(rawTs)
    if (!Number.isFinite(ts)) return
    if (hideExitUntilReveal && revealTs != null && ts > revealTs) return
    const label = String(rawLabel || (isFinal ? 'FINAL EXIT' : 'EXIT'))
    const key = `${ts}:${label}`
    if (seen.has(key)) return
    seen.add(key)
    points.push({ ts, label, isFinal, color: exitPointColor(label, isFinal) })
  }

  if (Array.isArray(markers?.exit_points)) {
    markers.exit_points.forEach((point, idx) => {
      if (point && typeof point === 'object') {
        add(point.ts, point.label || `EXIT ${idx + 1}`, Boolean(point.is_final))
      } else {
        add(point, `EXIT ${idx + 1}`, false)
      }
    })
  }

  const finalExitTs = Number(markers?.exit_ts)
  if (Number.isFinite(finalExitTs)) {
    add(finalExitTs, 'FINAL EXIT', true)
  }

  return points.sort((a, b) => a.ts - b.ts)
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
  chartVariant = 'terminal',
  showMovingAverages = false,
  movingAveragePeriods = [9, 21, 50],
  thinTemporalMarkers = false,
  showVolume = false,
  targetBarSpacing = null,
  autoBarSpacing = false,
  barSpacingTable = null,
  autoBarSpacingByPrice = false,
  priceBarSpacingTable = null,
  showCloseLine = false,
}) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const volumeSeriesRef = useRef(null)
  const closeLineSeriesRef = useRef(null)
  const maSeriesRef = useRef([])
  const priceLinesRef = useRef([])
  const markersRef = useRef(markers)
  const exitPointsRef = useRef([])
  const [markerCoords, setMarkerCoords] = useState({ entry: null, exits: [] })

  const clippedCandles = useMemo(() => {
    if (!Array.isArray(candles) || candles.length === 0) return []
    if (revealTs == null) return candles
    return candles.filter((c) => c.t <= revealTs)
  }, [candles, revealTs])

  const variantStyle = useMemo(() => getVariantStyle(chartVariant), [chartVariant])
  const maPeriods = useMemo(() => normalizePeriods(movingAveragePeriods), [movingAveragePeriods])
  const maKey = useMemo(() => maPeriods.join(','), [maPeriods])
  const showMAs = showMovingAverages && maPeriods.length > 0
  const barSpacingTableKey = useMemo(() => {
    if (!Array.isArray(barSpacingTable)) return ''
    return barSpacingTable.map((row) => `${row?.maxBars}:${row?.spacing}`).join('|')
  }, [barSpacingTable])
  const priceBarSpacingTableKey = useMemo(() => {
    if (!Array.isArray(priceBarSpacingTable)) return ''
    return priceBarSpacingTable.map((row) => `${row?.maxPrice}:${row?.spacing}`).join('|')
  }, [priceBarSpacingTable])

  const gapCount = useMemo(
    () => calcGapCount(clippedCandles, expectedGapMs),
    [clippedCandles, expectedGapMs]
  )

  const hideExitPrice = (
    hideExitUntilReveal
    && revealTs != null
    && markers?.exit_ts != null
    && revealTs < Number(markers.exit_ts)
  )

  const exitPoints = useMemo(
    () => normalizeExitPoints(markers, hideExitUntilReveal, revealTs),
    [markers, hideExitUntilReveal, revealTs]
  )

  useEffect(() => {
    markersRef.current = markers
    exitPointsRef.current = exitPoints
  }, [markers, exitPoints])

  const levelLegend = useMemo(() => {
    if (hideLevels) return []

    const items = []
    if (isRenderablePrice(markers?.entry_px)) {
      items.push({ key: 'entry', label: 'ENTRY', price: markers.entry_px, className: 'text-amber-300 border-amber-500/60 bg-amber-500/15' })
    }
    if (isRenderablePrice(markers?.stop_px)) {
      items.push({ key: 'stop', label: 'STOP', price: markers.stop_px, className: 'text-red-300 border-red-500/60 bg-red-500/15' })
    }
    if (Array.isArray(markers?.tp_px)) {
      markers.tp_px.forEach((price, idx) => {
        if (!isRenderablePrice(price)) return
        items.push({ key: `tp-${idx}`, label: `TP${idx + 1}`, price, className: 'text-emerald-300 border-emerald-500/60 bg-emerald-500/15' })
      })
    }
    if (!hideExitPrice && isRenderablePrice(markers?.exit_px)) {
      items.push({ key: 'exit', label: 'EXIT', price: markers.exit_px, className: 'text-sky-300 border-sky-500/60 bg-sky-500/15' })
    }
    return items
  }, [markers, hideLevels, hideExitPrice])

  const syncMarkerCoords = useCallback(() => {
    const chart = chartRef.current
    const markersCurrent = markersRef.current
    const exitPointsCurrent = exitPointsRef.current
    if (!chart || !markersCurrent) return

    const timeScale = chart.timeScale()
    const entryX = markersCurrent.entry_ts
      ? timeScale.timeToCoordinate(Math.floor(Number(markersCurrent.entry_ts) / 1000))
      : null

    const exits = exitPointsCurrent
      .map((point) => {
        const x = timeScale.timeToCoordinate(Math.floor(point.ts / 1000))
        return {
          ...point,
          x: Number.isFinite(x) ? x : null,
        }
      })
      .filter((point) => point.x !== null)

    setMarkerCoords({
      entry: Number.isFinite(entryX) ? entryX : null,
      exits,
    })
  }, [])

  useEffect(() => {
    if (!containerRef.current) return undefined

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: variantStyle.background },
        textColor: variantStyle.text,
      },
      grid: {
        vertLines: { color: variantStyle.grid },
        horzLines: { color: variantStyle.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: variantStyle.crosshair, style: LineStyle.Dashed, width: 1 },
        horzLine: { color: variantStyle.crosshair, style: LineStyle.Dashed, width: 1 },
      },
      timeScale: {
        borderColor: variantStyle.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 0,
      },
      rightPriceScale: {
        borderColor: variantStyle.border,
      },
      width: Math.max(containerRef.current.clientWidth || 0, 120),
      height,
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: variantStyle.up,
      downColor: variantStyle.down,
      borderUpColor: variantStyle.up,
      borderDownColor: variantStyle.down,
      wickUpColor: variantStyle.up,
      wickDownColor: variantStyle.down,
      priceLineVisible: false,
      lastValueVisible: !compact,
      priceFormat: DEFAULT_PRICE_FORMAT,
    })
    chartRef.current = chart
    seriesRef.current = candleSeries

    if (showVolume) {
      try {
        const volumeSeries = chart.addHistogramSeries({
          priceScaleId: 'volume',
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        })
        volumeSeriesRef.current = volumeSeries
        candleSeries.priceScale().applyOptions({
          scaleMargins: {
            top: 0.03,
            bottom: 0.28,
          },
        })
        chart.priceScale('volume').applyOptions({
          scaleMargins: {
            top: 0.74,
            bottom: 0,
          },
          borderVisible: false,
        })
      } catch (err) {
        // Keep candle rendering alive even if histogram panel fails at runtime.
        console.warn('volume pane disabled for this chart', err)
        volumeSeriesRef.current = null
      }
    } else {
      volumeSeriesRef.current = null
    }

    if (showCloseLine) {
      const closeLineSeries = chart.addLineSeries({
        color: '#dbeafe',
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        crosshairMarkerVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      closeLineSeriesRef.current = closeLineSeries
    } else {
      closeLineSeriesRef.current = null
    }

    const maSeries = []
    if (showMAs) {
      maPeriods.forEach((period, idx) => {
        const lineSeries = chart.addLineSeries({
          color: MA_COLORS[idx % MA_COLORS.length],
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          crosshairMarkerVisible: false,
          priceLineVisible: false,
          lastValueVisible: false,
          title: `MA ${period}`,
        })
        maSeries.push({ period, series: lineSeries })
      })
    }

    maSeriesRef.current = maSeries

    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current || !chartRef.current) return
      const width = Math.max(containerRef.current.clientWidth || 0, 120)
      chartRef.current.applyOptions({
        width,
        height,
      })
      chartRef.current.timeScale().fitContent()
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
      volumeSeriesRef.current = null
      closeLineSeriesRef.current = null
      maSeriesRef.current = []
    }
  }, [
    height,
    compact,
    variantStyle,
    showMAs,
    showVolume,
    showCloseLine,
    maKey,
    syncMarkerCoords,
  ])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const series = seriesRef.current
    const chart = chartRef.current

    const chartData = clippedCandles
      .map(toChartPoint)
      .filter((point) => (
        Number.isFinite(point.time)
        && Number.isFinite(point.open)
        && Number.isFinite(point.high)
        && Number.isFinite(point.low)
        && Number.isFinite(point.close)
      ))
    series.setData(chartData)
    if (showCloseLine && closeLineSeriesRef.current) {
      closeLineSeriesRef.current.setData(
        clippedCandles.map((c) => ({
          time: Math.floor(c.t / 1000),
          value: Number(c.c),
        })).filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
      )
    }
    if (showVolume && volumeSeriesRef.current) {
      try {
        volumeSeriesRef.current.setData(
          clippedCandles.map((c) => ({
            time: Math.floor(c.t / 1000),
            value: Number(c.v || 0),
            color: Number(c.c) >= Number(c.o) ? `${variantStyle.up}88` : `${variantStyle.down}88`,
          })).filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
        )
      } catch (err) {
        console.warn('volume series update skipped', err)
      }
    }

    if (showMAs && maSeriesRef.current.length > 0) {
      maSeriesRef.current.forEach(({ period, series: maSeries }) => {
        maSeries.setData(toMovingAverageData(clippedCandles, period))
      })
    }

    const referencePrice = Number(chartData[chartData.length - 1]?.close)
    let chosenBarSpacing = autoBarSpacingByPrice
      ? pickBarSpacingByPrice(referencePrice, priceBarSpacingTable, targetBarSpacing)
      : (
          autoBarSpacing
            ? pickBarSpacing(chartData.length, barSpacingTable, targetBarSpacing)
            : targetBarSpacing
        )
    const containerWidth = containerRef.current?.clientWidth || 0
    if (Number.isFinite(chosenBarSpacing) && chosenBarSpacing > 0 && containerWidth > 0 && chartData.length > 0) {
      const maxSafeSpacing = Math.max(1, (containerWidth - 24) / chartData.length)
      chosenBarSpacing = Math.min(chosenBarSpacing, maxSafeSpacing)
    }
    if (Number.isFinite(chosenBarSpacing) && chosenBarSpacing > 0) {
      try {
        chart.timeScale().applyOptions({
          barSpacing: chosenBarSpacing,
          minBarSpacing: 1,
        })
      } catch {
        // Ignore incompatible runtime versions/options.
      }
    }
    chart.timeScale().fitContent()
    requestAnimationFrame(syncMarkerCoords)
  }, [
    clippedCandles,
    showMAs,
    showVolume,
    showCloseLine,
    syncMarkerCoords,
    variantStyle,
    targetBarSpacing,
    autoBarSpacing,
    barSpacingTableKey,
    barSpacingTable,
    autoBarSpacingByPrice,
    priceBarSpacingTable,
    priceBarSpacingTableKey,
    maKey,
  ])

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
      if (!isRenderablePrice(price)) return
      const line = series.createPriceLine({
        price: Number(price),
        color,
        lineWidth: compact ? 1 : 2,
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
    if (!hideExitPrice) {
      addLine(markers.exit_px, '#38bdf8', 'EXIT')
    }
  }, [markers, hideLevels, hideExitPrice, clippedCandles.length, compact])

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
            <div className={`absolute top-0 bottom-0 ${thinTemporalMarkers ? 'w-px' : 'w-0.5'} bg-amber-300`} />
            {!compact && (
              <span className="absolute top-1 left-1 px-1 py-0.5 text-[9px] font-bold tracking-wide rounded border border-amber-500/70 bg-amber-500/25 text-amber-200">
                ENTRY
              </span>
            )}
          </div>
        )}

        {markerCoords.exits.map((point) => (
          <div key={`${point.ts}:${point.label}`} className="absolute top-0 bottom-0" style={{ left: `${point.x}px` }}>
            <div
              className={`absolute top-0 bottom-0 ${thinTemporalMarkers ? 'w-px' : 'w-0.5'}`}
              style={{ backgroundColor: point.color }}
            />
            {!compact && (
              <span
                className="absolute top-1 left-1 px-1 py-0.5 text-[9px] font-bold tracking-wide rounded border"
                style={{
                  color: point.color,
                  borderColor: `${point.color}99`,
                  backgroundColor: `${point.color}22`,
                }}
              >
                {point.label}
              </span>
            )}
          </div>
        ))}
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
