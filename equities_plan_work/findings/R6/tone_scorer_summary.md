# tone_scorer Summary (R6 input)

Source file: `Backend/governance/equities/tone_scorer.py` (213 lines).
Read 2026-04-16 for R6 design work.

## Public API

```python
def score_title_tone(
    title: str,
    source: str = "",
    anti_bot_score: float = 0.0,
) -> float
```

Returns a float in **[-1.0, +1.0]**. Clamped via `max(-1.0, min(1.0, total))` at line 213.

## Filters

- `source != 'OPEN_NEWS'` → 0.0
- `anti_bot_score > 0.5` → 0.0
- `len(title.split()) < 3` → 0.0
- Module initialisation failure → 0.0

## Category definitions

Four categories, each with weight × polarity:

| Category | Weight | Direction | Example patterns |
|---|---|---|---|
| ANALYST | 1.0 | +/-1 | "price target raised", "buy rating", "upgrades", "downgrades" |
| EARNINGS | 0.9 | +/-1 | "blowout quarter", "earnings beat", "misses estimates" |
| GUIDANCE | 0.9 | +/-1 | "raises outlook", "lowers guidance", "below consensus" |
| INSIDER | 0.8 | +/-1 | "buyback announced", "dividend increase", "insider buying" |

Scoring: each category contributes ≤ one match (first match wins). Negation
check: if a token in `{'not','no','never',"don't","doesn't",'without','unlikely','fails','despite','but','although','however'}` appears in the 3 tokens preceding the match, polarity is inverted.

## Score range characterisation

Minimum possible: -3.6 (all 4 categories match bearish) → clamped to -1.0.
Maximum possible: +3.6 (all 4 match bullish) → clamped to +1.0.
Common values observed (empirical, from 24h sample of equities_events feeding
the governance/equities/features.py atc_matrix):

- ~60-90% titles: **exactly 0.0** (non-OPEN_NEWS, anti-bot, or no keyword match)
- Single-category matches land at ±0.8, ±0.9, or ±1.0 (after clamp)
- Multi-category matches saturate at ±1.0

## Implications for R6 drift monitor

1. **The zero bin dominates.** A robust drift monitor must not be dominated
   by zero-mass drift (e.g. fewer OPEN_NEWS events one week). The 21-bin
   histogram design explicitly captures the zero mass in a central bin
   centred on 0 (bin width 0.1), and KL divergence over the full 21-bin
   support lets the zero-mass shift register as drift when it happens.
2. **Score range is [-1.0, +1.0] (bounded).** Bin edges fixed at
   `np.linspace(-1.05, 1.05, 22)` are safe — no out-of-range scores.
3. **Keyword set is frozen at import time.** A silent edit to the regex
   dictionary would change the distribution, which is exactly what the
   drift monitor is designed to catch. The monitor's `baseline_kl` floor
   (MIN_TRIGGER_ABS_KL) prevents false triggers but would eventually
   fire on a substantial set revision.
