import { usePublicJson } from '../../utils/publicJson'
import { useSubscription } from '../../context/SubscriptionContext'

export default function useLandingData() {
  const { isPaid, tier } = useSubscription()
  const { data: proofPack } = usePublicJson('/curation/proof-pack.v1.json')
  const { data: winnersPack } = usePublicJson('/curation/winners.v1.json')

  const primaryHref = isPaid ? '/app/strategies' : '/pricing?returnTo=%2Fapp%2Fstrategies'
  const primaryLabel = isPaid ? 'Open strategy dashboard' : 'See Pro Plans'
  const previewStrategies = (winnersPack?.strategies || []).slice(0, 2)
  const ready = Boolean(proofPack && winnersPack)

  return { proofPack, winnersPack, isPaid, tier, primaryHref, primaryLabel, previewStrategies, ready }
}
