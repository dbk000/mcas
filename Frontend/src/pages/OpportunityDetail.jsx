import { useParams, Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { useOpportunity, useLiveOpportunity } from '../hooks/useData'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardContent,
  Skeleton,
} from '../components/ui'
import {
  formatTradePlan,
  copyToClipboard,
} from '../utils/formatters'
import { usePublicJson } from '../utils/publicJson'
import OpportunityDetailHeader from './opportunity-detail/OpportunityDetailHeader'
import OpportunityTradeDetails from './opportunity-detail/OpportunityTradeDetails'
import OpportunitySidebar from './opportunity-detail/OpportunitySidebar'
import { parseCompositeId } from './opportunity-detail/utils'

export default function OpportunityDetail() {
  const { id } = useParams()
  // The route param may contain encoded colons from the URL
  const decodedId = decodeURIComponent(id)
  const { addToast } = useToast()
  const [copied, setCopied] = useState(false)
  const { data: glassBoxPlaceholderPack } = usePublicJson('/curation/glass-box-cases.v1.json', {})

  const composite = parseCompositeId(decodedId)
  const isLive = !!composite

  // Both hooks are always called (React hooks rule), but only one fires a request
  const { data: liveOpp, loading: liveLoading, error: liveError } = useLiveOpportunity(
    composite?.setup_id, composite?.symbol, composite?.tf
  )
  const { data: pgOpp, loading: pgLoading, error: pgError } = useOpportunity(
    isLive ? null : decodedId
  )

  const opp = isLive ? liveOpp : pgOpp
  const loading = isLive ? liveLoading : pgLoading
  const error = isLive ? liveError : pgError
  const liveLineage = opp?.sim_trace?.kline_lineage || null
  const liveWalkthrough = Array.isArray(liveLineage?.phases) ? liveLineage.phases : []
  const placeholderWalkthrough = Array.isArray(glassBoxPlaceholderPack?.public_walkthrough?.timeline)
    ? glassBoxPlaceholderPack.public_walkthrough.timeline
    : []
  const walkthroughSteps = liveWalkthrough.length > 0 ? liveWalkthrough : placeholderWalkthrough
  const walkthroughSourceLabel = liveWalkthrough.length > 0 ? 'Live feed' : 'Authored preview set'
  const previewCases = Array.isArray(glassBoxPlaceholderPack?.badge_cases)
    ? glassBoxPlaceholderPack.badge_cases
    : []

  const handleCopy = async () => {
    if (!opp) return

    const plan = formatTradePlan({
      symbol: opp.symbol,
      timeframe: opp.timeframe,
      setup_id: opp.setup_id,
      direction: opp.direction,
      entry_price: opp.payload?.entry_price,
      stop_price: opp.payload?.stop_price,
      tp1_price: opp.payload?.tp1_price,
      tp2_price: opp.payload?.tp2_price,
      r_value: opp.payload?.r_value,
      score: opp.score,
      reason: opp.reason,
    })

    await copyToClipboard(plan)
    setCopied(true)
    addToast('Trade plan copied to clipboard', 'success')
    setTimeout(() => setCopied(false), 2000)
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton variant="title" className="w-48" />
        <Card>
          <CardContent className="space-y-4">
            <Skeleton />
            <Skeleton />
            <Skeleton className="w-2/3" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !opp) {
    return (
      <div className="py-12 text-center">
        <p className="text-terminal-400">Opportunity not found</p>
        <Link to="/app/opportunities" className="btn btn-secondary mt-4">
          <ArrowLeft className="size-4" />
          Back to Opportunities
        </Link>
      </div>
    )
  }

  const payload = opp.payload || {}
  const sinceTs = opp.since_ts || payload.since_ts || null
  const actionByTs = opp.action_by_ts || payload.action_by_ts || null

  return (
    <div className="space-y-6">
      <OpportunityDetailHeader
        copied={copied}
        isLive={isLive}
        onCopy={handleCopy}
        opportunity={opp}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <OpportunityTradeDetails opportunity={opp} payload={payload} />
        <OpportunitySidebar
          actionByTs={actionByTs}
          handleCopy={handleCopy}
          isLive={isLive}
          liveWalkthrough={liveWalkthrough}
          opportunity={opp}
          payload={payload}
          previewCases={previewCases}
          sinceTs={sinceTs}
          walkthroughSourceLabel={walkthroughSourceLabel}
          walkthroughSteps={walkthroughSteps}
          walkthroughSubheadline={glassBoxPlaceholderPack?.public_walkthrough?.subheadline}
        />
      </div>
    </div>
  )
}
