import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards, WinnersPreview,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutCentered() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-05-centered" className="space-y-8">
      <section className="mx-auto flex max-w-4xl flex-col items-center space-y-4 text-center">
        <HeroPills />
        <HeroHeadline className="max-w-4xl text-center" />
        <HeroCopy />
        <MetricsTicker proofPack={proofPack} />
        <ProofBadges proofPack={proofPack} />
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
        <Disclaimer />
      </section>

      <PerformanceStrip proofPack={proofPack} />

      <GlassBoxReplay proofPack={proofPack} />

      <EvidenceCards proofPack={proofPack} />

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
