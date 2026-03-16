import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards, WinnersPreview,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutHeroFull() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-02-hero-full" className="space-y-8">
      {/* Hero spans full width, no max-w constraint */}
      <section className="space-y-4">
        <HeroPills />
        <HeroHeadline className="max-w-none" />
        <HeroCopy />
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
      </section>

      {/* Compact horizontal metrics bar */}
      <MetricsTicker proofPack={proofPack} />

      <PerformanceStrip proofPack={proofPack} />

      {/* Glass Box + Evidence side by side */}
      <div className="grid gap-4 lg:grid-cols-2">
        <GlassBoxReplay proofPack={proofPack} />
        <EvidenceCards proofPack={proofPack} />
      </div>

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
