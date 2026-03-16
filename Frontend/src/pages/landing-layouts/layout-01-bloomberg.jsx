import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards, WinnersPreview,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutBloomberg() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-01-bloomberg" className="space-y-8">
      {/* Full-width headline block */}
      <section className="space-y-4">
        <HeroPills />
        <HeroHeadline />
        <HeroCopy />
        <ProofBadges proofPack={proofPack} />
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
        <Disclaimer />
      </section>

      {/* Horizontal ticker strip */}
      <MetricsTicker proofPack={proofPack} />

      <PerformanceStrip proofPack={proofPack} />

      {/* Full-width Glass Box */}
      <GlassBoxReplay proofPack={proofPack} />

      <EvidenceCards proofPack={proofPack} />

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
