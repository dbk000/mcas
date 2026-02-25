# EQ_ATC — LLM Opportunity Notes

Running notes on where LLM-based analysis could improve on keyword tone scoring.
Updated during implementation as observations surface.

---

## Keyword Scorer Limitations

The v2 keyword scorer (`tone_scorer.py`) uses ~35 compiled regex patterns across 4 categories. Known blind spots:

- **Implicit negation / sarcasm:** "Great, another miss from INTC" scores as positive (matches "great") despite sarcastic intent. Keyword matching has no pragmatic understanding.
- **Multi-ticker headlines:** "AAPL up 5% while MSFT down 3% after earnings" — both symbols share the same headline, but the tone is opposite for each. Current scorer applies the same score to both.
- **Clickbait vs real analysis:** "Why This Stock Could Be the Next 10-Bagger" and "Citi Upgrades AAPL to Buy with $250 PT" both score bullish, but have very different information quality.
- **Conditional/speculative language:** "If NVDA beats earnings, expect a rally" — scores bullish but is hypothetical, not factual.
- **Abbreviations and jargon:** Financial Twitter shorthand (e.g., "PT" without context, "$AAPL calls printing") may not match patterns designed for news headlines.

## SEC 8-K Content Analysis

91.9% of equities_events are SEC filings (source = SEC_EDGAR). The keyword scorer ignores these entirely because SEC form codes (424B2, 8-K, etc.) carry no directional tone in their titles.

An LLM could:
- Read 8-K filing content to extract material events (management changes, M&A, material contracts)
- Parse 10-Q/10-K risk factor changes between filings
- Identify unusual filing patterns (amended filings, late filings, NT 10-K)

This represents the largest untapped information source in the events table.

## Entity-Level Sentiment

Headlines often mention multiple entities. "Apple gains market share from Samsung in Q4" is bullish AAPL, bearish for Samsung (not in our universe, but illustrates the problem). An LLM could parse entity-level sentiment rather than headline-level.

## Social Post Quality

OPEN_SOCIAL is excluded in v2 due to 87% NVDA concentration. If social coverage broadens:
- LLM could distinguish genuine conviction from questions, jokes, and noise
- Could identify information cascades (same thesis repeated by many accounts vs genuine independent analysis)
- Could score poster credibility based on historical accuracy (requires tracking)

## Coverage Gap Statistics (at v2 launch)

- Total events in equities_events: ~34K
- OPEN_NEWS events: ~2,794 (8.1%)
- SEC_EDGAR events: ~31,600 (91.9%)
- OPEN_SOCIAL events: small, 87% NVDA
- Scorable events (OPEN_NEWS, ≥4 tokens, anti_bot ≤ 0.5): ~2,196
- Events with directional keywords: TBD (will be logged after first run)
- Average per-symbol per-day coverage: ~0.09 events

## Implementation Observations

*(To be updated as v2 runs in production)*
