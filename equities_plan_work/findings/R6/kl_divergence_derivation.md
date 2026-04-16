# KL Divergence Derivation — R6

## Formulation

Discrete form:

```
KL(P || Q) = Σ_i P(i) * (log P(i) - log Q(i))
```

Natural log. P = rolling window distribution (today's observed). Q = baseline distribution (training-time).

## Why discrete (not continuous)

`score_title_tone` returns floats in [-1.0, +1.0], but the output is **not** a smooth continuous distribution in practice:

1. A large mass sits on exactly 0.0 (60-90% of titles per `tone_scorer_summary.md`).
2. Non-zero scores snap to a small set of values determined by category weights: ±0.8, ±0.9, ±1.0, or a sum when multiple categories fire (saturating at ±1.0 via clamp).
3. The observed distribution is therefore **discrete by construction** — a histogram is the right representation.

A continuous-density KL (e.g. KDE-based) would over-smooth the zero spike and under-estimate the drift signal. Discrete histogram KL over the natural bin structure preserves the signal shape.

## Binning choice

- **21 bins** of width 0.1 across [-1.05, 1.05].
- **Central bin** (-0.05, +0.05] captures the zero mass.
- **Tails** at ±1.0 capture saturated multi-category hits.
- Bin edges are `np.linspace(-1.05, 1.05, 22)` — 22 edges = 21 bins.

## Laplace smoothing

- **α = 1 per bin**, added to observed counts before normalisation.
- Justification: numerical floor against `log(0)` when the rolling window happens to miss a bin the baseline populated. With N_BINS=21 and typical rolling-window sample sizes of a few hundred to a few thousand, α=1 shifts mass by ≤ 21 / (21 + N), which is <5% for N ≥ 420 events and < 1% for N ≥ 2100 events — small enough not to distort the KL shape, large enough to eliminate log-of-zero failure modes entirely.

## Trigger logic

- Threshold = `DRIFT_TRIGGER_MULTIPLIER × baseline_kl`, where `DRIFT_TRIGGER_MULTIPLIER = 2.0` per review §2.6.
- `baseline_kl` floor = `MIN_TRIGGER_ABS_KL = ln(2)/100 ≈ 0.00693 nats`. Prevents the degenerate case where `baseline_kl = 0.0` (pooled training with no held-out chunks) would produce a threshold of 0 and fire on any noise.
- **5 consecutive** daily KL values all strictly above threshold → trigger fires. A mixed history (any day under threshold in the tail) does not trigger.
- `CONSECUTIVE_DAYS_TO_TRIGGER = 5` per review §2.6.

## Alternatives considered and rejected

1. **Jensen-Shannon divergence (symmetric KL).** JS is bounded in [0, log 2] and symmetric, which is appealing, but the review specifies KL explicitly, and the practical interpretation "probability the baseline would produce today's distribution" maps more cleanly onto KL than JS for this monitoring use-case.
2. **Earth-mover / Wasserstein distance.** EMD handles shifts along the score axis more gracefully than histogram KL (e.g. a uniform +0.1 shift is EMD-linear but KL-explosive). Rejected because tone scores are not quasi-continuous — there is no meaningful "distance" between bins, only polarity-category structure. KL on discrete bins is the right tool for this shape.
3. **Chi-squared test.** Produces a p-value, not a drift metric. Review wants a threshold-based trigger rule, not a significance test. KL is a drift magnitude in nats.
4. **Top-k bin mass ratio.** Simpler but loses information about the shape of the rest of the distribution. KL uses every bin.

## Determinism

No RNGs inside the monitor. The pytest suite uses `numpy.random.default_rng(<seed>)` deterministically. Every call to `compute_kl_divergence` is a pure function of its two inputs.
