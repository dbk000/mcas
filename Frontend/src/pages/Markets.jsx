import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Search, BarChart3, Filter } from 'lucide-react'
import { useMarkets } from '../hooks/useData'
import { 
  Card, 
  CardContent, 
  Badge, 
  Skeleton,
  EmptyState,
  Input,
  Select
} from '../components/ui'

export default function Markets() {
  const { data, loading } = useMarkets()
  const [search, setSearch] = useState('')
  const [tierFilter, setTierFilter] = useState('all')

  const markets = useMemo(() => {
    if (!data?.markets) return []
    
    let filtered = data.markets
    
    // Search filter
    if (search) {
      const s = search.toLowerCase()
      filtered = filtered.filter(m => 
        m.symbol.toLowerCase().includes(s) ||
        m.base.toLowerCase().includes(s)
      )
    }
    
    // Tier filter
    if (tierFilter !== 'all') {
      filtered = filtered.filter(m => m.liquidity_tier === parseInt(tierFilter))
    }
    
    return filtered
  }, [data?.markets, search, tierFilter])

  // Group by first letter for better organization
  const groupedMarkets = useMemo(() => {
    const groups = {}
    markets.forEach(m => {
      const letter = m.base[0].toUpperCase()
      if (!groups[letter]) groups[letter] = []
      groups[letter].push(m)
    })
    return groups
  }, [markets])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Markets</h1>
          <p className="text-sm text-terminal-400 mt-1">
            {data?.count || 0} trading pairs available
          </p>
        </div>
      </div>

      {/* Search and filters */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-terminal-400" />
            <Input
              type="text"
              placeholder="Search symbols..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9 w-full"
            />
          </div>
          
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-terminal-400" />
            <Select
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value)}
              options={[
                { value: 'all', label: 'All Tiers' },
                { value: '1', label: 'Tier 1 (Majors)' },
                { value: '2', label: 'Tier 2+' },
              ]}
              className="w-40"
            />
          </div>

          <div className="text-sm text-terminal-400">
            {markets.length} results
          </div>
        </CardContent>
      </Card>

      {/* Markets grid */}
      {loading ? (
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4 lg:grid-cols-6">
          {Array(24).fill(0).map((_, i) => (
            <Card key={i}>
              <CardContent className="py-3">
                <Skeleton className="h-6 w-20 mb-1" />
                <Skeleton className="h-4 w-12" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : markets.length === 0 ? (
        <EmptyState
          icon={BarChart3}
          title="No markets found"
          description="Try adjusting your search or filters"
        />
      ) : (
        <div className="space-y-6">
          {Object.entries(groupedMarkets).sort().map(([letter, letterMarkets]) => (
            <div key={letter}>
              <h3 className="text-sm font-medium text-terminal-400 mb-3 sticky top-0 bg-terminal-900 py-2">
                {letter}
              </h3>
              <div className="grid gap-3 grid-cols-2 md:grid-cols-4 lg:grid-cols-6">
                {letterMarkets.map((market) => (
                  <MarketCard key={market.symbol} market={market} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function MarketCard({ market }) {
  return (
    <Link to={`/markets/${market.symbol}`}>
      <Card hover>
        <CardContent className="py-3">
          <div className="flex items-center justify-between">
            <span className="font-mono font-medium text-terminal-100">
              {market.base}
            </span>
            {market.liquidity_tier === 1 && (
              <Badge variant="warning" className="text-[10px]">Major</Badge>
            )}
          </div>
          <p className="text-xs text-terminal-400 mt-1">{market.symbol}</p>
        </CardContent>
      </Card>
    </Link>
  )
}
