import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards,
  WinnersPreview, HeroMetric,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutMinimal() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-07-minimal" className="space-y-8">
      {/* Above the fold: just headline + one-liner + CTA */}
      <section className="space-y-4">
        <HeroPills />
        <HeroHeadline />
        <HeroCopy />
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
        <Disclaimer />
      </section>

      {/* Divider */}
      <div className="border-t border-terminal-800" />

      {/* Below the fold */}
      <MetricsGrid proofPack={proofPack} />

      <PerformanceStrip proofPack={proofPack} />

      <GlassBoxReplay proofPack={proofPack} />

      <EvidenceCards proofPack={proofPack} />

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
