# On-Chain Data Source Assessment — MCAS

## Universe: 453 spot symbols on Binance, 370 with futures overlap

---

## Budget Allocation (EUR 150/month)

| Source | Monthly Cost | Coverage | Priority | Allocated |
|--------|-------------|----------|----------|-----------|
| Binance Futures API | Free | 370 symbols | DONE | EUR 0 |
| Binance Spot (additional) | Free | 453 symbols | P1 | EUR 0 |
| DefiLlama | Free | ~120 protocols, 7099 total | P1 | EUR 0 |
| CoinGecko Free Tier | Free | All 453 (18,880 total) | P1 | EUR 0 |
| Etherscan + BSCScan | Free (API key) | ~200 ERC-20, ~100 BEP-20 | P2 | EUR 0 |
| Coinglass Professional | ~EUR 72/mo ($79) | Top 200 symbols, 1yr history | P1 | EUR 72 |
| Santiment Pro (SanAPI) | ~EUR 45/mo ($49) | 2500+ assets, 5yr history | P1 | EUR 45 |
| **Total** | | | | **EUR 117** |
| **Buffer** | | | | **EUR 33** |

Budget leaves EUR 33 buffer for plan upgrades. The $128/mo total = ~EUR 117 at current rates.

---

## Source Details

### 1. Binance Futures API (FREE) — ALREADY DONE

| Property | Value |
|----------|-------|
| Coverage | 370/453 symbols (81.7%) |
| Data types | Funding rates, open interest, long/short ratios, taker buy/sell |
| Granularity | Funding: 4-8h, OI/LS/taker: 1h |
| History | Funding: full (years), OI/LS/taker: 30 days |
| Rate limit | 2400 weight/min (separate from spot) |
| Auth | None for market data |
| Status | Collector built, backfill done, features materialized |

### 2. DefiLlama (FREE)

| Property | Value |
|----------|-------|
| Coverage | ~200 of our tokens have protocol TVL data; stablecoin data covers all stablecoins in our universe |
| Data types | Protocol TVL, DEX volumes (by chain + token), stablecoin supply/flows, yields |
| Granularity | Daily (TVL), hourly (some DEX volume endpoints) |
| History | Full history back to protocol launch (years for most) |
| Rate limit | No hard limit, be respectful (~5 req/s recommended) |
| Auth | None |
| API | REST, JSON. Base: `https://api.llama.fi` |
| Key endpoints | `/protocol/{name}` (TVL history), `/stablecoins` (supply), `/overview/dexs/{chain}` (DEX volume) |
| Value for ignition | TVL changes signal capital flows into/out of protocols. Stablecoin supply on exchanges = dry powder for buying. DEX volume divergence from CEX = smart money positioning. |

### 3. CoinGecko Free Tier (FREE)

| Property | Value |
|----------|-------|
| Coverage | All 453 symbols (CoinGecko has every listed token) |
| Data types | Market cap, circulating supply, total supply, community data (twitter/telegram followers), developer data (github commits/stars), exchange tickers |
| Granularity | Daily for most metrics, 5-min for price (but we have OHLCV) |
| History | Up to 365 days on free tier (daily), some metrics less |
| Rate limit | 10-30 calls/min (free tier). Pro: 500/min |
| Auth | Optional API key for higher limits (free key available) |
| API | REST. Base: `https://api.coingecko.com/api/v3` |
| Key endpoints | `/coins/{id}` (full data), `/coins/{id}/market_chart` (history), `/coins/{id}/tickers` (exchange data) |
| Value for ignition | Circulating supply changes (token unlocks proxy), social following growth as leading indicator, market cap rank changes for regime detection. |

### 4. Etherscan / BSCScan (FREE with API key)

| Property | Value |
|----------|-------|
| Coverage | ~150 ERC-20 tokens + ~80 BEP-20 tokens in our universe |
| Data types | Token holder count, top holders, token transfers, contract interactions |
| Granularity | Per-block (but we aggregate to daily/hourly) |
| History | Full blockchain history |
| Rate limit | 5 calls/sec (free tier), 10/sec (paid) |
| Auth | Free API key (registration required) |
| API | REST. `https://api.etherscan.io/api`, `https://api.bscscan.com/api` |
| Key endpoints | `/api?module=token&action=tokenholdercount` (deprecated on some chains, use tokenholderlist), `/api?module=account&action=tokentx` (transfers) |
| Limitation | Holder count endpoint is limited. Better to track whale wallets via transfer events. Rate limit makes bulk collection slow for 230 tokens. |
| Value for ignition | Unique holder growth = retail interest accumulation. Whale concentration changes = smart money positioning. Large transfer spikes = imminent volatility. |

### 5. Coinglass API (~EUR 45/month)

| Property | Value |
|----------|-------|
| Coverage | All Binance futures symbols (~370 of our universe) |
| Data types | **Extended OI history** (beyond Binance 30d limit), **liquidation data** (long/short liquidations by symbol), funding rate aggregated across exchanges, OI by exchange, order book depth |
| Granularity | 1h for most, 5m for liquidations |
| History | 365+ days for OI and liquidations (this is the key advantage over raw Binance API) |
| Rate limit | Depends on plan. Open API: 30 req/min. Pro: 120/min |
| Auth | API key (registration + subscription) |
| API | REST. Base: `https://open-api.coinglass.com` |
| Key endpoints | `/public/v2/perpetual/funding_rate_ohlc_history`, `/public/v2/perpetual/open_interest_ohlc_history`, `/public/v2/perpetual/liquidation_history` |
| Value for ignition | **Critical**: liquidation cascades are the mechanism behind many ignition events. Long liquidation clusters = forced selling exhaustion = setup for reversal ignition. Extended OI history enables proper 365d walk-forward of OI features that Binance's 30d limit prevented. |

### 6. Santiment SanAPI (~EUR 55/month Basic plan)

| Property | Value |
|----------|-------|
| Coverage | 300+ altcoins with on-chain data (excellent overlap with our universe) |
| Data types | **Exchange flow** (inflow/outflow by exchange), **whale transactions** (>$100K moves), **holder distribution** (by balance tier), **active addresses**, **network growth**, **social volume** (mentions across crypto social media), **development activity** (github proxy), **MVRV ratio**, **NVT ratio**, age consumed, dormant circulation |
| Granularity | Hourly for most on-chain metrics, 5-min for social, daily for some |
| History | 2-5 years for most metrics depending on when token was listed |
| Rate limit | Basic: 300 API calls/min, 10K/day |
| Auth | API key (registration + subscription) |
| API | GraphQL (SanAPI). Base: `https://api.santiment.net/graphql` |
| Key metrics | `exchange_inflow`, `exchange_outflow`, `whale_transaction_count`, `amount_in_top_holders`, `active_addresses_24h`, `social_volume_total`, `dev_activity` |
| Value for ignition | **Highest expected alpha**: Exchange outflows during compression = whales moving to cold storage = accumulation signal. Whale transaction spikes preceding compression breaks. Holder distribution shifts from retail to whale = smart money loading. Social volume inflection points as catalyst detection. |

---

## Sources Evaluated But Not Recommended (for now)

| Source | Why Not |
|--------|---------|
| **Glassnode** | Excellent for BTC/ETH, very limited altcoin coverage. Our universe is mostly altcoins. Revisit if we add BTC-specific strategies. ~$39-99/mo. |
| **CryptoQuant** | Good exchange flow data but Santiment covers the same ground with better altcoin breadth. ~$49-99/mo. Could replace Santiment if SanAPI disappoints. |
| **Nansen** | Smart money wallet labeling. Premium pricing (>$150/mo for API). Exceeds budget alone. |
| **Arkham Intelligence** | Entity tracking/wallet labels. API is invite-only and expensive. |
| **LunarCrush** | Social metrics. Santiment covers social volume already. Free tier very limited. Could add later from buffer budget. |
| **Dune Analytics** | Powerful custom queries but requires building/maintaining SQL queries per metric. Better for one-off research than continuous collection. Free tier: 2500 executions/mo. |
| **The Graph** | DEX subgraphs. Requires GraphQL and per-chain indexers. DefiLlama aggregates this already. |
| **Token Unlocks** | Useful data but only affects ~50 tokens in our universe with significant unlocks. Manual/RSS tracking is fine. Not worth API cost. |

---

## Priority Ordering (Build Sequence)

### Phase 1: Free Sources (immediate — no API key costs)
1. **DefiLlama** — TVL, stablecoin supply, DEX volumes
2. **CoinGecko** — Market cap, circulating supply, social stats
3. **Etherscan/BSCScan** — Holder counts for ERC-20/BEP-20 tokens

### Phase 2: Paid Sources (after Phase 1 validates pipeline)
4. **Coinglass** — Extended OI, liquidation data (sign up, get API key)
5. **Santiment** — Exchange flows, whale txns, holder distribution, social volume (sign up for Basic plan)

### Phase 3: Feature Integration
6. Materialize all raw data into `onchain_features_1h`
7. Run IC screening against ignition targets (same framework as derivatives_signal_test.py)
8. Walk-forward validate survivors

---

## Feature Hypotheses for Ignition Detection

### Tier 1 — Highest expected predictive value

| Feature | Source | Hypothesis | Mechanism |
|---------|--------|-----------|-----------|
| `exchange_outflow_zscore` | Santiment | Large exchange outflows during compression = accumulation | Whales withdraw to cold storage before anticipated move up |
| `liquidation_long_cascade` | Coinglass | Cluster of long liquidations in prior 24h = exhausted sellers | Forced selling clears weak longs, reduces overhead supply |
| `whale_tx_count_spike` | Santiment | Spike in >$100K transactions during low-vol period = positioning | Smart money moves large size when volatility is cheap |
| `oi_extended_buildup` | Coinglass | OI steadily rising over 7-30d while price flat = coiled spring | Leveraged traders building positions anticipating breakout |
| `holder_whale_accumulation` | Santiment | Top holders increasing share during compression = strong hands loading | Distribution shifting from retail to whales precedes moves |

### Tier 2 — Moderate expected value

| Feature | Source | Hypothesis | Mechanism |
|---------|--------|-----------|-----------|
| `tvl_change_pct_7d` | DefiLlama | TVL growth = capital confidence in protocol | Rising TVL signals utility/demand beyond speculation |
| `stablecoin_exchange_inflow` | DefiLlama/Santiment | Stablecoin deposits to exchanges = dry powder | Buying pressure waiting to deploy |
| `social_volume_acceleration` | Santiment | Social mentions accelerating from low base = early catalyst | Narrative forming before price discovers it |
| `circulating_supply_change` | CoinGecko | Supply inflation/deflation rate affects price dynamics | Token unlocks create sell pressure; burns reduce supply |
| `dex_cex_volume_ratio` | DefiLlama | DEX volume rising vs CEX = smart money trading | Informed traders prefer DEX for size and privacy |
| `active_addresses_growth` | Santiment | Network usage growing during price compression = divergence | Real adoption continuing despite flat price = undervalued |
| `unique_holders_growth_rate` | Etherscan | Holder count growing during flat price = distribution broadening | More holders = more potential buyers on breakout |

### Tier 3 — Market regime indicators (not symbol-specific)

| Feature | Source | Hypothesis | Mechanism |
|---------|--------|-----------|-----------|
| `btc_exchange_netflow` | Santiment | BTC net exchange flow as market regime proxy | Large BTC outflows = bullish regime for altcoins |
| `total_stablecoin_supply` | DefiLlama | Total stablecoin mcap change = market-level liquidity | Growing stablecoin supply = more capital available |
| `total_crypto_tvl_change` | DefiLlama | Aggregate TVL change as DeFi activity proxy | Rising total TVL = risk-on environment |

---

## Architecture Decision: Option C (Hybrid)

**Raw tables** per source type for auditability, backfill, and source-specific schema.
**Materialized feature table** `onchain_features_1h` aligned to the OHLCV hourly grid for research speed.

This matches the derivatives pipeline pattern exactly (raw tables → `derivatives_features_1h`).
