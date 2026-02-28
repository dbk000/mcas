#!/usr/bin/env python3
"""EQ-WS1: Equities Predictor Ground Truth Audit.

Phases 0, 2, 3 run immediately (Tranche A — no predictor outcomes needed).
Phases 1, 4, 5 require ~3500 settled outcomes (Tranche B, March 18+).

Usage:
  tmux new -s eq-ws1
  cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/eq_ws1_predictor_audit.py \
    --start-date 2025-08-01 --end-date 2026-02-28 --workers 12
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing as mp
import os
import sys
import time as time_mod
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Runtime path detection (CLAUDE.md §2)
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")
    sys.path.insert(0, "/scripts/equities_predictor")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "equities_predictor"))

from equities_predictor.db import (  # type: ignore[import-untyped]
    connect,
    list_symbols,
    load_events,
    load_feature_events,
    load_ohlcv,
    load_earnings_calendar,
)
from equities_predictor.indicators import INDICATOR_REGISTRY  # type: ignore[import-untyped]
from equities_predictor.content_features import (  # type: ignore[import-untyped]
    ETF_SYMBOLS,
    compute_rolling_beta,
    compute_excess_returns,
    compute_residual_vol,
    compute_realized_vol,
    compute_tone_score,
    compute_simple_sentiment,
    compute_filing_type_features,
    is_etf,
    label_excess_return,
    label_raw_return,
    moulton_effective_n,
    estimate_cross_sectional_correlation,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

FEATURE_COLUMNS_LEGACY = [
    "price_return_1", "price_return_5", "price_return_20",
    "rolling_vol_20",
    "event_count_6h", "event_count_24h", "event_count_3d",
    "event_sentiment_mean_6h", "event_sentiment_mean_24h", "event_sentiment_mean_3d",
    "anti_bot_mean_24h", "sec_event_count_3d", "earnings_window_flag",
    "source_count_6h", "source_count_24h", "source_count_3d",
]
FEATURE_COLUMNS = FEATURE_COLUMNS_LEGACY + [
    name for name in sorted(INDICATOR_REGISTRY.keys())
    if name not in FEATURE_COLUMNS_LEGACY
]

HORIZONS = ["1d", "3d", "5d", "gap_open"]
OUTPUT_DIR = Path("/scripts/output" if os.path.isdir("/app") else
                  os.path.join(os.path.dirname(__file__), "output"))


# ── Phase checkpointing (reused from crypto WS2) ──────────────

def write_phase_output(data: dict, phase: int, output_dir: Path,
                       phase_version: str = "1.0") -> str:
    payload_str = json.dumps(data, sort_keys=True, default=str)
    sha = hashlib.sha256(payload_str.encode()).hexdigest()
    data["_meta"] = {
        "phase": phase,
        "phase_version": phase_version,
        "sha256": sha,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path = output_dir / f"eq_ws1_phase{phase}_output.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info(f"Phase {phase} output → {path} (sha256={sha[:12]}...)")
    return sha


def load_phase_output(phase: int, output_dir: Path) -> dict:
    path = output_dir / f"eq_ws1_phase{phase}_output.json"
    if not path.exists():
        raise FileNotFoundError(f"Phase {phase} output not found at {path}")
    with open(path) as f:
        return json.load(f)


# ── Globals for multiprocessing workers ────────────────────────
_G_FEATURE_EVENTS: list[dict] = []
_G_OHLCV_1D: dict[str, list[dict]] = {}
_G_EVENTS: list[dict] = []
_G_SPY_BARS: list[dict] = []


# ══════════════════════════════════════════════════════════════
# PHASE 0: Calibration — return distributions, base rates
# ══════════════════════════════════════════════════════════════

def phase0_calibration(
    symbols: list[str],
    ohlcv_1d: dict[str, list[dict]],
    spy_bars: list[dict],
) -> dict:
    """Compute per-symbol and per-sector forward return distributions."""
    log.info("Phase 0: Calibration — forward return distributions")
    t0 = time_mod.time()

    per_symbol: dict[str, dict] = {}
    all_returns_1d: list[float] = []

    spy_closes = np.array([b["close"] for b in spy_bars], dtype=float)
    spy_returns_1d = np.diff(spy_closes) / spy_closes[:-1] if len(spy_closes) > 1 else np.array([])

    for sym in sorted(symbols):
        bars = ohlcv_1d.get(sym, [])
        if len(bars) < 30:
            continue

        closes = np.array([b["close"] for b in bars], dtype=float)
        returns = np.diff(closes) / closes[:-1]
        abs_returns = np.abs(returns)

        if len(abs_returns) < 10:
            continue

        pcts = {
            f"p{p}": round(float(np.percentile(abs_returns, p)), 6)
            for p in [50, 75, 90, 95, 99]
        }
        pcts["mean"] = round(float(np.mean(abs_returns)), 6)
        pcts["std"] = round(float(np.std(returns)), 6)
        pcts["n_bars"] = len(bars)
        pcts["is_etf"] = is_etf(sym)

        per_symbol[sym] = pcts
        all_returns_1d.extend(returns.tolist())

    # Universe-wide stats
    all_arr = np.array(all_returns_1d)
    universe_stats = {
        f"p{p}": round(float(np.percentile(np.abs(all_arr), p)), 6)
        for p in [50, 75, 90, 95, 99]
    }
    universe_stats["mean"] = round(float(np.mean(np.abs(all_arr))), 6)
    universe_stats["n_observations"] = len(all_arr)

    # Kill switch check
    if universe_stats["p75"] < 0.003:
        log.warning(f"⚠ Universe p75={universe_stats['p75']:.4f} < 0.3% — "
                    f"consider per-symbol thresholds or z-score approach")

    # Separate ETFs vs stocks
    etf_stats = {s: v for s, v in per_symbol.items() if v.get("is_etf")}
    stock_stats = {s: v for s, v in per_symbol.items() if not v.get("is_etf")}

    elapsed = time_mod.time() - t0
    log.info(f"Phase 0 done: {len(per_symbol)} symbols, "
             f"{len(stock_stats)} stocks + {len(etf_stats)} ETFs, "
             f"{elapsed:.1f}s")

    return {
        "per_symbol": per_symbol,
        "universe": universe_stats,
        "etf_count": len(etf_stats),
        "stock_count": len(stock_stats),
    }


# ══════════════════════════════════════════════════════════════
# PHASE 2: Feature Attribution (runs now — Tranche A)
# ══════════════════════════════════════════════════════════════

def _phase2_worker(sym: str) -> dict | None:
    """Process one symbol for Phase 2 feature attribution."""
    sym_fes = [fe for fe in _G_FEATURE_EVENTS if fe["symbol"] == sym]
    if not sym_fes:
        return None

    sym_bars = _G_OHLCV_1D.get(sym, [])
    if len(sym_bars) < 30:
        return None

    # Build close price lookup by date for forward return computation
    bar_by_date: dict[str, dict] = {}
    for b in sym_bars:
        ts = b["bar_start_ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        bar_by_date[ts.strftime("%Y-%m-%d")] = b

    sorted_dates = sorted(bar_by_date.keys())
    date_to_idx = {d: i for i, d in enumerate(sorted_dates)}

    results_by_horizon: dict[str, list[dict]] = defaultdict(list)

    for fe in sym_fes:
        features = fe.get("features") or {}
        source_cov = fe.get("source_coverage") or {}
        merged = {**features, **source_cov}
        horizon = fe["horizon"]

        # Get bar date for this feature event
        bar_ts = fe["bar_start_ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        bar_date = bar_ts.strftime("%Y-%m-%d")

        if bar_date not in date_to_idx:
            continue

        idx = date_to_idx[bar_date]

        # Compute forward return for this horizon
        if horizon == "gap_open":
            # next bar's open / current close - 1
            if idx + 1 < len(sorted_dates):
                next_bar = bar_by_date[sorted_dates[idx + 1]]
                cur_bar = bar_by_date[bar_date]
                fwd_ret = float(next_bar["open"]) / float(cur_bar["close"]) - 1
            else:
                continue
        else:
            n_days = int(horizon.replace("d", ""))
            if idx + n_days < len(sorted_dates):
                future_bar = bar_by_date[sorted_dates[idx + n_days]]
                cur_bar = bar_by_date[bar_date]
                fwd_ret = float(future_bar["close"]) / float(cur_bar["close"]) - 1
            else:
                continue

        direction_correct = 1 if fwd_ret > 0 else 0

        results_by_horizon[horizon].append({
            "features": merged,
            "forward_return": fwd_ret,
            "direction_correct": direction_correct,
        })

    if not results_by_horizon:
        return None

    # Point-biserial correlation per feature per horizon
    correlations: dict[str, dict[str, float | None]] = {}
    for horizon, entries in results_by_horizon.items():
        if len(entries) < 20:
            continue

        directions = np.array([e["direction_correct"] for e in entries])
        if directions.std() < 1e-8:
            continue

        for feat_name in FEATURE_COLUMNS:
            vals = []
            dirs = []
            for e in entries:
                v = e["features"].get(feat_name)
                if v is not None and np.isfinite(v):
                    vals.append(v)
                    dirs.append(e["direction_correct"])

            if len(vals) < 20:
                continue

            vals_arr = np.array(vals)
            dirs_arr = np.array(dirs)

            if vals_arr.std() < 1e-10 or dirs_arr.std() < 1e-10:
                continue

            corr = float(np.corrcoef(vals_arr, dirs_arr)[0, 1])
            if np.isfinite(corr):
                key = f"{feat_name}|{horizon}"
                correlations[key] = correlations.get(key, 0.0)
                # Accumulate — we'll average across symbols in the main thread
                if key not in correlations:
                    correlations[key] = {"sum": corr, "n": 1}
                else:
                    if isinstance(correlations[key], dict):
                        correlations[key]["sum"] += corr
                        correlations[key]["n"] += 1
                    else:
                        correlations[key] = {"sum": correlations[key] + corr, "n": 2}

    return {
        "symbol": sym,
        "n_feature_events": len(sym_fes),
        "correlations": correlations,
        "horizons_covered": list(results_by_horizon.keys()),
    }


def phase2_feature_attribution(
    symbols: list[str],
    workers: int,
) -> dict:
    """Phase 2: Point-biserial correlation of each feature with directional accuracy."""
    log.info("Phase 2: Feature Attribution")
    t0 = time_mod.time()

    # Filter to symbols that have feature events
    syms_with_data = sorted(set(fe["symbol"] for fe in _G_FEATURE_EVENTS))
    syms_to_process = [s for s in syms_with_data if s in set(symbols)]
    log.info(f"  {len(syms_to_process)} symbols with feature events")

    if workers > 1 and len(syms_to_process) > 1:
        with mp.Pool(min(workers, len(syms_to_process))) as pool:
            results = pool.map(_phase2_worker, syms_to_process)
    else:
        results = [_phase2_worker(s) for s in syms_to_process]

    results = [r for r in results if r is not None]
    log.info(f"  {len(results)} symbols produced correlations")

    # Aggregate correlations across symbols
    agg: dict[str, dict] = defaultdict(lambda: {"sum": 0.0, "n": 0})
    for r in results:
        for key, val in r["correlations"].items():
            if isinstance(val, dict):
                agg[key]["sum"] += val["sum"]
                agg[key]["n"] += val["n"]
            else:
                agg[key]["sum"] += val
                agg[key]["n"] += 1

    feature_rankings: list[dict] = []
    for key, vals in sorted(agg.items()):
        feat, horizon = key.split("|")
        mean_corr = vals["sum"] / vals["n"] if vals["n"] > 0 else 0
        feature_rankings.append({
            "feature": feat,
            "horizon": horizon,
            "mean_correlation": round(mean_corr, 6),
            "n_symbols": vals["n"],
        })

    # Sort by abs correlation descending
    feature_rankings.sort(key=lambda x: abs(x["mean_correlation"]), reverse=True)

    elapsed = time_mod.time() - t0
    log.info(f"Phase 2 done: {len(feature_rankings)} feature-horizon pairs, {elapsed:.1f}s")

    # Top-10 report
    log.info("  Top-10 feature-horizon correlations:")
    for i, r in enumerate(feature_rankings[:10]):
        log.info(f"    {i+1}. {r['feature']}|{r['horizon']}: "
                 f"r={r['mean_correlation']:.4f} (n={r['n_symbols']})")

    return {
        "feature_rankings": feature_rankings,
        "n_symbols_processed": len(results),
        "n_feature_horizon_pairs": len(feature_rankings),
    }


# ══════════════════════════════════════════════════════════════
# PHASE 3: Event Analysis (runs now — Tranche A)
# ══════════════════════════════════════════════════════════════

def phase3_event_analysis(
    symbols: list[str],
    events: list[dict],
    ohlcv_1d: dict[str, list[dict]],
    earnings_calendar: list[dict],
) -> dict:
    """Phase 3: Event type analysis, tone scorer verdict, session timing."""
    log.info("Phase 3: Event Analysis")
    t0 = time_mod.time()

    # ── Per event type counts and coverage ──────────────────
    type_counts: dict[str, int] = defaultdict(int)
    type_symbols: dict[str, set] = defaultdict(set)
    for ev in events:
        et = ev.get("event_type", "UNKNOWN")
        type_counts[et] += 1
        type_symbols[et].add(ev.get("symbol", ""))

    event_type_summary = []
    for et in sorted(type_counts.keys()):
        event_type_summary.append({
            "event_type": et,
            "count": type_counts[et],
            "n_symbols": len(type_symbols[et]),
            "pct_of_total": round(type_counts[et] / max(1, len(events)) * 100, 1),
        })

    log.info(f"  {len(events)} total events across {len(type_counts)} types")
    for s in event_type_summary:
        log.info(f"    {s['event_type']}: {s['count']} ({s['pct_of_total']:.1f}%)")

    # ── Tone scorer evaluation ──────────────────────────────
    tone_results = {"n_scored": 0, "n_nonzero": 0, "scores": []}
    sentiment_results = {"n_scored": 0, "n_nonzero": 0, "scores": []}

    for ev in events:
        title = ev.get("title", "")
        source = ev.get("source", "")
        ab_score = ev.get("anti_bot_score", 0.0)

        tone = compute_tone_score(title, source, ab_score)
        tone_results["n_scored"] += 1
        if tone != 0.0:
            tone_results["n_nonzero"] += 1
        tone_results["scores"].append(tone)

        sent = compute_simple_sentiment(title)
        sentiment_results["n_scored"] += 1
        if sent != 0.0:
            sentiment_results["n_nonzero"] += 1
        sentiment_results["scores"].append(sent)

    tone_coverage = tone_results["n_nonzero"] / max(1, tone_results["n_scored"])
    sent_coverage = sentiment_results["n_nonzero"] / max(1, sentiment_results["n_scored"])

    log.info(f"  Tone scorer: {tone_results['n_nonzero']}/{tone_results['n_scored']} "
             f"nonzero ({tone_coverage:.1%})")
    log.info(f"  Simple sentiment: {sentiment_results['n_nonzero']}/{sentiment_results['n_scored']} "
             f"nonzero ({sent_coverage:.1%})")

    # ── Session timing distribution ─────────────────────────
    session_dist: dict[str, int] = defaultdict(int)
    for ev in events:
        sr = ev.get("session_relation", "unknown")
        session_dist[sr] += 1

    # ── Earnings window analysis ────────────────────────────
    # Build earnings dates lookup per symbol
    earnings_by_sym: dict[str, list[date]] = defaultdict(list)
    for ec in earnings_calendar:
        if ec.get("report_date"):
            rd = ec["report_date"]
            if isinstance(rd, str):
                rd = date.fromisoformat(rd)
            earnings_by_sym[ec["symbol"]].append(rd)

    n_in_window = 0
    n_out_window = 0
    for ev in events:
        sym = ev.get("symbol", "")
        ev_ts = ev.get("available_at_ts")
        if ev_ts is None:
            continue
        if isinstance(ev_ts, str):
            ev_ts = datetime.fromisoformat(ev_ts)
        ev_date = ev_ts.date()

        in_window = False
        for ed in earnings_by_sym.get(sym, []):
            if abs((ev_date - ed).days) <= 5:
                in_window = True
                break

        if in_window:
            n_in_window += 1
        else:
            n_out_window += 1

    log.info(f"  Events in earnings window: {n_in_window} "
             f"({n_in_window / max(1, n_in_window + n_out_window):.1%})")

    # ── Event density per symbol-day ────────────────────────
    sym_day_counts: dict[str, int] = defaultdict(int)
    for ev in events:
        sym = ev.get("symbol", "")
        ev_ts = ev.get("available_at_ts")
        if ev_ts is None:
            continue
        if isinstance(ev_ts, str):
            ev_ts = datetime.fromisoformat(ev_ts)
        key = f"{sym}|{ev_ts.strftime('%Y-%m-%d')}"
        sym_day_counts[key] += 1

    density_vals = list(sym_day_counts.values())
    density_stats = {}
    if density_vals:
        density_arr = np.array(density_vals)
        density_stats = {
            "mean": round(float(np.mean(density_arr)), 2),
            "median": round(float(np.median(density_arr)), 2),
            "p90": round(float(np.percentile(density_arr, 90)), 2),
            "max": int(np.max(density_arr)),
        }

    elapsed = time_mod.time() - t0
    log.info(f"Phase 3 done: {elapsed:.1f}s")

    # Don't serialize full score arrays — just stats
    tone_stats = {
        "n_scored": tone_results["n_scored"],
        "n_nonzero": tone_results["n_nonzero"],
        "coverage": round(tone_coverage, 4),
        "mean_when_nonzero": round(float(np.mean(
            [s for s in tone_results["scores"] if s != 0.0]
        )), 4) if tone_results["n_nonzero"] > 0 else 0.0,
    }
    sent_stats = {
        "n_scored": sentiment_results["n_scored"],
        "n_nonzero": sentiment_results["n_nonzero"],
        "coverage": round(sent_coverage, 4),
        "mean_when_nonzero": round(float(np.mean(
            [s for s in sentiment_results["scores"] if s != 0.0]
        )), 4) if sentiment_results["n_nonzero"] > 0 else 0.0,
    }

    return {
        "event_type_summary": event_type_summary,
        "tone_scorer_verdict": tone_stats,
        "simple_sentiment_verdict": sent_stats,
        "session_distribution": dict(session_dist),
        "earnings_window": {
            "n_in_window": n_in_window,
            "n_out_window": n_out_window,
        },
        "event_density": density_stats,
    }


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main() -> None:
    global _G_FEATURE_EVENTS, _G_OHLCV_1D, _G_EVENTS, _G_SPY_BARS

    parser = argparse.ArgumentParser(description="EQ-WS1 Predictor Ground Truth Audit")
    parser.add_argument("--start-date", required=True, help="Study start (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="Study end (YYYY-MM-DD)")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--from-phase", type=int, default=0)
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("EQ-WS1: Equities Predictor Ground Truth Audit")
    log.info("=" * 60)
    log.info(f"  Period:  {args.start_date} to {args.end_date}")
    log.info(f"  Workers: {args.workers}")
    log.info(f"  Output:  {OUTPUT_DIR}")
    log.info("=" * 60)

    # ── Load data ──────────────────────────────────────────
    conn = connect()

    symbols = list_symbols(conn)
    log.info(f"Loaded {len(symbols)} symbols from registry")

    # OHLCV 1d with warmup (300 days for SMA_200)
    warmup_start = start_dt - timedelta(days=300)
    log.info(f"Loading 1d OHLCV from {warmup_start.date()} to {end_dt.date()}...")
    ohlcv_raw = load_ohlcv(conn, symbols=symbols, timeframe="1d",
                           start_ts=warmup_start, end_ts=end_dt)
    log.info(f"  {len(ohlcv_raw)} OHLCV bars loaded")

    # Group by symbol
    ohlcv_1d: dict[str, list[dict]] = defaultdict(list)
    for bar in ohlcv_raw:
        ohlcv_1d[bar["symbol"]].append(bar)
    _G_OHLCV_1D = dict(ohlcv_1d)

    # SPY bars for excess-return computation
    spy_bars = ohlcv_1d.get("SPY", [])
    _G_SPY_BARS = spy_bars
    log.info(f"  SPY: {len(spy_bars)} bars")

    # Events
    log.info("Loading events...")
    events = load_events(conn, symbols=symbols, start_ts=start_dt, end_ts=end_dt)
    _G_EVENTS = events
    log.info(f"  {len(events)} events loaded")

    # Feature events
    log.info("Loading feature events (1d timeframe, all horizons)...")
    feature_events = load_feature_events(
        conn, symbols=symbols, timeframe="1d",
        horizons=HORIZONS, start_ts=start_dt, end_ts=end_dt,
    )
    _G_FEATURE_EVENTS = feature_events
    log.info(f"  {len(feature_events)} feature events loaded")

    # Earnings calendar
    earnings_calendar = load_earnings_calendar(conn, symbols=symbols)
    log.info(f"  {len(earnings_calendar)} earnings calendar entries")

    conn.close()

    # ── Phase 0 ────────────────────────────────────────────
    if args.from_phase <= 0:
        phase0_data = phase0_calibration(symbols, ohlcv_1d, spy_bars)
        write_phase_output(phase0_data, 0, OUTPUT_DIR)
    else:
        phase0_data = load_phase_output(0, OUTPUT_DIR)
        log.info(f"Loaded Phase 0 from disk")

    # ── Phase 2 (Feature Attribution) ─────────────────────
    if args.from_phase <= 2:
        phase2_data = phase2_feature_attribution(symbols, args.workers)
        write_phase_output(phase2_data, 2, OUTPUT_DIR)
    else:
        phase2_data = load_phase_output(2, OUTPUT_DIR)
        log.info(f"Loaded Phase 2 from disk")

    # ── Phase 3 (Event Analysis) ──────────────────────────
    if args.from_phase <= 3:
        phase3_data = phase3_event_analysis(symbols, events, ohlcv_1d, earnings_calendar)
        write_phase_output(phase3_data, 3, OUTPUT_DIR)
    else:
        phase3_data = load_phase_output(3, OUTPUT_DIR)
        log.info(f"Loaded Phase 3 from disk")

    # ── Phase 5: Report ───────────────────────────────────
    log.info("Phase 5: Generating report")

    report = {
        "study": "EQ-WS1 Predictor Ground Truth Audit",
        "period": f"{args.start_date} to {args.end_date}",
        "tranche": "A (Phases 0, 2, 3 — no predictor outcomes)",
        "n_symbols": len(symbols),
        "n_stocks": len([s for s in symbols if not is_etf(s)]),
        "n_etfs": len([s for s in symbols if is_etf(s)]),
        "n_events": len(events),
        "n_feature_events": len(feature_events),
        "phase0_calibration": phase0_data,
        "phase2_feature_attribution": phase2_data,
        "phase3_event_analysis": phase3_data,
        "tranche_b_pending": [
            "Phase 1: Prediction vs Reality (needs ~3500 outcomes)",
            "Phase 4: Failure Mode Classification (needs outcomes)",
        ],
    }

    report_path = OUTPUT_DIR / "eq_ws1_predictor_audit.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"Report written to {report_path}")

    # ── Summary ───────────────────────────────────────────
    md_lines = [
        "# EQ-WS1 Predictor Ground Truth Audit — Tranche A",
        f"\n**Period**: {args.start_date} to {args.end_date}",
        f"**Symbols**: {len(symbols)} ({report['n_stocks']} stocks + {report['n_etfs']} ETFs)",
        f"**Events**: {len(events)}",
        f"**Feature Events**: {len(feature_events)}",
        "",
        "## Phase 0: Return Distributions",
        f"- Universe daily |return| p75: {phase0_data.get('universe', {}).get('p75', 'N/A')}",
        f"- Universe daily |return| p90: {phase0_data.get('universe', {}).get('p90', 'N/A')}",
        "",
        "## Phase 2: Feature Attribution (Top-10)",
    ]

    for i, r in enumerate(phase2_data.get("feature_rankings", [])[:10]):
        md_lines.append(
            f"{i+1}. `{r['feature']}` | {r['horizon']}: "
            f"r={r['mean_correlation']:.4f} (n={r['n_symbols']})"
        )

    md_lines.extend([
        "",
        "## Phase 3: Event Analysis",
    ])
    for s in phase3_data.get("event_type_summary", []):
        md_lines.append(f"- {s['event_type']}: {s['count']} ({s['pct_of_total']}%)")

    tv = phase3_data.get("tone_scorer_verdict", {})
    sv = phase3_data.get("simple_sentiment_verdict", {})
    md_lines.extend([
        "",
        "## Scorer Verdicts",
        f"- **Tone scorer**: {tv.get('coverage', 0):.1%} nonzero coverage",
        f"- **Simple sentiment**: {sv.get('coverage', 0):.1%} nonzero coverage",
    ])

    md_path = OUTPUT_DIR / "eq_ws1_predictor_audit.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))
    log.info(f"Markdown summary written to {md_path}")

    log.info("EQ-WS1 Tranche A complete.")


if __name__ == "__main__":
    main()
