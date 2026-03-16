import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  HeroMetric, MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards, WinnersPreview,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutSplit6040() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  const heroMetrics = proofPack?.hero_metrics || []

  return (
    <div data-operator-surface="layout-03-split-60-40" className="space-y-8">
      {/* 60/40 split */}
      <div className="grid gap-6 lg:grid-cols-5">
        {/* Left column — 60% */}
        <section className="space-y-4 lg:col-span-3">
          <HeroPills />
          <HeroHeadline />
          <HeroCopy />
          <ProofBadges proofPack={proofPack} />
          <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
          <Disclaimer />
        </section>

        {/* Right column — 40%, metrics stacked vertically */}
        <aside className="space-y-3 lg:col-span-2">
          {heroMetrics.map((metric) => (
            <HeroMetric key={metric.id} metric={metric} />
          ))}
          <PerformanceStrip proofPack={proofPack} />
        </aside>
      </div>

      {/* Below fold */}
      <div className="grid gap-4 lg:grid-cols-2">
        <GlassBoxReplay proofPack={proofPack} />
        <EvidenceCards proofPack={proofPack} />
      </div>

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
