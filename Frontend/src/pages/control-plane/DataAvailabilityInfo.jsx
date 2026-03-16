import { useDataAvailability } from '../../hooks/useData'

export default function DataAvailabilityInfo({ symbols, timeframes, startDate, endDate }) {
  const { data } = useDataAvailability(symbols, timeframes, {
    skip: !symbols?.length || !timeframes?.length || !startDate || !endDate,
  })

  if (!data?.availability || !symbols?.length) return null

  const availability = data.availability
  const missing = []
  const partial = []

  for (const symbol of symbols) {
    for (const timeframe of timeframes) {
      const key = `${symbol}_${timeframe}`
      const info = availability[key]
      if (!info || info.bar_count === 0) {
        missing.push(key)
      } else if (info.first_bar > startDate || info.last_bar < endDate) {
        partial.push({
          key,
          first: info.first_bar?.slice(0, 10),
          last: info.last_bar?.slice(0, 10),
          bars: info.bar_count,
        })
      }
    }
  }

  if (missing.length === 0 && partial.length === 0) {
    return (
      <div className="rounded border border-green-500/30 bg-green-500/10 p-2.5 text-sm text-green-400">
        Data available for all {symbols.length} symbol(s) across {timeframes.length} timeframe(s).
      </div>
    )
  }

  return (
    <div className="space-y-1 rounded border border-amber-500/30 bg-amber-500/10 p-2.5 text-sm">
      {missing.length > 0 && (
        <div className="text-amber-400">
          Missing data: {missing.slice(0, 5).join(', ')}
          {missing.length > 5 && ` +${missing.length - 5} more`}
        </div>
      )}
      {partial.length > 0 && (
        <div className="text-amber-300">
          Partial coverage: {partial.slice(0, 3).map((item) =>
            `${item.key} (${item.first} to ${item.last}, ${item.bars} bars)`
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
