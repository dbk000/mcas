import { useCallback, useMemo, useState } from 'react'
import {
  AlertCircle,
  BarChart3,
  ChevronDown,
  ChevronUp,
  Lightbulb,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../../components/ui'

const EMPTY_ARRAY = []
const EMPTY_OBJECT = {}

function FeatureBar({
  details,
  direction,
  expanded,
  feature,
  importance,
  onToggle,
  rank,
}) {
  const maxImportance = 1.5
  const barWidth = Math.min(100, (importance / maxImportance) * 100)

  return (
    <div className="space-y-2">
      <button
        onClick={() => onToggle(feature)}
        className="group flex w-full items-center gap-3"
      >
        <span className="w-6 text-sm text-terminal-400">{rank}.</span>
        <div className="flex-1">
          <div className="mb-1 flex items-center justify-between">
            <span className="truncate font-mono text-sm text-terminal-100">{feature}</span>
            <div className="flex items-center gap-2">
              {direction === 'positive' ? (
                <TrendingUp className="size-4 text-signal-long" />
              ) : (
                <TrendingDown className="size-4 text-signal-short" />
              )}
              <span className="font-mono text-sm text-terminal-400">d={importance.toFixed(2)}</span>
              {expanded ? (
                <ChevronUp className="size-4 text-terminal-400" />
              ) : (
                <ChevronDown className="size-4 text-terminal-400 opacity-0 transition-opacity group-hover:opacity-100" />
              )}
            </div>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-terminal-700">
            <div
              className={`h-full rounded-full ${direction === 'positive' ? 'bg-signal-long' : 'bg-signal-short'}`}
              style={{ width: `${barWidth}%` }}
            />
          </div>
        </div>
      </button>

      {expanded && details && (
        <div className="ml-9 space-y-3 rounded-lg bg-terminal-900 p-3">
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-terminal-400">Winner Mean</p>
              <p className="font-mono text-signal-long">{details.winner_mean?.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-terminal-400">Loser Mean</p>
              <p className="font-mono text-signal-short">{details.loser_mean?.toFixed(4)}</p>
            </div>
          </div>

          {details.bins && details.bins.length > 0 && (
            <div>
              <p className="mb-2 text-sm text-terminal-400">Win Rate by Value Range</p>
              <div className="space-y-1">
                {details.bins.map((bin, index) => (
                  <div key={index} className="flex items-center gap-2 text-xs">
                    <span className="w-24 truncate font-mono text-terminal-400">{bin.range}</span>
                    <div className="relative h-4 flex-1 overflow-hidden rounded bg-terminal-700">
                      <div
                        className={`h-full ${bin.win_rate >= 0.5 ? 'bg-signal-long/60' : 'bg-signal-short/60'}`}
                        style={{ width: `${bin.win_rate * 100}%` }}
                      />
                      <span className="absolute inset-0 flex items-center justify-center text-terminal-100">
                        {(bin.win_rate * 100).toFixed(0)}%
                      </span>
                    </div>
                    <span className="w-12 text-right text-terminal-400">n={bin.count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {details.insight && (
            <p className="text-sm italic text-amber-400">{details.insight}</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function AnalysisResults({ data }) {
  const [expandedFeature, setExpandedFeature] = useState(null)
  const summary = data?.summary ?? EMPTY_OBJECT
  const featureAnalyses = data?.feature_analyses ?? EMPTY_OBJECT
  const insights = data?.insights ?? EMPTY_ARRAY
  const topPredictors = useMemo(
    () => (data?.top_predictors ?? EMPTY_ARRAY).slice(0, 10),
    [data?.top_predictors]
  )
  const exitReasonEntries = useMemo(
    () => Object.entries(data?.exit_reason_breakdown ?? EMPTY_OBJECT)
      .sort((left, right) => right[1].count - left[1].count),
    [data?.exit_reason_breakdown]
  )
  const handleFeatureToggle = useCallback((feature) => {
    setExpandedFeature((currentFeature) => (currentFeature === feature ? null : feature))
  }, [])

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className="text-3xl font-bold text-terminal-100">{summary?.total_trades || 0}</p>
            <p className="text-sm text-terminal-400">Total Trades</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className="text-3xl font-bold text-signal-long">{summary?.winning_trades || 0}</p>
            <p className="text-sm text-terminal-400">Winners</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className="text-3xl font-bold text-signal-short">{summary?.losing_trades || 0}</p>
            <p className="text-sm text-terminal-400">Losers</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={`text-3xl font-bold ${(summary?.win_rate || 0) >= 0.5 ? 'text-signal-long' : 'text-signal-short'}`}>
              {((summary?.win_rate || 0) * 100).toFixed(1)}%
            </p>
            <p className="text-sm text-terminal-400">Win Rate</p>
          </CardContent>
        </Card>
      </div>

      {summary?.truncated && (
        <Card className="border-amber-600/50 bg-amber-900/10">
          <CardContent className="flex items-center gap-3">
            <AlertCircle className="size-5 text-amber-400" />
            <p className="text-sm text-amber-400">
              Analysis was truncated to 2000 trades. Results may not represent the full population.
            </p>
          </CardContent>
        </Card>
      )}

      {insights.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lightbulb className="size-4 text-amber-400" />
              Key Insights
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {insights.map((insight, index) => (
                <li key={index} className="flex items-start gap-2 text-sm">
                  <span className="mt-1 text-amber-500">•</span>
                  <span className="text-terminal-200">{insight}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {topPredictors.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="size-4" />
              Top Feature Predictors
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {topPredictors.map((predictor, index) => (
                <FeatureBar
                  key={predictor.feature}
                  rank={index + 1}
                  feature={predictor.feature}
                  importance={predictor.importance}
                  direction={predictor.direction}
                  expanded={expandedFeature === predictor.feature}
                  onToggle={handleFeatureToggle}
                  details={featureAnalyses[predictor.feature]}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {exitReasonEntries.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Exit Reason Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-terminal-600">
                    <th className="py-2 text-left font-medium text-terminal-400">Exit Reason</th>
                    <th className="py-2 text-right font-medium text-terminal-400">Count</th>
                    <th className="py-2 text-right font-medium text-terminal-400">Win Rate</th>
                    <th className="py-2 text-right font-medium text-terminal-400">Avg R</th>
                  </tr>
                </thead>
                <tbody>
                  {exitReasonEntries.map(([reason, stats]) => (
                    <tr key={reason} className="border-b border-terminal-700">
                      <td className="py-2 text-terminal-100">{reason}</td>
                      <td className="py-2 text-right font-mono text-terminal-200">{stats.count}</td>
                      <td className={`py-2 text-right font-mono ${stats.win_rate >= 0.5 ? 'text-signal-long' : 'text-signal-short'}`}>
                        {(stats.win_rate * 100).toFixed(1)}%
                      </td>
                      <td className={`py-2 text-right font-mono ${stats.avg_r >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                        {stats.avg_r >= 0 ? '+' : ''}{stats.avg_r?.toFixed(2)}R
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
