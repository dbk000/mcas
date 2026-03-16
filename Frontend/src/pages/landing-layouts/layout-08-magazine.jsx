import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards,
  WinnersPreview, HeroMetric,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutMagazine() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-08-magazine" className="space-y-8">
      {/* Large hero text spanning full width */}
      <section className="space-y-4">
        <HeroHeadline className="lg:text-7xl" />
        <HeroCopy />
      </section>

      {/* 3-column grid */}
      <div className="grid gap-4 lg:grid-cols-3">
        <MetricsGrid proofPack={proofPack} />
        <PerformanceStrip proofPack={proofPack} />
        <GlassBoxReplay proofPack={proofPack} />
      </div>

      <EvidenceCards proofPack={proofPack} />

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />

      {/* Sticky CTA bar at bottom of viewport */}
      <div className="sticky bottom-0 z-30 border-t border-terminal-800 bg-terminal-950/95 px-4 py-3 backdrop-blur-sm">
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
      </div>
    </div>
  )
}
