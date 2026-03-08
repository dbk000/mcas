# Social Intelligence Magna Carta

## Context

Three independent analyses (Claude, Gemini, and your own reading of the raw tape) converged on the same finding: MCAS's social data is ~80% algorithmic noise, and the remaining ~20% human-authored content carries a qualitatively distinct psychological signature that separates D1 winners from losers. But the noise isn't noise — it's a structured signal that fingerprints pump-and-dump events.

This prompt covers four workstreams. Execute in order. Do not skip ahead — each workstream's findings shape the next.

---

## Workstream 1: Know Your Bots (Empirical Source Classification)

### 1.1 Source frequency census

Query `social_posts` (or equivalent table) and produce a census of every (source, channel) pair:

```sql
SELECT source, channel,
       COUNT(*) as total_posts,
       COUNT(DISTINCT symbol) as unique_symbols,
       MIN(post_ts) as earliest,
       MAX(post_ts) as latest,
       ROUND(AVG(engagement_score), 1) as avg_engagement
FROM social_posts  -- adapt table name
GROUP BY source, channel
ORDER BY total_posts DESC
LIMIT 50;
```

For each of the top 50, pull 5 random sample posts:

```sql
SELECT source, channel, content, engagement_score, post_ts
FROM social_posts
WHERE source = %s AND channel = %s
ORDER BY random()
LIMIT 5;
```

**Output:** `scripts/output/social_source_census_{ts}.json` with the full census + 5 samples per source.

**Purpose:** We need to see ALL sources before classifying any. The 40-opp sample we read manually only surfaced a few bot sources. There may be 20+ we haven't seen.

### 1.2 Template detection

For each source/channel in the top 50, compute a structural similarity score. A bot source produces templated content — the structure is identical, only the symbol/price/volume changes.

Approach:
1. Pull 100 random posts from each source/channel
2. For each post, extract a "skeleton" by replacing all numbers with `{NUM}`, all ticker symbols (4-5 uppercase letter sequences preceded by $ or standalone) with `{TICKER}`, and all URLs with `{URL}`
3. Count unique skeletons per source. If 100 posts produce < 5 unique skeletons, the source is templated (bot)
4. Classify each source as: `bot_templated` (< 5 skeletons), `bot_semi` (5-20 skeletons), `human_mixed` (20-50), `human_organic` (50+)

**Output:** Add `classification` and `unique_skeletons` and `sample_skeleton` fields to the census JSON.

### 1.3 Manual review checkpoint

**STOP.** Report the classified source census to big bro. Do not proceed until the classification is reviewed. We may need to reclassify some sources or split a source that has both bot and human content (e.g., a news channel that publishes both real journalism and sponsored Ozak AI spam).

---

## Workstream 2: Bots as Signal (Structured Pump Fingerprinting)

**Do not filter out bot posts. Parse them.**

### 2.1 Bot alert schema extraction

For every source classified as `bot_templated`, extract structured fields from the template. Most pump/dump bots follow a pattern like:

```
🚀{SYMBOL}/USDT is Pumping on {EXCHANGE}!📈
💸Price: {PRICE} USDT ({PCT_CHANGE}%)
📊24h Vol: {VOL_24H} USDT
💹Last Vol: {LAST_VOL} ({VOL_CHANGE}%)
⬆️Price: {PRICE_CHANGE} USDT
✅24h%: {PCT_24H}%
🧲OI: {OI} ({OI_CHANGE}%)
💰Funding: {FUNDING}%
```

Write a parser for each bot source that extracts these fields into structured columns. Store as a new table or materialised view:

```sql
CREATE TABLE IF NOT EXISTS social_bot_signals (
    id SERIAL PRIMARY KEY,
    source_post_id BIGINT REFERENCES social_posts(id),
    source TEXT NOT NULL,
    channel TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT,
    signal_type TEXT,  -- 'pump' or 'dump'
    price NUMERIC,
    pct_change NUMERIC,
    vol_24h NUMERIC,
    last_vol NUMERIC,
    vol_change_pct NUMERIC,
    oi NUMERIC,
    oi_change_pct NUMERIC,
    funding_rate NUMERIC,
    bid_vol NUMERIC,
    ask_vol NUMERIC,
    spread_pct NUMERIC,
    post_ts TIMESTAMPTZ NOT NULL,
    parsed_ts TIMESTAMPTZ DEFAULT now()
);
```

**Parse ALL historical bot posts** into this table. This is your structured bot signal time series.

### 2.2 Pump cluster detection

A "pump cluster" is multiple bot alerts for the same symbol within a short window. Define:

```python
CLUSTER_WINDOW_HOURS = 4      # alerts within 4h = same cluster
MIN_ALERTS_PER_CLUSTER = 3    # need at least 3 bot alerts to qualify
```

Group `social_bot_signals` where `signal_type = 'pump'` into clusters by (symbol, rolling window). For each cluster, compute:
- `cluster_start_ts`: first alert timestamp
- `cluster_end_ts`: last alert timestamp
- `alert_count`: number of bot alerts in the cluster
- `exchanges_seen`: unique exchanges (more exchanges = wider visibility)
- `max_pct_change`: peak percentage change reported
- `max_vol_change_pct`: peak volume change reported
- `funding_rate_at_peak`: funding rate from the alert with highest pct_change (negative funding during a pump = shorts getting squeezed = more explosive but less sustainable)

### 2.3 Cluster outcome classification

For each pump cluster, pull OHLCV for the symbol starting from `cluster_start_ts`:

- **Pre-pump price**: close of the bar immediately before `cluster_start_ts`
- **Peak price**: highest high in the 24h after cluster start
- **Post-pump price (24h)**: close 24 hours after cluster start
- **Post-pump price (48h)**: close 48 hours after cluster start
- **Post-pump price (72h)**: close 72 hours after cluster start

Classify each cluster:
- `sustained_breakout`: post-72h price > pre-pump price + 5% (it held)
- `pump_and_dump`: peak price > pre-pump + 10% AND post-48h price < pre-pump price (it dumped below starting point)
- `fade`: peak price > pre-pump + 5% AND post-48h price between pre-pump and peak (it faded but didn't fully dump)
- `noise`: peak price < pre-pump + 5% (the "pump" was nothing)

### 2.4 Statistical summary

Report:
- Total pump clusters identified
- Classification distribution (sustained / pump_and_dump / fade / noise)
- If pump_and_dump rate > 60%: this is a viable short signal
- Mean time from cluster_start to peak (how long does the pump phase last?)
- Mean time from peak to dump below pre-pump (how long until the dump completes?)
- Does funding_rate predict outcome? (negative funding during pump = squeeze = more likely to sustain? positive funding = crowded long = more likely to dump?)
- Does alert_count predict outcome? (more bot alerts = wider visibility = more retail inflow = bigger dump?)
- Does exchange_count predict outcome? (multi-exchange pumps vs single-exchange pumps)

### 2.5 Bot toxicity ratio (per-opportunity feature)

For every D1 opportunity in the backfill, compute a continuous toxicity score for the pre-trigger window:

```python
bot_toxicity_ratio = bot_mentions_in_window / total_mentions_in_window
```

Compute at two window sizes:
- **48h window:** `bot_toxicity_48h` — captures immediate pump cluster activity around the trigger
- **7d window:** `bot_toxicity_7d` — captures sustained bot attention vs organic discussion

Where `bot_mentions` = posts from any source classified as `bot_templated` or `bot_semi` in Workstream 1, and `total_mentions` = all posts including human-authored.

If total_mentions = 0 in the window, set toxicity_ratio = 0 (no data, not toxic).

This gives a continuous feature rather than binary cluster presence — a D1 opp with 90% bot chatter is qualitatively different from one with 20% bot chatter, even if both "have mentions."

### 2.6 D1 cross-reference

For every D1 opportunity in the 1535-opp backfill, compute:
1. Whether a pump cluster (from 2.2) occurred for that symbol within 6 hours before or after the D1 trigger
2. The `bot_toxicity_48h` ratio from 2.5
3. The `bot_toxicity_7d` ratio from 2.5

Split D1 outcomes three ways:
- **By cluster presence:** D1 opps WITH vs WITHOUT a concurrent pump cluster — what's the PF for each?
- **By toxicity quartile:** Bin D1 opps into quartiles of `bot_toxicity_48h`. Report PF per quartile. If PF degrades monotonically with increasing toxicity, the ratio is a continuous predictor, not just a binary flag.
- **Correlation with Early SL:** Specifically, what's the bot_toxicity_48h distribution for Early SL losers vs Winners? Run Mann-Whitney on this. If high toxicity correlates with Early SL, we have a direct rejection feature.

If the cluster-contaminated or high-toxicity D1 trades have materially worse PF, we have an automatic rejection filter: D1 triggers that coincide with bot pump clusters or exceed a toxicity threshold get suppressed. The continuous ratio is preferred over binary cluster detection because it catches partial contamination too.

**Output:** `scripts/output/pump_cluster_analysis_{ts}.json` — include cluster stats, toxicity quartile PFs, and Early SL correlation.

**CHECKPOINT:** Report pump cluster stats, toxicity quartile PFs, D1 cross-reference, and potential short signal viability to big bro.

---

## Workstream 3: The Human Tape (Filtered Quantitative Retest)

### 3.1 Re-run B1 analysis with bot sources excluded

Using the source classifications from Workstream 1, re-run `d1_social_silence_analysis.py` with a WHERE clause that excludes all `bot_templated` and `bot_semi` sources:

```sql
WHERE (source, channel) NOT IN (
    SELECT source, channel FROM social_source_classification
    WHERE classification IN ('bot_templated', 'bot_semi')
)
```

**Do not modify or delete any data in social_posts.** This is a query-time filter only.

Report the same metrics as the original B1 analysis:
- For N = 1, 3, 7, 14, 21, 30 day windows
- Split by Winners / Early SL / Late Exits
- Median mentions, mean mentions, pct_zero_mentions per group
- Mann-Whitney U test, p-values
- Separation in percentage points

### 3.2 Interpret results

Three possible outcomes:

**A) Statistical separation appears (p < 0.05, separation > 10pp):** The bot noise was drowning the signal. Human-only mention counts are predictive. Build a simple D1 gate based on human-only mention count. No LLM needed.

**B) Weak separation (p < 0.10, separation 5-10pp):** The signal exists but is too weak for a binary gate. Proceed to Workstream 4 (qualitative classification) to sharpen it.

**C) No separation (p > 0.10):** Human-only counts don't separate either. The signal is qualitative, not quantitative. The distinction is in WHAT humans are saying, not HOW MANY are saying it. Proceed to Workstream 4 as the primary path.

**CHECKPOINT:** Report filtered B1 results before proceeding.

---

## Workstream 4: Befriending the Authors (Qualitative Social Classification)

This workstream is CONTINGENT on Workstream 3 results. Only proceed if outcome B or C.

### 4.1 The taxonomy

Three independent analyses of the raw social tape (Claude, Gemini, and your own reading) converged on the same three-bucket taxonomy for human-authored social content:

**THE CASINO (reject signal):**
The crowd is speculating. The coin has a "story." People are paying attention for financial reasons.
- Price prediction articles ("SNX price prediction 2026-2032")
- Lottery ticket screenshots ("Turned $394 into $420K!")
- Governance drama creating narrative energy ("Neo co-founders' public dispute")
- Listing hype ("BREAKING: Coinbase announces surprise listing!")
- Influencer calls and trading signals
- Any content where the author's intent is to profit from or promote price movement

*The psychology: Exit liquidity is assembling. The breakout will be sold into.*

**THE GHOST (approve signal):**
The coin's ticker is being mentioned, but nobody is actually talking about the coin. Retail has forgotten it exists.
- Linguistic collisions: "gas fees" (not $GAS), "help me" (not $ME), "ATM withdrawal" (not $ATM)
- Exchange operational notices that list 50 coins and this one happens to be included
- Aggregator posts that list top gainers/losers and this coin appears in a long list
- Any content where removing the ticker symbol would not change the meaning of the post

*The psychology: The coin is so dead that its only social footprint is accidental. Maximum dormancy.*

**THE WORKSHOP (approve signal):**
Real humans are interacting with the protocol for utility, not speculation. The tone is clinical, bored, or frustrated — never excited.
- Technical support: "Unstaking AXL is impossible — how do I solve this?"
- Infrastructure complaints: "Cross-chain deposit mistake to HIVE address"
- Developer documentation, GitHub commit discussions, integration announcements
- Corporate/protocol financial reporting: "$338M in organic revenue"
- Clinical post-mortems: "XPL down 92% — supply in hands of real people = bearish"
- Any content where the author is using the protocol, not speculating on its price

*The psychology: The tourists and moon-boys are gone. Only builders and users remain. The coin is a tool, not a lottery ticket.*

### 4.2 Classification approach — two tiers

**Tier 1 (cheap, no LLM):** Rule-based pre-classification for obvious cases:
- Post from a `bot_templated` source → skip (already handled in Workstream 2)
- Post contains price prediction patterns ("price prediction 2026", "could rally Xx", "target $X by 202X") → CASINO
- Post is from a known aggregator AND the symbol appears in a list of 10+ tickers → GHOST
- Post content, when the symbol/ticker is removed, still makes complete grammatical sense → likely GHOST
- Post is from Reddit with flair "Support" or "Help" → likely WORKSHOP

**Tier 2 (LLM, for ambiguous posts that survive Tier 1):**

System prompt for Brain 2:

```
You are classifying a social media post about a cryptocurrency token.
Your job is to determine the INTENT of the author — not the content of the post.

Read the post empathetically. Imagine you are the person's friend and they just sent you this message. Ask yourself: WHY did they write this? What were they feeling?

Classify into exactly one of:

CASINO — The author is speculating on price, promoting the token, sharing profit/loss stories, reacting to price movement, or creating narrative excitement. They want the price to move. They have a financial motive for posting.

GHOST — The author is not actually talking about this specific token. The token's name or ticker appears incidentally — it's a common English word, it appears in a long list of tickers, or it's mentioned as part of a broader topic (like "gas fees" when the token is GAS). Removing the ticker from the post would not change its meaning.

WORKSHOP — The author is using, building on, or analytically discussing the protocol. They are troubleshooting, filing bug reports, discussing integrations, analysing fundamentals with no price target, or writing clinical post-mortems. Their tone is bored, frustrated, or clinical — never excited. They are interacting with the technology, not the price.

Respond with ONLY one word: CASINO, GHOST, or WORKSHOP.
```

### 4.3 Batch classification of historical data

Run the two-tier classifier on ALL human-authored posts (excluding bot sources) for the D1 backfill symbols in the 30-day lookback windows. Store the classification:

```sql
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS vibe_class TEXT;
-- Values: 'CASINO', 'GHOST', 'WORKSHOP', 'BOT', NULL (unclassified)
```

For bot sources (from Workstream 1): set `vibe_class = 'BOT'` in bulk.
For Tier 1 rule-based matches: set the appropriate class.
For Tier 2 (LLM): batch-classify using the Anthropic API. Use `claude-sonnet-4-20250514` for cost efficiency. Each post is a single classification call — should be fast.

### 4.4 Vibe-aware D1 analysis

Re-run the B1 analysis one more time, but instead of raw mention counts, use the vibe classification:

For each D1 opportunity in the honest subset, for N = 7, 14, 21, 30 day windows:
- `casino_count`: posts classified as CASINO
- `ghost_count`: posts classified as GHOST
- `workshop_count`: posts classified as WORKSHOP
- `casino_ratio`: casino_count / (casino + ghost + workshop) — what fraction of human conversation is speculative?
- `human_total`: casino + ghost + workshop (excluding bots)

Split by Winners / Early SL / Late Exits. Report distributions of `casino_ratio` per group.

**The hypothesis:** Early SL losers will have a higher `casino_ratio` (more speculative chatter relative to total human posts). Winners will have a lower `casino_ratio` (more ghosts and workshops, less casino).

If `casino_ratio` separates the groups, the D1 gate becomes:
```python
if casino_ratio > threshold:
    reject_trigger("Social vibe: CASINO dominates — speculative crowd present")
```

### 4.5 Cost estimation

Before running Tier 2 on the full historical dataset, estimate the cost:
- How many posts survive bot filtering and Tier 1 rule-based classification?
- Each Tier 2 LLM call is ~100 input tokens + 1 output token
- At Sonnet pricing, estimate total cost
- If > $50, report to big bro for approval before running

---

## Workstream 5: The Short Signal (Pump-and-Dump Strategy)

This is a SEPARATE strategy from D1. It runs in parallel, not as a filter.

### 5.1 Strategy thesis

When bot pump-alert clusters fire on a microcap, retail FOMO inflow creates temporary price elevation. Smart money (and the bots themselves) sell into this inflow. The price dumps within 24-48 hours.

**Entry:** Short the symbol N hours after a pump cluster is detected, once the initial momentum fades (price starts declining from peak, or a configurable delay after cluster_start).

**Exit:** Cover when price returns to pre-pump level, or time-stop at 48-72 hours.

**Stop:** Above the peak price + buffer (the pump was real, not a dump — take the loss).

### 5.2 Viability assessment (from Workstream 2 data)

Using the pump cluster analysis from Workstream 2.4, answer:
- What % of pump clusters resulted in pump_and_dump? (need > 60% for viable strategy)
- What's the median R-multiple if you short at peak+1h and cover at pre-pump level? 
- What's the worst-case R-multiple (the sustained breakouts you'd be wrong about)?
- Is there a filter (funding_rate, alert_count, exchange_count, time_of_day) that improves the hit rate?

### 5.3 Signal integration design

If the pump-and-dump rate is viable:
- Design a new setup: `PD1` (Pump-Dump Detection v1)
- Signal source: `social_bot_signals` cluster detection (real-time, as bot posts arrive)
- Direction: short
- Timeframe: 1h-4h (fast, not D1's 72h horizon)
- Integration point: `signal_worker.py` subscribes to social_posts stream, detects pump clusters in real time, generates PD1 opportunities

**Do not implement PD1 yet.** Just design the signal flow and report the viability metrics. This is a new strategy proposal, not a D1 filter.

---

## Execution Order

```
1. Workstream 1 (source census + template detection)
2. CHECKPOINT → big bro reviews source classifications
3. Workstream 2 (bot parsing + pump cluster analysis + D1 cross-reference)
4. Workstream 3 (filtered B1 retest with human-only data)
5. CHECKPOINT → big bro reviews pump stats + filtered B1 results
6. Workstream 4 (qualitative classification — ONLY if Workstream 3 shows outcome B or C)
7. CHECKPOINT → big bro reviews vibe classification results + cost estimate
8. Workstream 5 (pump-and-dump strategy viability — parallel with Workstream 4)
9. FINAL CHECKPOINT → big bro reviews all findings, decides on D1 gate and PD1 strategy
```

## Safety Rules

- **Never modify or delete social_posts data.** All filtering is at query time.
- **The `vibe_class` column is additive** — it classifies existing data, doesn't remove it.
- **Bot parsing creates a NEW table** (`social_bot_signals`), doesn't modify the source.
- **Every workstream has a checkpoint.** Do not proceed past a checkpoint without big bro sign-off.
- **Cost-estimate before any LLM batch job.** No surprise API bills.
- **The pump-and-dump strategy (Workstream 5) is analysis only.** Do not implement a new strategy without explicit approval.
