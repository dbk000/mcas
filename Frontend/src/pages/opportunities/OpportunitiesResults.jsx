import { Target } from 'lucide-react'
import {
  Card,
  CardContent,
  EmptyState,
  Skeleton,
} from '../../components/ui'
import OpportunityCard from './OpportunityCard'

export default function OpportunitiesResults({
  loading,
  hasData,
  opportunities,
  filters,
  selectedOppSet,
  onCopyTradePlan,
  onOpenCurve,
  onToggleSelection,
}) {
  if (loading && !hasData) {
    return (
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {[1, 2, 3, 4, 5, 6].map((index) => (
          <Card key={index}>
            <CardContent className="space-y-3">
              <Skeleton variant="title" />
              <Skeleton />
              <Skeleton className="w-2/3" />
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  if (opportunities.length === 0) {
    return (
      <EmptyState
        icon={Target}
        title="No opportunities found"
        description="Try adjusting your filters or run a new scan"
      />
    )
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {opportunities.map((opportunity) => (
        <OpportunityCard
          key={opportunity.id || `${opportunity.setup_id}:${opportunity.symbol}:${opportunity.timeframe}`}
          opportunity={opportunity}
          onCopy={() => onCopyTradePlan(opportunity)}
          onCurve={() => onOpenCurve(opportunity)}
          showCurve={filters.status === 'expired'}
          showOutcome={filters.status === 'expired'}
          isSelectable={filters.status === 'expired'}
          isSelected={selectedOppSet.has(String(opportunity.id))}
          onToggleSelect={() => onToggleSelection(opportunity.id)}
        />
      ))}
    </div>
  )
}
