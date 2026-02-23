# Telegram Channel List — Verification Report & Integration Notes

## Summary

**25 verified channels** in the main list, **6 candidates** pending username verification.  
Every channel in the main list has been confirmed via at least one of: direct `t.me/` URL resolving, TGStat listing, or official exchange community page.

## Channel Selection Strategy

Your pipeline isn't a signal-following service — it's a **social mention volume detector** feeding into Brain 1/Brain 2. That means the selection criteria are different from "best trading signals" lists:

1. **Volume over quality** — You want high-throughput text streams where coins get mentioned. A noisy channel with 50 posts/day is more valuable for mention velocity than a curated channel with 2 posts/week.
2. **Structured data over prose** — Whale Alert and WhaleBot post machine-parseable formats (ticker, amount, exchange). Symbol matching will be near-perfect on these.
3. **Exchange announcements are the crown jewels** — Listing announcements are the single highest-signal catalyst for the ignition model. Binance/Bybit/OKX/KuCoin announcements give you ground truth for `catalyst_type: exchange_listing_rumor` before the market fully prices it in.
4. **Negative catalysts matter too** — ZachXBT and Rekt News give you hack/exploit/scam signals that map directly to Brain 2's `controversy_level` and `coordination_probability`.

## What I Deliberately Excluded

- **Paid signal groups** (CryptoNinjas, Fat Pig Signals, Crypto Inner Circle, etc.) — These are VIP/gated content. Even their free tiers are mostly self-promotional noise with no coin-specific mention signal.
- **Pump-and-dump groups** (Crypto Pump Club, DegenPump, etc.) — These would trigger Brain 2 constantly with coordinated shill content. Your `coordination_probability` score would be permanently high on every mention. Garbage in → garbage out.
- **Chat groups** (as opposed to channels) — Telethon can listen to both, but groups like @binanceexchange have massive spam/noise ratios. Channels are broadcast-only = cleaner data.
- **Non-English channels** — Your symbol_matcher and Brain 2 Haiku prompt are English-only. Adding Chinese/Korean channels would need a language detection gate first.

## Category Distribution

| Category | Count | Pipeline Value |
|---|---|---|
| Exchange Listings | 6 | **Critical** — direct catalyst detection |
| Whale Alerts / On-chain | 5 | **High** — pre-ignition volume signal |
| News | 3 | **Medium** — mention velocity driver |
| Security / Investigations | 3 | **Medium** — negative catalyst detection |
| Alpha / Narratives | 4 | **Medium** — narrative_category, cascade_stage |
| Analytics / Social | 2 | **Medium** — meta-signal, cross-validation |
| Technical / Other | 2 | **Low** — supplementary |

## Integration Notes for `telegram.py`

### Telethon Username Resolution

Telethon resolves usernames at connection time. If a username is wrong or the channel is private, you'll get a `UsernameNotOccupiedError` or `ChannelPrivateError`. The collector should:

```python
for channel_cfg in channels:
    try:
        entity = await client.get_entity(channel_cfg["username"])
        # Store the resolved channel ID for future reconnects
        channel_cfg["resolved_id"] = entity.id
        logger.info(f"Resolved {channel_cfg['username']} → {entity.id} ({entity.title})")
    except Exception as e:
        logger.warning(f"Failed to resolve {channel_cfg['username']}: {e}")
        # Don't crash — skip and continue
```

### Expected Message Volume

Based on observed posting frequencies:

| Channel | Posts/day (est.) |
|---|---|
| whale_alert_io | 200-500 (very high, mostly BTC/ETH/USDT) |
| WhaleBotAlerts | 100-300 |
| binance_announcements | 5-15 |
| cointelegraph | 20-40 |
| glassnode | 1-3 |
| lookonchain | 5-15 |
| investigations | 0-2 (sparse but high impact) |

**Total estimated: 400-1000 messages/day across all 25 channels.**

Most volume comes from whale alert channels. These are high-frequency but low symbol diversity (mostly BTC/ETH/USDT). The real ignition signal comes from the lower-volume channels where altcoins get specifically named.

### Symbol Matching Implications

- **Whale Alert format**: "1,973 #BTC (143,888,686 USD) transferred from #Kraken to unknown wallet" — The `#BTC` hashtag format means your cashtag matcher should also match `#TICKER` as Tier 1. Consider adding this to `symbol_matcher.py`.
- **Exchange announcements**: "Binance Will Add Zama (ZAMA) on Earn" — Ticker in parentheses. Your Tier 2 regex will catch `ZAMA`. The full name before it provides extra confirmation.
- **News channels**: Prose-heavy. Tier 4 (full name matching) carries more weight here. "Solana wants gasless transactions" → matches on "Solana".

### Priority Field Usage

The `priority` field in the config isn't just documentation — use it to weight mentions in Brain 1:

```python
# In brain1.py aggregation, weight by source priority
PRIORITY_WEIGHTS = {"high": 1.5, "medium": 1.0, "low": 0.5}
```

This is optional and probably premature for Phase 0, but worth noting for Phase 3 tuning.

## First-Week Pruning Protocol

After 7 days of collection:

```sql
-- Which channels are actually producing data?
SELECT channel, count(*) as posts, count(DISTINCT symbols) as unique_symbols
FROM social_posts 
WHERE source = 'telegram'
GROUP BY channel
ORDER BY posts DESC;
```

Channels with zero posts → check if Telethon resolved them. If resolved but zero posts, the channel may be inactive or posting non-text content (images/videos only).

Channels producing posts but zero symbol matches → check if the content is parseable. Some channels post images-only or use unicode characters that break regex matching.

## Candidates to Add After Validation

The 6 candidates in `_candidate_channels_to_evaluate` need manual verification:
1. Join each via Telegram app
2. Confirm it's active (posted within last 48h)
3. Confirm it's a channel (not a group)
4. Confirm it's public (Telethon can read without joining for public channels, but needs to join for some)
5. Note the exact username (case-sensitive)

The most important candidate to verify is **MEXC** — they list tokens earlier and more aggressively than other exchanges, making their announcements a potential leading indicator for Binance listings.
