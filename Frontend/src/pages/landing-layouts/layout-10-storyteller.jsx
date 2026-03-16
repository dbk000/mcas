import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards,
  WinnersPreview, HeroMetric,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutStoryteller() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  const metrics = proofPack?.hero_metrics || []

  return (
    <div data-operator-surface="layout-10-storyteller" className="flex flex-col gap-16">
      {/* Section 1: Hero centered */}
      <section className="space-y-4 text-center">
        <div className="flex justify-center">
          <HeroPills />
        </div>
        <HeroHeadline />
      </section>

      {/* Section 2: Metrics one by one vertically */}
      <section className="flex flex-col items-center gap-4">
        {metrics.map((metric) => (
          <div key={metric.id} className="w-full max-w-md">
            <HeroMetric metric={metric} />
          </div>
        ))}
      </section>

      {/* Section 3: Performance strip */}
      <section>
        <PerformanceStrip proofPack={proofPack} />
      </section>

      {/* Section 4: ProofBadges + Glass Box */}
      <section className="space-y-6">
        <ProofBadges proofPack={proofPack} />
        <GlassBoxReplay proofPack={proofPack} />
      </section>

      {/* Section 5: Evidence */}
      <section>
        <EvidenceCards proofPack={proofPack} />
      </section>

      {/* Section 6: CTAs centered */}
      <section className="flex justify-center">
        <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
      </section>

      {/* Section 7: Winners */}
      <section>
        <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
      </section>
    </div>
  )
}
