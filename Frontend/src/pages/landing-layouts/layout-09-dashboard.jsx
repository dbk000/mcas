import {
  HeroPills, HeroHeadline, HeroCopy, ProofBadges, HeroCTAs, Disclaimer,
  MetricsGrid, MetricsTicker, PerformanceStrip, GlassBoxReplay, EvidenceCards,
  WinnersPreview, HeroMetric,
} from './shared'
import useLandingData from './useLandingData'

export default function LayoutDashboard() {
  const { proofPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready } = useLandingData()
  if (!ready) return null

  return (
    <div data-operator-surface="layout-09-dashboard" className="space-y-8">
      {/* Hero left + fake dashboard right */}
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <section className="space-y-4">
          <HeroPills />
          <HeroHeadline />
          <HeroCopy />
          <HeroCTAs primaryHref={primaryHref} primaryLabel={primaryLabel} isPaid={isPaid} tier={tier} />
        </section>

        {/* Fake app window */}
        <div className="overflow-hidden rounded-2xl border border-terminal-800 bg-terminal-900/85 shadow-lg">
          {/* Title bar with three dots */}
          <div className="flex items-center gap-1.5 border-b border-terminal-800 bg-terminal-950/80 px-3 py-2">
            <span className="size-2.5 rounded-full bg-red-400/70" />
            <span className="size-2.5 rounded-full bg-amber-400/70" />
            <span className="size-2.5 rounded-full bg-emerald-400/70" />
            <span className="ml-2 text-[10px] text-terminal-500">MCAS Dashboard</span>
          </div>
          <div className="space-y-4 p-4">
            <MetricsGrid proofPack={proofPack} />
            <PerformanceStrip proofPack={proofPack} />
          </div>
        </div>
      </div>

      <GlassBoxReplay proofPack={proofPack} />

      <EvidenceCards proofPack={proofPack} />

      <WinnersPreview previewStrategies={previewStrategies} tier={tier} />
    </div>
  )
}
