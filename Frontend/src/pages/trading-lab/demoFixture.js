// Expand a compact, authored sample path for the preview only. Live feeds never
// call this function or substitute illustrative values for missing API data.
export function expandDemoFixture(fixture, tfSeconds) {
  const candles = {}
  const end = Date.parse(fixture.sample_as_of_ts)
  for (const market of fixture.heatmap.tiles) {
    for (const [tf, seconds] of Object.entries(tfSeconds)) {
      candles[`${market.symbol}:${tf}`] = {
        symbol: market.symbol,
        timeframe: tf,
        source: 'illustrative_sample',
        collector_as_of_ts: fixture.sample_as_of_ts,
        candles: fixture.candle_template.map(
          ([open, high, low, close, volume], index) => ({
            timestamp:
              end - (fixture.candle_template.length - index) * seconds * 1000,
            open: market.close * open,
            high: market.close * high,
            low: market.close * low,
            close: market.close * close,
            volume,
            is_final: true,
            source: 'illustrative_sample',
          })
        ),
      }
    }
  }
  return { ...fixture, candles }
}
