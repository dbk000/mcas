export function groupPnLByDay(items = [], tsField = 'exit_ts', pnlField = 'pnl_net') {
  const grouped = new Map()

  for (const item of items || []) {
    const timestamp = item?.[tsField]
    const pnl = Number(item?.[pnlField])

    if (!timestamp || !Number.isFinite(pnl)) {
      continue
    }

    const parsed = new Date(timestamp)
    if (Number.isNaN(parsed.getTime())) {
      continue
    }

    const day = parsed.toISOString().slice(0, 10)
    const bucket = grouped.get(day) || {
      day,
      grossProfit: 0,
      grossLossAbs: 0,
      trades: 0,
    }

    if (pnl > 0) {
      bucket.grossProfit += pnl
    } else if (pnl < 0) {
      bucket.grossLossAbs += Math.abs(pnl)
    }

    bucket.trades += 1
    grouped.set(day, bucket)
  }

  return Array.from(grouped.values()).sort((left, right) => left.day.localeCompare(right.day))
}

export function profitFactor(grossProfit = 0, grossLossAbs = 0) {
  const safeGrossProfit = Number(grossProfit) || 0
  const safeGrossLossAbs = Number(grossLossAbs) || 0

  if (safeGrossProfit === 0 && safeGrossLossAbs === 0) {
    return null
  }

  if (safeGrossLossAbs === 0) {
    return safeGrossProfit > 0 ? Infinity : 0
  }

  return safeGrossProfit / safeGrossLossAbs
}

export function averageDailyPF(items = [], tsField = 'exit_ts', pnlField = 'pnl_net') {
  const dailyBuckets = groupPnLByDay(items, tsField, pnlField)
  const finiteValues = []
  let infiniteProfitableDays = 0

  for (const bucket of dailyBuckets) {
    const dailyProfitFactor = profitFactor(bucket.grossProfit, bucket.grossLossAbs)

    if (dailyProfitFactor === Infinity) {
      infiniteProfitableDays += 1
      continue
    }

    if (Number.isFinite(dailyProfitFactor)) {
      finiteValues.push(dailyProfitFactor)
    }
  }

  let value = null
  if (finiteValues.length > 0) {
    value = finiteValues.reduce((sum, current) => sum + current, 0) / finiteValues.length
  } else if (infiniteProfitableDays > 0) {
    value = Infinity
  }

  return {
    value,
    daysCountedFinite: finiteValues.length,
    daysExcludedInfiniteProfitable: infiniteProfitableDays,
    totalTradingDays: dailyBuckets.length,
    dailyBuckets,
  }
}
