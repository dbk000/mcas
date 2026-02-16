#!/usr/bin/env python3
"""
MAE/MFE Calibration Analysis (Phase 4)
=======================================

Reads trades.csv + features.csv from a simulation run profiler output,
computes MAE/MFE statistics, and produces a calibration report answering
5 key questions about trade mechanics.

Usage:
    python Backend/scripts/mae_mfe_analysis.py \
      --data-dir analysis/sim_runs/<RUN_ID>/data/ \
      --outdir analysis/sim_runs/<RUN_ID>/

Outputs:
    <outdir>/mae_mfe_report.md

Questions answered:
    Q1: Are stops too tight?
    Q2: Is FAIL_FAST too aggressive?
    Q3: Are targets too conservative?
    Q4: Is entry timing the problem?
    Q5: What would optimal stops look like?
"""

import os
import sys
import argparse
import logging
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    import pandas as pd
    import numpy as np
except ImportError:
    logger.error("pandas and numpy are required. Install with: pip install pandas numpy")
    sys.exit(1)


def _pct(count, total):
    if not total:
        return "0.0%"
    return f"{100 * count / total:.1f}%"


def _fmt(val, decimals=4):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"{val:.{decimals}f}"


def _percentile_str(series, p):
    val = series.quantile(p / 100)
    return _fmt(val, 4)


def load_data(data_dir):
    """Load and merge trades.csv + features.csv."""
    trades_path = os.path.join(data_dir, "trades.csv")
    features_path = os.path.join(data_dir, "features.csv")

    if not os.path.exists(trades_path):
        logger.error(f"trades.csv not found at {trades_path}")
        sys.exit(1)
    if not os.path.exists(features_path):
        logger.error(f"features.csv not found at {features_path}")
        sys.exit(1)

    trades = pd.read_csv(trades_path)
    features = pd.read_csv(features_path)

    logger.info(f"Loaded {len(trades)} trades, {len(features)} feature rows")

    # Merge on id
    df = trades.merge(features, on="id", how="left")

    # Filter to valid trades
    df = df[df["data_available"].astype(str).str.lower().isin(["true", "t", "1"])]
    df = df[df["pnl_net"].notna()]

    logger.info(f"Valid trades after filtering: {len(df)}")

    return df


def _has_mae_mfe(df):
    """Check if MAE/MFE columns exist and have data."""
    required = ["mae_mfe.mae_r", "mae_mfe.mfe_r"]
    for col in required:
        if col not in df.columns:
            return False
    return df["mae_mfe.mae_r"].notna().sum() > 0


def q1_stops_too_tight(df, lines):
    """Q1: Are stops too tight?"""
    lines.append("## Q1: Are Stops Too Tight?\n")

    winners = df[df["pnl_net"] > 0].copy()
    losers = df[df["pnl_net"] <= 0].copy()
    sl_trades = df[df["exit_reason"] == "STOP_LOSS"].copy()

    n_total = len(df)
    n_winners = len(winners)
    n_losers = len(losers)

    lines.append(f"**Sample:** {n_total} trades ({n_winners} winners, {n_losers} losers)\n")

    # Winner MAE distribution
    if n_winners > 0 and "mae_mfe.mae_r" in winners.columns:
        w_mae = winners["mae_mfe.mae_r"].dropna()
        if len(w_mae) > 0:
            lines.append("### Winner MAE Distribution (how far winners dip against you)\n")
            lines.append("| Metric | Value |")
            lines.append("|:-------|------:|")
            for p in [10, 25, 50, 75, 90]:
                lines.append(f"| P{p} | {_fmt(w_mae.quantile(p/100), 4)} R |")
            lines.append(f"| Mean | {_fmt(w_mae.mean(), 4)} R |")
            lines.append(f"| Min (worst) | {_fmt(w_mae.min(), 4)} R |")
            lines.append("")

            # % of winners with deep MAE
            for threshold in [-0.5, -0.75, -0.9]:
                count = (w_mae <= threshold).sum()
                lines.append(f"- Winners with MAE worse than {threshold}R: {count} ({_pct(count, len(w_mae))})")
            lines.append("")

    # "Near miss" analysis: STOP_LOSS trades with MFE > 1R
    if len(sl_trades) > 0 and "mae_mfe.mfe_r" in sl_trades.columns:
        sl_mfe = sl_trades["mae_mfe.mfe_r"].dropna()
        if len(sl_mfe) > 0:
            near_miss = (sl_mfe >= 1.0).sum()
            near_miss_half = (sl_mfe >= 0.5).sum()
            lines.append("### Near-Miss Analysis (STOP_LOSS trades that had profit potential)\n")
            lines.append(f"- STOP_LOSS trades: {len(sl_trades)}")
            lines.append(f"- STOP_LOSS with MFE >= 1.0R (would have hit TP1): **{near_miss}** ({_pct(near_miss, len(sl_mfe))})")
            lines.append(f"- STOP_LOSS with MFE >= 0.5R (had meaningful favorable move): **{near_miss_half}** ({_pct(near_miss_half, len(sl_mfe))})")
            lines.append("")

            # MFE distribution for stopped-out trades
            lines.append("### STOP_LOSS MFE Distribution\n")
            lines.append("| Metric | Value |")
            lines.append("|:-------|------:|")
            for p in [10, 25, 50, 75, 90]:
                lines.append(f"| P{p} | {_fmt(sl_mfe.quantile(p/100), 4)} R |")
            lines.append(f"| Mean | {_fmt(sl_mfe.mean(), 4)} R |")
            lines.append(f"| Max | {_fmt(sl_mfe.max(), 4)} R |")
            lines.append("")

    # Breakdown by setup x timeframe
    if "mae_mfe.mae_r" in winners.columns and len(winners) > 0:
        lines.append("### Winner MAE by Setup x Timeframe\n")
        lines.append("| Setup | Timeframe | N | Median MAE | P25 MAE | P75 MAE |")
        lines.append("|:------|:----------|--:|-----------:|--------:|--------:|")
        for (setup, tf), grp in winners.groupby(["setup_id", "timeframe"]):
            mae = grp["mae_mfe.mae_r"].dropna()
            if len(mae) >= 5:
                lines.append(
                    f"| {setup} | {tf} | {len(mae)} "
                    f"| {_fmt(mae.median(), 4)} | {_fmt(mae.quantile(0.25), 4)} "
                    f"| {_fmt(mae.quantile(0.75), 4)} |"
                )
        lines.append("")


def q2_fail_fast_too_aggressive(df, lines):
    """Q2: Is FAIL_FAST too aggressive?"""
    lines.append("## Q2: Is FAIL_FAST Too Aggressive?\n")

    ff_trades = df[df["exit_reason"] == "FAIL_FAST"].copy()
    n_ff = len(ff_trades)

    if n_ff == 0:
        lines.append("No FAIL_FAST exits in this run.\n")
        return

    lines.append(f"**FAIL_FAST trades:** {n_ff} ({_pct(n_ff, len(df))} of all trades)\n")

    if "mae_mfe.mfe_r" not in ff_trades.columns:
        lines.append("No MFE data available for FAIL_FAST trades.\n")
        return

    ff_mfe = ff_trades["mae_mfe.mfe_r"].dropna()
    if len(ff_mfe) == 0:
        lines.append("No MFE data available for FAIL_FAST trades.\n")
        return

    # MFE distribution of FAIL_FAST exits
    lines.append("### FAIL_FAST MFE Distribution\n")
    lines.append("| Metric | Value |")
    lines.append("|:-------|------:|")
    for p in [10, 25, 50, 75, 90]:
        lines.append(f"| P{p} | {_fmt(ff_mfe.quantile(p/100), 4)} R |")
    lines.append(f"| Mean | {_fmt(ff_mfe.mean(), 4)} R |")
    lines.append(f"| Max | {_fmt(ff_mfe.max(), 4)} R |")
    lines.append("")

    # Would-have-won analysis
    # Estimate TP1 distance — for most setups TP1 is ~1R above entry
    would_tp1 = (ff_mfe >= 1.0).sum()
    had_movement = (ff_mfe >= 0.5).sum()
    no_movement = (ff_mfe < 0.1).sum()

    lines.append("### FAIL_FAST Opportunity Cost\n")
    lines.append(f"- MFE >= 1.0R (would have hit ~TP1): **{would_tp1}** ({_pct(would_tp1, len(ff_mfe))})")
    lines.append(f"- MFE >= 0.5R (meaningful favorable move): **{had_movement}** ({_pct(had_movement, len(ff_mfe))})")
    lines.append(f"- MFE < 0.1R (correct to exit — no favorable move): **{no_movement}** ({_pct(no_movement, len(ff_mfe))})")
    lines.append("")

    # Compare FAIL_FAST MFE vs winner MAE
    winners = df[df["pnl_net"] > 0]
    if "mae_mfe.mae_r" in winners.columns and len(winners) > 0:
        w_mae = winners["mae_mfe.mae_r"].dropna()
        if len(w_mae) > 0:
            lines.append("### FAIL_FAST Threshold vs Winner MAE\n")
            lines.append(f"- Median winner MAE: {_fmt(w_mae.median(), 4)}R")
            lines.append(f"- Median FAIL_FAST MFE: {_fmt(ff_mfe.median(), 4)}R")
            lines.append(f"- If FAIL_FAST threshold were widened to median winner MAE ({_fmt(w_mae.median(), 4)}R), "
                         f"some FAIL_FAST exits might have become winners.")
            lines.append("")

    # Breakdown by setup
    lines.append("### FAIL_FAST by Setup\n")
    lines.append("| Setup | N | Median MFE | % with MFE >= 0.5R |")
    lines.append("|:------|--:|-----------:|-------------------:|")
    for setup, grp in ff_trades.groupby("setup_id"):
        mfe = grp["mae_mfe.mfe_r"].dropna()
        if len(mfe) > 0:
            pct_half = _pct((mfe >= 0.5).sum(), len(mfe))
            lines.append(f"| {setup} | {len(mfe)} | {_fmt(mfe.median(), 4)} | {pct_half} |")
    lines.append("")


def q3_targets_too_conservative(df, lines):
    """Q3: Are targets too conservative?"""
    lines.append("## Q3: Are Targets Too Conservative?\n")

    n_total = len(df)

    if "mae_mfe.mfe_r" not in df.columns:
        lines.append("No MFE data available.\n")
        return

    mfe = df["mae_mfe.mfe_r"].dropna()
    if len(mfe) == 0:
        lines.append("No MFE data available.\n")
        return

    # MFE distribution for all trades
    lines.append("### MFE Distribution (all trades)\n")
    lines.append("| Metric | Value |")
    lines.append("|:-------|------:|")
    for p in [10, 25, 50, 75, 90, 95]:
        lines.append(f"| P{p} | {_fmt(mfe.quantile(p/100), 4)} R |")
    lines.append(f"| Mean | {_fmt(mfe.mean(), 4)} R |")
    lines.append(f"| Max | {_fmt(mfe.max(), 4)} R |")
    lines.append("")

    # MFE buckets
    lines.append("### MFE Buckets\n")
    lines.append("| MFE Range | Count | % |")
    lines.append("|:----------|------:|--:|")
    buckets = [
        ("< 0.25R", mfe < 0.25),
        ("0.25R - 0.5R", (mfe >= 0.25) & (mfe < 0.5)),
        ("0.5R - 1.0R", (mfe >= 0.5) & (mfe < 1.0)),
        ("1.0R - 1.5R", (mfe >= 1.0) & (mfe < 1.5)),
        ("1.5R - 2.0R", (mfe >= 1.5) & (mfe < 2.0)),
        ("2.0R - 3.0R", (mfe >= 2.0) & (mfe < 3.0)),
        (">= 3.0R", mfe >= 3.0),
    ]
    for label, mask in buckets:
        cnt = mask.sum()
        lines.append(f"| {label} | {cnt} | {_pct(cnt, len(mfe))} |")
    lines.append("")

    # TP1 exits: additional MFE beyond exit
    tp1_trades = df[df["exit_reason"] == "TP1"].copy()
    if len(tp1_trades) > 0 and "mae_mfe.mfe_r" in tp1_trades.columns:
        tp1_mfe = tp1_trades["mae_mfe.mfe_r"].dropna()
        tp1_r_mult = tp1_trades["r_multiple"].dropna()
        if len(tp1_mfe) > 0 and len(tp1_r_mult) > 0:
            # "Left on table" = MFE - actual R achieved
            left_on_table = tp1_mfe.values[:len(tp1_r_mult)] - tp1_r_mult.values[:len(tp1_mfe)]
            lot_series = pd.Series(left_on_table).dropna()

            lines.append("### TP1 Exits: Profit Left on Table\n")
            lines.append(f"- TP1 trades: {len(tp1_trades)}")
            if len(lot_series) > 0:
                lines.append(f"- Median additional MFE beyond TP1 exit: {_fmt(lot_series.median(), 4)}R")
                lines.append(f"- Mean additional MFE beyond TP1 exit: {_fmt(lot_series.mean(), 4)}R")
                lines.append(f"- P90 additional MFE beyond TP1 exit: {_fmt(lot_series.quantile(0.90), 4)}R")
            lines.append("")

    # Losers with high MFE (trades that were winners before reversing)
    losers = df[df["pnl_net"] <= 0].copy()
    if len(losers) > 0 and "mae_mfe.mfe_r" in losers.columns:
        loser_mfe = losers["mae_mfe.mfe_r"].dropna()
        if len(loser_mfe) > 0:
            loser_1r = (loser_mfe >= 1.0).sum()
            loser_half = (loser_mfe >= 0.5).sum()
            lines.append("### Losers That Were Once Winners\n")
            lines.append(f"- Losers total: {len(losers)}")
            lines.append(f"- Losers with MFE >= 1.0R before reversing: **{loser_1r}** ({_pct(loser_1r, len(loser_mfe))})")
            lines.append(f"- Losers with MFE >= 0.5R before reversing: **{loser_half}** ({_pct(loser_half, len(loser_mfe))})")
            lines.append("")

            if loser_1r > 0:
                high_mfe_losers = losers[losers["mae_mfe.mfe_r"] >= 1.0]
                lines.append("### Losers with MFE >= 1R — Detail\n")
                lines.append("| Symbol | TF | Setup | Dir | MFE R | Final R | Exit Reason | Bars |")
                lines.append("|:-------|:---|:------|:----|------:|--------:|:------------|-----:|")
                for _, t in high_mfe_losers.head(15).iterrows():
                    lines.append(
                        f"| {t.get('symbol', '?')} | {t.get('timeframe', '?')} "
                        f"| {t.get('setup_id', '?')} | {t.get('direction', '?')} "
                        f"| {_fmt(t.get('mae_mfe.mfe_r'), 2)} | {_fmt(t.get('r_multiple'), 2)} "
                        f"| {t.get('exit_reason', '?')} | {t.get('bars_held', '?')} |"
                    )
                lines.append("")


def q4_entry_timing(df, lines):
    """Q4: Is entry timing the problem?"""
    lines.append("## Q4: Is Entry Timing the Problem?\n")

    if "mae_mfe.mae_bar" not in df.columns or "mae_mfe.bars_to_mae" not in df.columns:
        lines.append("No MAE bar timing data available.\n")
        return

    bars_to_mae = df["mae_mfe.bars_to_mae"].dropna()
    if len(bars_to_mae) == 0:
        lines.append("No MAE bar timing data available.\n")
        return

    # bars_to_mae distribution
    lines.append("### Bars to MAE Distribution (when does the worst drawdown occur?)\n")
    lines.append("| Metric | Value |")
    lines.append("|:-------|------:|")
    for p in [10, 25, 50, 75, 90]:
        lines.append(f"| P{p} | {_fmt(bars_to_mae.quantile(p/100), 1)} bars |")
    lines.append(f"| Mean | {_fmt(bars_to_mae.mean(), 1)} bars |")
    lines.append("")

    # Instant-death analysis
    instant_death = df[df["bars_held"] <= 2].copy()
    survivors = df[df["bars_held"] > 2].copy()

    if len(instant_death) > 0 and len(survivors) > 0:
        id_mae = instant_death["mae_mfe.mae_r"].dropna()
        sv_mae = survivors["mae_mfe.mae_r"].dropna()

        lines.append("### Instant-Death vs Survivors MAE Comparison\n")
        lines.append(f"- Instant-death trades (bars_held <= 2): {len(instant_death)}")
        lines.append(f"- Survivor trades (bars_held > 2): {len(survivors)}")
        if len(id_mae) > 0 and len(sv_mae) > 0:
            lines.append(f"- Median MAE (instant-death): {_fmt(id_mae.median(), 4)}R")
            lines.append(f"- Median MAE (survivors): {_fmt(sv_mae.median(), 4)}R")
            lines.append("")

    # Bar-0 adverse movement analysis
    bar0_mae = df[df["mae_mfe.mae_bar"] == 0].copy()
    if len(bar0_mae) > 0:
        bar0_mae_r = bar0_mae["mae_mfe.mae_r"].dropna()
        lines.append("### Bar-0 Adverse Movement (entry bar is the worst point)\n")
        lines.append(f"- Trades where MAE occurs on bar 0: {len(bar0_mae)} ({_pct(len(bar0_mae), len(df))})")
        if len(bar0_mae_r) > 0:
            lines.append(f"- Median bar-0 MAE: {_fmt(bar0_mae_r.median(), 4)}R")
            lines.append(f"- P90 bar-0 MAE: {_fmt(bar0_mae_r.quantile(0.10), 4)}R")  # 10th percentile since MAE is negative
        lines.append("")

    # MFE timing
    if "mae_mfe.bars_to_mfe" in df.columns:
        bars_to_mfe = df["mae_mfe.bars_to_mfe"].dropna()
        if len(bars_to_mfe) > 0:
            lines.append("### Bars to MFE Distribution (when does the best point occur?)\n")
            lines.append("| Metric | Value |")
            lines.append("|:-------|------:|")
            for p in [10, 25, 50, 75, 90]:
                lines.append(f"| P{p} | {_fmt(bars_to_mfe.quantile(p/100), 1)} bars |")
            lines.append(f"| Mean | {_fmt(bars_to_mfe.mean(), 1)} bars |")
            lines.append("")


def q5_optimal_stops(df, lines):
    """Q5: What would optimal stops look like?"""
    lines.append("## Q5: What Would Optimal Stops Look Like?\n")

    winners = df[df["pnl_net"] > 0].copy()
    losers = df[df["pnl_net"] <= 0].copy()

    if "mae_mfe.mae_r" not in winners.columns or len(winners) == 0:
        lines.append("Insufficient data for optimal stop analysis.\n")
        return

    w_mae = winners["mae_mfe.mae_r"].dropna()
    l_mae = losers["mae_mfe.mae_r"].dropna() if len(losers) > 0 else pd.Series(dtype=float)

    if len(w_mae) == 0:
        lines.append("No winner MAE data available.\n")
        return

    # Winner MAE percentiles by setup
    lines.append("### Winner MAE Percentiles by Setup\n")
    lines.append("| Setup | N | P10 | P25 | P50 | P75 | P90 |")
    lines.append("|:------|--:|----:|----:|----:|----:|----:|")
    for setup, grp in winners.groupby("setup_id"):
        mae = grp["mae_mfe.mae_r"].dropna()
        if len(mae) >= 5:
            lines.append(
                f"| {setup} | {len(mae)} "
                f"| {_fmt(mae.quantile(0.10), 3)} "
                f"| {_fmt(mae.quantile(0.25), 3)} "
                f"| {_fmt(mae.quantile(0.50), 3)} "
                f"| {_fmt(mae.quantile(0.75), 3)} "
                f"| {_fmt(mae.quantile(0.90), 3)} |"
            )

    # Overall
    lines.append(
        f"| **ALL** | {len(w_mae)} "
        f"| {_fmt(w_mae.quantile(0.10), 3)} "
        f"| {_fmt(w_mae.quantile(0.25), 3)} "
        f"| {_fmt(w_mae.quantile(0.50), 3)} "
        f"| {_fmt(w_mae.quantile(0.75), 3)} "
        f"| {_fmt(w_mae.quantile(0.90), 3)} |"
    )
    lines.append("")

    # Trade-off table: if stop = Nth percentile of winner MAE
    lines.append("### Stop Calibration Trade-Off Table\n")
    lines.append("If stop were set at the Nth percentile of winner MAE, "
                 "what % of winners and losers would survive?\n")
    lines.append("| Stop Level (R) | Winner MAE Pctile | % Winners Survive | % Losers Survive | Net Trades Kept |")
    lines.append("|---------------:|------------------:|------------------:|-----------------:|----------------:|")

    for pctile in [5, 10, 15, 20, 25, 30, 40, 50]:
        stop_r = w_mae.quantile(pctile / 100)
        # Winners surviving: those whose MAE is above (less negative than) this threshold
        w_survive = (w_mae >= stop_r).sum()
        w_pct = w_survive / len(w_mae) * 100 if len(w_mae) > 0 else 0

        # Losers surviving: those whose MAE is above this threshold (they don't get stopped)
        if len(l_mae) > 0:
            l_survive = (l_mae >= stop_r).sum()
            l_pct = l_survive / len(l_mae) * 100
        else:
            l_survive = 0
            l_pct = 0

        net_kept = w_survive + l_survive
        lines.append(
            f"| {_fmt(stop_r, 3)} | P{pctile} "
            f"| {w_survive}/{len(w_mae)} ({w_pct:.1f}%) "
            f"| {l_survive}/{len(l_mae)} ({l_pct:.1f}%) "
            f"| {net_kept} |"
        )
    lines.append("")

    # Per-setup recommendations
    lines.append("### Per-Setup Recommendations\n")
    for setup, grp in df.groupby("setup_id"):
        s_winners = grp[grp["pnl_net"] > 0]
        s_losers = grp[grp["pnl_net"] <= 0]
        s_w_mae = s_winners["mae_mfe.mae_r"].dropna() if len(s_winners) > 0 else pd.Series(dtype=float)
        s_l_mae = s_losers["mae_mfe.mae_r"].dropna() if len(s_losers) > 0 else pd.Series(dtype=float)

        if len(s_w_mae) < 10:
            continue

        current_pnl = grp["pnl_net"].sum()
        current_wr = (grp["pnl_net"] > 0).sum() / len(grp) * 100

        lines.append(f"**{setup}** (N={len(grp)}, WR={current_wr:.1f}%, PnL=${current_pnl:.2f})\n")
        lines.append(f"- Winner P25 MAE: {_fmt(s_w_mae.quantile(0.25), 3)}R — "
                     f"setting stop here would keep 75% of winners")
        lines.append(f"- Winner P10 MAE: {_fmt(s_w_mae.quantile(0.10), 3)}R — "
                     f"setting stop here would keep 90% of winners")

        # Estimate: tighter stop loses some losers earlier but also loses some winners
        p10_stop = s_w_mae.quantile(0.10)
        p25_stop = s_w_mae.quantile(0.25)

        if len(s_l_mae) > 0:
            l_saved_p25 = (s_l_mae < p25_stop).sum()
            l_saved_p10 = (s_l_mae < p10_stop).sum()
            lines.append(f"- Losers stopped earlier at P25 ({_fmt(p25_stop, 3)}R): "
                         f"{l_saved_p25} of {len(s_l_mae)} ({_pct(l_saved_p25, len(s_l_mae))})")
            lines.append(f"- Losers stopped earlier at P10 ({_fmt(p10_stop, 3)}R): "
                         f"{l_saved_p10} of {len(s_l_mae)} ({_pct(l_saved_p10, len(s_l_mae))})")
        lines.append("")


def generate_summary(df, lines):
    """Generate summary table mapping findings to parameters."""
    lines.append("## Summary: Findings → Parameters → Expected Impact\n")
    lines.append("| # | Finding | Parameter | Suggested Action | Expected Impact |")
    lines.append("|--:|:--------|:----------|:-----------------|:----------------|")

    findings = []

    # Check stop tightness
    if "mae_mfe.mae_r" in df.columns:
        winners = df[df["pnl_net"] > 0]
        w_mae = winners["mae_mfe.mae_r"].dropna()
        if len(w_mae) > 0:
            p25 = w_mae.quantile(0.25)
            if p25 > -0.5:
                findings.append(
                    ("Winners have shallow MAE",
                     "stop_price distance",
                     f"Consider tightening stops — winner P25 MAE is only {_fmt(p25, 3)}R",
                     "Reduced loser damage, minor winner loss")
                )
            else:
                findings.append(
                    ("Winners have deep MAE",
                     "stop_price distance",
                     f"Stops may need widening — winner P25 MAE is {_fmt(p25, 3)}R",
                     "More winners survive, but losers run longer")
                )

    # Check FAIL_FAST
    ff = df[df["exit_reason"] == "FAIL_FAST"]
    if len(ff) > 0 and "mae_mfe.mfe_r" in ff.columns:
        ff_mfe = ff["mae_mfe.mfe_r"].dropna()
        if len(ff_mfe) > 0:
            ff_would_win = (ff_mfe >= 1.0).sum()
            if ff_would_win / len(ff_mfe) > 0.1:
                findings.append(
                    (f"{_pct(ff_would_win, len(ff_mfe))} of FAIL_FAST had MFE >= 1R",
                     "FAIL_FAST threshold",
                     "FAIL_FAST may be too aggressive",
                     f"Recovering {ff_would_win} trades could improve PnL")
                )
            else:
                findings.append(
                    (f"Only {_pct(ff_would_win, len(ff_mfe))} of FAIL_FAST had MFE >= 1R",
                     "FAIL_FAST threshold",
                     "FAIL_FAST is well-calibrated",
                     "No change needed")
                )

    # Check target conservatism
    losers = df[df["pnl_net"] <= 0]
    if len(losers) > 0 and "mae_mfe.mfe_r" in losers.columns:
        l_mfe = losers["mae_mfe.mfe_r"].dropna()
        if len(l_mfe) > 0:
            losers_1r = (l_mfe >= 1.0).sum()
            if losers_1r / len(l_mfe) > 0.05:
                findings.append(
                    (f"{_pct(losers_1r, len(l_mfe))} of losers had MFE >= 1R",
                     "TP1/TP2 levels",
                     "Some losers visited profitable territory — targets may be missing the move",
                     "Earlier TP1 could capture these")
                )

    # Check entry timing
    if "mae_mfe.mae_bar" in df.columns:
        bar0_count = (df["mae_mfe.mae_bar"] == 0).sum()
        if bar0_count / len(df) > 0.3:
            findings.append(
                (f"{_pct(bar0_count, len(df))} of trades hit MAE on entry bar",
                 "Entry fill model / timing",
                 "High bar-0 MAE suggests poor entry timing",
                 "Limit orders or delayed entry could help")
            )

    if not findings:
        findings.append(("Insufficient MAE/MFE data", "—", "Re-run simulation with MAE/MFE tracking", "—"))

    for i, (finding, param, action, impact) in enumerate(findings, 1):
        lines.append(f"| {i} | {finding} | {param} | {action} | {impact} |")

    lines.append("")


def main():
    parser = argparse.ArgumentParser(
        description="MAE/MFE Calibration Analysis (Phase 4)",
    )
    parser.add_argument(
        "--data-dir", required=True,
        help="Directory containing trades.csv and features.csv",
    )
    parser.add_argument(
        "--outdir", required=True,
        help="Output directory for mae_mfe_report.md",
    )
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    df = load_data(args.data_dir)

    if not _has_mae_mfe(df):
        logger.error(
            "No MAE/MFE data found in features.csv. "
            "Re-run the simulation with MAE/MFE tracking enabled, "
            "then run the profiler to extract features.csv."
        )
        sys.exit(1)

    logger.info(f"MAE/MFE data found for {df['mae_mfe.mae_r'].notna().sum()} trades")

    lines = []
    lines.append("# MAE/MFE Calibration Report\n")
    lines.append(f"**Trades analyzed:** {len(df)}")
    lines.append(f"**Winners:** {(df['pnl_net'] > 0).sum()} ({_pct((df['pnl_net'] > 0).sum(), len(df))})")
    lines.append(f"**Losers:** {(df['pnl_net'] <= 0).sum()} ({_pct((df['pnl_net'] <= 0).sum(), len(df))})")
    lines.append(f"**Total PnL:** ${df['pnl_net'].sum():.2f}")
    lines.append("")

    q1_stops_too_tight(df, lines)
    q2_fail_fast_too_aggressive(df, lines)
    q3_targets_too_conservative(df, lines)
    q4_entry_timing(df, lines)
    q5_optimal_stops(df, lines)
    generate_summary(df, lines)

    report_path = os.path.join(args.outdir, "mae_mfe_report.md")
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    logger.info(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
