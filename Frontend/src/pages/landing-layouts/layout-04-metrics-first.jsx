import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards, WinnersPreview,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutMetricsFirst() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-04-metrics-first" className="space-y-8">
      {/* Market data banner at the very top */}
      <MetricsTicker proofPack={proofPack} />

      {/* Hero content */}
      <section className="space-y-4">
        <HeroPills />
        <HeroHeadline />
        <HeroCopy />
        <ProofBadges proofPack={proofPack} />
      </section>

      {/* Performance strip between headline area and CTAs */}
      <PerformanceStrip proofPack={proofPack} />

      <section className="space-y-4">
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
        <Disclaimer />
      </section>

      {/* Glass Box + Evidence */}
      <div className="grid gap-4 lg:grid-cols-2">
        <GlassBoxReplay proofPack={proofPack} />
        <EvidenceCards proofPack={proofPack} />
      </div>

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
