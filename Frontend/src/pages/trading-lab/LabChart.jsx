import { useEffect, useMemo, useRef } from 'react'
import { createChart, ColorType } from 'lightweight-charts'
import { normalizeCandles } from './model'

export default function LabChart({ candles, theme, marketKey, scale }) {
  const element = useRef(null)
  const chartRef = useRef(null)
  const series = useRef(null)
  const fitted = useRef(false)
  const bars = useMemo(() => normalizeCandles(candles), [candles])

  useEffect(() => {
    const light = theme === 'light'
    const chart = createChart(element.current, {
      layout: {
        background: {
          type: ColorType.Solid,
          color: light ? '#ffffff' : '#11171e',
        },
        textColor: light ? '#4d5b69' : '#9cabb9',
        fontFamily: 'ui-monospace, monospace',
        fontSize: Math.round((12 * scale) / 100),
      },
      grid: {
        vertLines: { color: light ? '#edf0f3' : '#1c252f' },
        horzLines: { color: light ? '#edf0f3' : '#1c252f' },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.1, bottom: 0.24 },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderVisible: false,
      },
      crosshair: { mode: 0 },
      localization: { locale: 'en-IE' },
      handleScroll: true,
      handleScale: true,
    })
    const candle = chart.addCandlestickSeries({
      upColor: '#32c59a',
      downColor: '#ef7784',
      borderVisible: false,
      wickUpColor: '#32c59a',
      wickDownColor: '#ef7784',
    })
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    volume
      .priceScale()
      .applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false })
    chartRef.current = chart
    series.current = { candle, volume }
    fitted.current = false
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      chart.applyOptions({
        width: Math.max(1, Math.floor(width)),
        height: Math.max(1, Math.floor(height)),
      })
    })
    observer.observe(element.current)
    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
      series.current = null
    }
  }, [theme, marketKey, scale])

  useEffect(() => {
    if (!series.current) return
    series.current.candle.setData(bars)
    series.current.volume.setData(
      bars.map((bar) => ({
        time: bar.time,
        value: bar.volume,
        color: bar.close >= bar.open ? '#32c59a55' : '#ef778455',
      }))
    )
    if (bars.length && !fitted.current) {
      chartRef.current.timeScale().fitContent()
      fitted.current = true
    }
  }, [bars, marketKey, theme, scale])

  return (
    <div
      className="lab-chart"
      ref={element}
      role="img"
      aria-label={`${marketKey}: ${bars.length} finalized candlesticks and volume. Drag to pan, scroll to zoom.`}
    />
  )
}
