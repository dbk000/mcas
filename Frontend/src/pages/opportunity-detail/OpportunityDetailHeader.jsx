import { ArrowLeft, BarChart3, Check, Copy } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  Badge,
  DirectionBadge,
} from '../../components/ui'
import GlassBoxBadge from '../../components/opportunities/GlassBoxBadge'

export default function OpportunityDetailHeader({
  copied,
  isLive,
  onCopy,
  opportunity,
}) {
  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div className="flex items-start gap-4">
        <Link to="/app/opportunities" className="btn btn-ghost btn-sm mt-0.5">
          <ArrowLeft className="size-4" />
        </Link>
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-mono text-2xl font-bold text-terminal-100">{opportunity.symbol}</h1>
            <Badge>{opportunity.timeframe}</Badge>
            <Badge variant="info">{opportunity.setup_id}</Badge>
            <DirectionBadge direction={opportunity.direction || opportunity.decision} />
            <GlassBoxBadge opportunity={opportunity} />
          </div>
          <p className="mt-1 text-sm text-terminal-400">
            {isLive ? 'Live Signal' : `Opportunity #${opportunity.id}`}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 lg:justify-end">
        <Link
          to={`/app/markets/${opportunity.symbol}`}
          className="btn btn-secondary btn-sm"
        >
          <BarChart3 className="size-4" />
          View Chart
        </Link>
        <button onClick={onCopy} className="btn btn-primary btn-sm">
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
          Copy Trade Plan
        </button>
      </div>
    </div>
  )
}
