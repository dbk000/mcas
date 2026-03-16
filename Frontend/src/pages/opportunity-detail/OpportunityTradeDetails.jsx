import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataRow,
  JsonView,
} from '../../components/ui'
import TraceViewer from '../../components/TraceViewer'
import {
  formatDollar,
  formatPrice,
  isEquitiesStrategy,
} from '../../utils/formatters'

function LevelBox({ color, label, value }) {
  return (
    <div className="rounded-lg border border-terminal-600 bg-terminal-900 p-4">
      <p className="mb-1 text-xs uppercase tracking-wider text-terminal-500">{label}</p>
      <p className={`font-mono text-lg font-bold ${color}`}>{value}</p>
    </div>
  )
}

export default function OpportunityTradeDetails({ opportunity, payload }) {
  const equitiesStrategy = isEquitiesStrategy(opportunity.setup_id)

  return (
    <div className="space-y-6 lg:col-span-2">
      <Card>
        <CardHeader>
          <CardTitle>{equitiesStrategy ? 'Signal Details' : 'Trade Levels'}</CardTitle>
        </CardHeader>
        <CardContent>
          {equitiesStrategy ? (
            <>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <LevelBox label="Entry Price" value={formatPrice(payload.entry_price)} color="text-terminal-100" />
                <LevelBox
                  label="Direction"
                  value={payload.direction || opportunity.direction}
                  color={payload.direction === 'LONG' ? 'text-signal-long' : 'text-signal-short'}
                />
                <LevelBox label="Horizon" value={`${payload.horizon_days}d`} color="text-terminal-100" />
                <LevelBox
                  label="Confidence"
                  value={`${(payload.confidence * 100).toFixed(0)}%`}
                  color="text-amber-400"
                />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-4 border-t border-terminal-600 pt-4">
                <DataRow label="Expected Return" value={`+${payload.expected_return_bps} bps`} />
                <DataRow label="Score" value={opportunity.score?.toFixed(2)} />
                <DataRow label="Strategy Version" value={payload.strategy_version || '—'} />
                {payload.exploratory && (
                  <DataRow label="Status" value="EXPLORATORY" />
                )}
              </div>
              {payload.explain && payload.explain.length > 0 && (
                <div className="mt-4 border-t border-terminal-600 pt-4">
                  <p className="mb-2 text-xs uppercase tracking-wider text-terminal-500">Signal Explanation</p>
                  <ul className="space-y-1">
                    {payload.explain.map((item, index) => (
                      <li key={index} className="text-sm text-terminal-300">{item}</li>
                    ))}
                  </ul>
                </div>
              )}
              {payload.features && Object.keys(payload.features).length > 0 && (
                <div className="mt-4 border-t border-terminal-600 pt-4">
                  <p className="mb-2 text-xs uppercase tracking-wider text-terminal-500">Feature Snapshot</p>
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                    {Object.entries(payload.features).map(([key, value]) => (
                      <div key={key} className="text-sm">
                        <span className="text-terminal-500">{key}</span>
                        <p className="font-mono text-terminal-100">
                          {typeof value === 'number' ? value.toFixed(4) : value}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <LevelBox label="Entry" value={formatPrice(payload.entry_price)} color="text-terminal-100" />
                <LevelBox label="Stop Loss" value={formatPrice(payload.stop_price)} color="text-signal-short" />
                <LevelBox label="Take Profit 1" value={formatPrice(payload.tp1_price)} color="text-signal-long" />
                <LevelBox label="Take Profit 2" value={formatPrice(payload.tp2_price)} color="text-signal-long" />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-4 border-t border-terminal-600 pt-4">
                <DataRow label="R-Value" value={formatDollar(payload.r_value)} />
                <DataRow label="Score" value={opportunity.score?.toFixed(2)} />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {opportunity.reason && (
        <Card>
          <CardHeader>
            <CardTitle>Signal Reason</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-terminal-200">{opportunity.reason}</p>
          </CardContent>
        </Card>
      )}

      {(opportunity.trace || opportunity.sim_trace) && (
        <Card>
          <CardHeader>
            <CardTitle>Computation Trace</CardTitle>
          </CardHeader>
          <CardContent>
            <TraceViewer trace={opportunity.trace} simTrace={opportunity.sim_trace} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Full Payload</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonView data={payload} expanded />
        </CardContent>
      </Card>
    </div>
  )
}
