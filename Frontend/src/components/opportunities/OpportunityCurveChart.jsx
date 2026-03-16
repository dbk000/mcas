import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getEventColor } from './chartEventPalette'
import { getLogger } from '../../utils/logger'

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

const chartLog = getLogger('opportunity_chart')

let chartingModulePromise = null

function loadChartingLibrary() {
  if (!chartingModulePromise) {
    chartingModulePromise = import('lightweight-charts')
  }
  return chartingModulePromise
}

function areMarkerPointsEqual(leftPoints, rightPoints) {
  if (leftPoints.length !== rightPoints.length) return false

  for (let index = 0; index < leftPoints.length; index += 1) {
    const left = leftPoints[index]
    const right = rightPoints[index]

    if (
      left.ts !== right.ts ||
      left.label !== right.label ||
      left.x !== right.x ||
      left.color !== right.color ||
      left.isFinal !== right.isFinal
    ) {
      return false
    }
  }

  return true
}

function areAxisEventsEqual(leftEvents, rightEvents) {
  if (leftEvents.length !== rightEvents.length) return false

  for (let index = 0; index < leftEvents.length; index += 1) {
    const left = leftEvents[index]
    const right = rightEvents[index]

    if (
      left.key !== right.key ||
      left.label !== right.label ||
      left.shortLabel !== right.shortLabel ||
      left.x !== right.x ||
      left.color !== right.color ||
      left.lane !== right.lane
    ) {
      return false
    }
  }

  return true
}

function areMarkerCoordsEqual(previous, next) {
  return (
    previous.entry === next.entry &&
    areMarkerPointsEqual(previous.exits, next.exits) &&
    areAxisEventsEqual(previous.axisEvents, next.axisEvents)
  )
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
    points.push({ ts, label, isFinal, color: getEventColor(label, { isFinal }) })
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

function toEventShortLabel(label, isFinal = false) {
  const text = String(label || '').toLowerCase()
  if (isFinal || text.includes('final')) return 'FX'
  if (text.includes('entry')) return 'E'
  if (text.includes('stop') || text.includes('sl')) return 'SL'

  const tpMatch = text.match(/tp\s*([0-9]+)/i)
  if (tpMatch?.[1]) return `T${tpMatch[1]}`

  if (text.includes('partial')) return 'PX'
  if (text.includes('exit')) return 'X'
  return 'EV'
}

function toEventCoordinate(tsMs, timeScale, candles) {
  const ts = Number(tsMs)
  if (!Number.isFinite(ts) || !timeScale || !Array.isArray(candles) || candles.length === 0) {
    return null
  }

  const tsSec = Math.floor(ts / 1000)
  const exact = timeScale.timeToCoordinate(tsSec)
  if (Number.isFinite(exact)) return exact

  const firstSec = Math.floor(Number(candles[0]?.t) / 1000)
  const lastSec = Math.floor(Number(candles[candles.length - 1]?.t) / 1000)
  if (!Number.isFinite(firstSec) || !Number.isFinite(lastSec)) return null
  if (tsSec < firstSec || tsSec > lastSec) return null

  let lo = 0
  let hi = candles.length - 1
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2)
    const midSec = Math.floor(Number(candles[mid]?.t) / 1000)
    if (midSec < tsSec) lo = mid + 1
    else hi = mid
  }

  const rightIdx = lo
  const rightSec = Math.floor(Number(candles[rightIdx]?.t) / 1000)
  if (rightSec === tsSec) {
    const rightX = timeScale.timeToCoordinate(rightSec)
    return Number.isFinite(rightX) ? rightX : null
  }

  const leftIdx = rightIdx - 1
  if (leftIdx < 0) return null
  const leftSec = Math.floor(Number(candles[leftIdx]?.t) / 1000)
  if (!Number.isFinite(leftSec) || !Number.isFinite(rightSec) || rightSec <= leftSec) return null

  const leftX = timeScale.timeToCoordinate(leftSec)
  const rightX = timeScale.timeToCoordinate(rightSec)
  if (!Number.isFinite(leftX) || !Number.isFinite(rightX)) return null

  const ratio = (tsSec - leftSec) / (rightSec - leftSec)
  return leftX + ((rightX - leftX) * ratio)
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
  showLevelLineLabels = true,
  showLevelLegend = true,
}) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const volumeSeriesRef = useRef(null)
  const closeLineSeriesRef = useRef(null)
  const maSeriesRef = useRef([])
  const priceLinesRef = useRef([])
  const syncRafRef = useRef(0)
  const markersRef = useRef(markers)
  const exitPointsRef = useRef([])
  const clippedCandlesRef = useRef([])
  const chartingModuleRef = useRef(null)
  const [markerCoords, setMarkerCoords] = useState({ entry: null, exits: [], axisEvents: [] })
  const [chartStatus, setChartStatus] = useState('idle')

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

  const chartData = useMemo(
    () => clippedCandles
      .map(toChartPoint)
      .filter((point) => (
        Number.isFinite(point.time)
        && Number.isFinite(point.open)
        && Number.isFinite(point.high)
        && Number.isFinite(point.low)
        && Number.isFinite(point.close)
      )),
    [clippedCandles]
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

  const closeLineData = useMemo(() => {
    if (!showCloseLine) return []

    return clippedCandles
      .map((candle) => ({
        time: Math.floor(candle.t / 1000),
        value: Number(candle.c),
      }))
      .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
  }, [clippedCandles, showCloseLine])

  const volumeData = useMemo(() => {
    if (!showVolume) return []

    return clippedCandles
      .map((candle) => ({
        time: Math.floor(candle.t / 1000),
        value: Number(candle.v || 0),
        color: Number(candle.c) >= Number(candle.o) ? `${variantStyle.up}88` : `${variantStyle.down}88`,
      }))
      .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
  }, [clippedCandles, showVolume, variantStyle.up, variantStyle.down])

  const movingAverageData = useMemo(() => {
    if (!showMAs) return []

    return maPeriods.map((period) => ({
      period,
      data: toMovingAverageData(clippedCandles, period),
    }))
  }, [clippedCandles, showMAs, maPeriods])

  useEffect(() => {
    markersRef.current = markers
    exitPointsRef.current = exitPoints
  }, [markers, exitPoints])

  useEffect(() => {
    clippedCandlesRef.current = clippedCandles
  }, [clippedCandles])

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
    const candlesCurrent = clippedCandlesRef.current
    if (!chart || !markersCurrent) return

    const timeScale = chart.timeScale()
    const entryX = markersCurrent.entry_ts
      ? toEventCoordinate(Number(markersCurrent.entry_ts), timeScale, candlesCurrent)
      : null

    const exits = exitPointsCurrent
      .map((point) => {
        const x = toEventCoordinate(Number(point.ts), timeScale, candlesCurrent)
        return {
          ...point,
          x: Number.isFinite(x) ? x : null,
        }
      })
      .filter((point) => point.x !== null)

    const axisEventBase = []
    if (Number.isFinite(entryX)) {
      axisEventBase.push({
        key: 'entry',
        label: 'ENTRY',
        shortLabel: toEventShortLabel('ENTRY'),
        x: entryX,
        color: getEventColor('ENTRY'),
      })
    }
    exits.forEach((point) => {
      axisEventBase.push({
        key: `exit-${point.ts}-${point.label}`,
        label: point.label,
        shortLabel: toEventShortLabel(point.label, point.isFinal),
        x: point.x,
        color: point.color,
      })
    })

    const laneLastX = [-Infinity, -Infinity, -Infinity]
    const axisEvents = axisEventBase
      .sort((a, b) => a.x - b.x)
      .map((event) => {
        let lane = 0
        while (lane < laneLastX.length && (event.x - laneLastX[lane]) < 44) {
          lane += 1
        }
        if (lane >= laneLastX.length) lane = laneLastX.length - 1
        laneLastX[lane] = event.x
        return { ...event, lane }
      })

    const nextMarkerCoords = {
      entry: Number.isFinite(entryX) ? entryX : null,
      exits,
      axisEvents,
    }

    setMarkerCoords((previous) => (
      areMarkerCoordsEqual(previous, nextMarkerCoords) ? previous : nextMarkerCoords
    ))
  }, [])

  useEffect(() => {
    if (!containerRef.current) return undefined

    let cancelled = false
    let chartInstance = null
    let resizeObserver = null
    let handleVisibleRangeChange = null

    setChartStatus('loading')

    async function initChart() {
      const loadStart = performance.now()

      try {
        const chartingModule = await loadChartingLibrary()
        if (cancelled || !containerRef.current) return

        chartingModuleRef.current = chartingModule
        const { createChart, CrosshairMode, LineStyle } = chartingModule

        chartInstance = createChart(containerRef.current, {
          layout: {
            background: { color: variantStyle.background },
            textColor: variantStyle.text,
            attributionLogo: false,
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

        const candleSeries = chartInstance.addCandlestickSeries({
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

        chartRef.current = chartInstance
        seriesRef.current = candleSeries

        if (showVolume) {
          try {
            const volumeSeries = chartInstance.addHistogramSeries({
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
            chartInstance.priceScale('volume').applyOptions({
              scaleMargins: {
                top: 0.74,
                bottom: 0,
              },
              borderVisible: false,
            })
          } catch (err) {
            chartLog.warn('Volume pane disabled for chart surface', {
              event: 'chart_volume_pane_disabled',
              error_message: err?.message || String(err),
            })
            volumeSeriesRef.current = null
          }
        } else {
          volumeSeriesRef.current = null
        }

        if (showCloseLine) {
          const closeLineSeries = chartInstance.addLineSeries({
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
            const lineSeries = chartInstance.addLineSeries({
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

        resizeObserver = new ResizeObserver(() => {
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

        handleVisibleRangeChange = () => syncMarkerCoords()
        chartInstance.timeScale().subscribeVisibleTimeRangeChange(handleVisibleRangeChange)

        setChartStatus('ready')
        chartLog.info('Chart engine ready', {
          event: 'chart_engine_ready',
          duration_ms: Math.round(performance.now() - loadStart),
          candle_count: clippedCandlesRef.current.length,
          moving_average_count: maPeriods.length,
        })
      } catch (err) {
        if (cancelled) return

        setChartStatus('error')
        chartLog.error('Chart engine failed to initialize', {
          event: 'chart_engine_failed',
          duration_ms: Math.round(performance.now() - loadStart),
          error_message: err?.message || String(err),
        })
      }
    }

    initChart()

    return () => {
      cancelled = true

      if (syncRafRef.current) {
        cancelAnimationFrame(syncRafRef.current)
        syncRafRef.current = 0
      }

      if (chartInstance && handleVisibleRangeChange) {
        chartInstance.timeScale().unsubscribeVisibleTimeRangeChange(handleVisibleRangeChange)
      }
      resizeObserver?.disconnect()
      chartInstance?.remove()

      chartRef.current = null
      seriesRef.current = null
      volumeSeriesRef.current = null
      closeLineSeriesRef.current = null
      maSeriesRef.current = []
      priceLinesRef.current = []
    }
  }, [
    height,
    compact,
    variantStyle,
    showMAs,
    showVolume,
    showCloseLine,
    maPeriods,
    maKey,
    syncMarkerCoords,
  ])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const series = seriesRef.current
    const chart = chartRef.current

    series.setData(chartData)
    if (showCloseLine && closeLineSeriesRef.current) {
      closeLineSeriesRef.current.setData(closeLineData)
    }
    if (showVolume && volumeSeriesRef.current) {
      try {
        volumeSeriesRef.current.setData(volumeData)
      } catch (err) {
        chartLog.warn('Volume series update skipped', {
          event: 'chart_volume_series_update_skipped',
          error_message: err?.message || String(err),
        })
      }
    }

    if (showMAs && maSeriesRef.current.length > 0) {
      maSeriesRef.current.forEach(({ period, series: maSeries }) => {
        const maEntry = movingAverageData.find((entry) => entry.period === period)
        maSeries.setData(maEntry?.data || [])
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
    if (syncRafRef.current) cancelAnimationFrame(syncRafRef.current)
    syncRafRef.current = requestAnimationFrame(() => {
      syncRafRef.current = 0
      syncMarkerCoords()
    })
  }, [
    chartData,
    closeLineData,
    volumeData,
    movingAverageData,
    showMAs,
    showVolume,
    showCloseLine,
    syncMarkerCoords,
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
    const lineStyle = chartingModuleRef.current?.LineStyle
    if (!series) return
    if (!lineStyle) return

    for (const line of priceLinesRef.current) {
      try {
        series.removePriceLine(line)
      } catch {
        // Ignore stale handles after rapid rerenders.
      }
    }
    priceLinesRef.current = []

    if (hideLevels || clippedCandles.length === 0 || !markers) return

    const addLine = (price, color, title, style = lineStyle.Solid) => {
      if (!isRenderablePrice(price)) return
      const line = series.createPriceLine({
        price: Number(price),
        color,
        lineWidth: compact ? 1 : 2,
        lineStyle: style,
        axisLabelVisible: showLevelLineLabels,
        title: showLevelLineLabels ? title : '',
      })
      priceLinesRef.current.push(line)
    }

    addLine(markers.entry_px, getEventColor('ENTRY'), 'ENTRY')
    addLine(markers.stop_px, getEventColor('STOP'), 'STOP', lineStyle.Dashed)
    if (Array.isArray(markers.tp_px)) {
      markers.tp_px.forEach((px, idx) => addLine(px, getEventColor(`TP${idx + 1}`), `TP${idx + 1}`, lineStyle.Dashed))
    }
    if (!hideExitPrice) {
      addLine(markers.exit_px, getEventColor('FINAL EXIT', { isFinal: true }), 'EXIT')
    }
  }, [markers, hideLevels, hideExitPrice, clippedCandles.length, compact, showLevelLineLabels])

  if (clippedCandles.length === 0) {
    return (
      <div
        className={`flex w-full items-center justify-center rounded border border-terminal-600 bg-terminal-900/50 text-sm text-terminal-400 ${className}`}
        style={{ height }}
      >
        No candles in current reveal window.
      </div>
    )
  }

  return (
    <div className={`relative w-full overflow-hidden rounded border border-terminal-600 ${className}`} style={{ height }}>
      <div ref={containerRef} className="size-full" />

      {chartStatus !== 'ready' && (
        <div className="bg-terminal-950/70 pointer-events-none absolute inset-0 z-50 flex items-center justify-center px-4 text-center text-xs font-medium uppercase tracking-[0.18em] text-terminal-300">
          {chartStatus === 'error' ? 'Chart engine unavailable' : 'Loading chart engine'}
        </div>
      )}

      <div className="pointer-events-none absolute inset-0 z-40">
        {markerCoords.entry !== null && (
          <div className="absolute inset-y-0" style={{ left: `${markerCoords.entry}px` }}>
            <div className={`absolute inset-y-0 ${thinTemporalMarkers ? 'w-px' : 'w-0.5'} bg-amber-300`} />
            {!compact && (
              <span className="absolute left-1 top-1 rounded border border-amber-500/70 bg-amber-500/25 px-1 py-0.5 text-[9px] font-bold tracking-wide text-amber-200">
                ENTRY
              </span>
            )}
          </div>
        )}

        {markerCoords.exits.map((point) => (
          <div key={`${point.ts}:${point.label}`} className="absolute inset-y-0" style={{ left: `${point.x}px` }}>
            <div
              className={`absolute inset-y-0 ${thinTemporalMarkers ? 'w-px' : 'w-0.5'}`}
              style={{ backgroundColor: point.color }}
            />
            {!compact && (
              <span
                className="absolute left-1 top-1 rounded border px-1 py-0.5 text-[9px] font-bold tracking-wide"
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

        {markerCoords.axisEvents.map((event) => (
          <div
            key={event.key}
            className="absolute z-50 flex -translate-x-1/2 flex-col items-center"
            style={{ left: `${event.x}px`, bottom: `${14 + (event.lane * 14)}px` }}
            title={event.label}
          >
            <span
              className="rounded-[3px] border px-1 font-mono text-[8px] font-bold leading-tight tracking-wide shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
              style={{
                color: event.color,
                borderColor: `${event.color}99`,
                backgroundColor: `${event.color}33`,
              }}
            >
              {event.shortLabel}
            </span>
            <div
              className={thinTemporalMarkers ? 'mt-0.5 h-2 w-px' : 'mt-0.5 h-3 w-px'}
              style={{ backgroundColor: event.color }}
            />
          </div>
        ))}
      </div>

      {showLevelLegend && !hideLevels && levelLegend.length > 0 && (
        <div className="pointer-events-none absolute inset-x-2 top-2 flex flex-wrap gap-1">
          {levelLegend.map((item) => (
            <span
              key={item.key}
              className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${item.className}`}
            >
              {item.label} {formatLevelPrice(item.price)}
            </span>
          ))}
        </div>
      )}

      {showGapBadge && gapCount > 0 && (
        <div className="pointer-events-none absolute bottom-2 left-2 rounded border border-amber-700/60 bg-terminal-900/90 px-2 py-1 text-[11px] text-amber-300">
          Gappy {gapLabel} ({gapCount} gap{gapCount > 1 ? 's' : ''})
        </div>
      )}
    </div>
  )
}
