import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards,
  WinnersPreview, HeroMetric,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutCardWall() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-06-card-wall" className="space-y-8">
      {/* Hero left + Glass Box right */}
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <section className="space-y-4">
          <HeroPills />
          <HeroHeadline />
          <HeroCopy />
          <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
        </section>
        <GlassBoxReplay proofPack={proofPack} />
      </div>

      {/* MetricsGrid + PerformanceStrip side by side */}
      <div className="grid gap-4 lg:grid-cols-2">
        <MetricsGrid proofPack={proofPack} />
        <PerformanceStrip proofPack={proofPack} />
      </div>

      <EvidenceCards proofPack={proofPack} />

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
