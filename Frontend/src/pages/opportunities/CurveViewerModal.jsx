import { RefreshCw } from 'lucide-react'
import { useOpportunityPriceWindow } from '../../hooks/useData'
import {
  Modal,
  Skeleton,
  DataRow,
} from '../../components/ui'
import { formatDateTime, formatPrice } from '../../utils/formatters'
import OpportunityCurveChart from '../../components/opportunities/OpportunityCurveChart'
import { CURVE_VIEWER_CHART_PROPS } from '../../components/opportunities/chartPresets'

export default function CurveViewerModal({ opportunity, onClose }) {
  const { data, loading, error, refetch } = useOpportunityPriceWindow(
    opportunity?.id,
    { pre_s: 3600, post_s: 21600 },
    !!opportunity?.id
  )

  const markers = data?.markers || {}
  const entryIso = markers.entry_ts ? new Date(markers.entry_ts).toISOString() : null
  const exitIso = markers.exit_ts ? new Date(markers.exit_ts).toISOString() : null
  const hasGaps = data?.data_quality?.is_gappy

  return (
    <Modal
      isOpen={!!opportunity}
      onClose={onClose}
      size="xl"
      title={opportunity ? `Curve • ${opportunity.symbol} #${opportunity.id}` : 'Curve'}
    >
      {!opportunity ? null : loading && !data ? (
        <Skeleton className="h-[420px]" />
      ) : error ? (
        <div className="space-y-3">
          <p className="text-terminal-300">Failed to load curve: {error}</p>
          <button className="btn btn-secondary btn-sm" onClick={refetch}>
            <RefreshCw className="size-4" />
            Retry
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <OpportunityCurveChart
            candles={data?.candles || []}
            markers={markers}
            {...CURVE_VIEWER_CHART_PROPS}
          />

          {data?.data_quality?.empty && (
            <p className="text-sm text-terminal-400">No 5m candles found in this window.</p>
          )}
          {hasGaps && (
            <p className="text-sm text-amber-300">Data has gaps. Pattern interpretation may be distorted.</p>
          )}

          <div className="grid grid-cols-2 gap-3 border-t border-terminal-600 pt-2 md:grid-cols-4">
            <DataRow label="Entry Time" value={formatDateTime(entryIso)} />
            <DataRow label="Exit Time" value={formatDateTime(exitIso)} />
            <DataRow label="Entry Px" value={formatPrice(markers.entry_px)} />
            <DataRow label="Stop Px" value={formatPrice(markers.stop_px)} />
          </div>
        </div>
      )}
    </Modal>
  )
}
