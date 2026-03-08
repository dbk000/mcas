#!/usr/bin/env python3
"""
Workstream 2: Pump Cluster Analysis + D1 Cross-Reference
=========================================================
Steps:
  2.1 Parse WhaleBotPumps posts into social_bot_signals table
  2.2 Detect pump clusters (same symbol, 4h window, ≥3 alerts)
  2.3 Classify cluster outcomes using 1h OHLCV
  2.4 Statistical summary (dump rate, timing, predictors)
  2.5 Compute bot_toxicity_ratio for D1 opportunities
  2.6 D1 cross-reference: PF by cluster presence, toxicity quartile, Early SL

Output: scripts/output/pump_cluster_analysis_{ts}.json

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/social_pump_cluster_analysis.py
"""

import bisect
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
if os.path.isdir("/scripts/output"):
    OUTPUT_DIR = "/scripts/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"pump_cluster_analysis_{TIMESTAMP}.json")

# ---------------------------------------------------------------------------
# Channel classifications
# ---------------------------------------------------------------------------
PUMP_SIGNAL_CHANNELS = {
    ("telegram_historical", "WhaleBotAlerts"),
    ("telegram_historical", "WhaleBotPumps"),
    ("telegram_historical", "whale_alert_io"),
    ("telegram_historical", "WhaleBotRektd"),
    ("telegram_historical", "CryptoScanner100eyes"),
}

CONTENT_FARM_CHANNELS = {
    ("news_historical", "CoinOtag"),
    ("news_historical", "Bitcoin World"),
    ("news_historical", "TimesTabloid"),
    ("news_historical", "Crypto Daily"),
    ("news_historical", "Coin Edition"),
    ("news_historical", "U.Today"),
    ("telegram_historical", "coingeckonews"),
    ("coingecko", "trending"),
    ("telegram_historical", "Kucoin_News"),
    ("telegram_historical", "binance_announcements"),
    ("telegram_historical", "CryptoComOfficialAnnouncements"),
    ("telegram_historical", "Bybit_Announcements"),
}

# All non-human channels (for toxicity ratio)
BOT_CHANNELS = PUMP_SIGNAL_CHANNELS | CONTENT_FARM_CHANNELS

CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3

# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------
def parse_number(s: str) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", "")
    mult = 1
    if s.endswith("K"):
        mult = 1_000; s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000; s = s[:-1]
    elif s.endswith("B"):
        mult = 1_000_000_000; s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None

# ---------------------------------------------------------------------------
# Regex patterns for WhaleBotPumps
# ---------------------------------------------------------------------------
PUMP_HEADER_RE = re.compile(
    r"(\w+)/USDT\s+is\s+(Pumping|Dumping)\s+on\s+(\w+)", re.IGNORECASE
)
PRICE_RE = re.compile(r"Price:\s*([\d,.]+)\s*USDT\s*\(([+-]?[\d,.]+)%?\)")
VOL_24H_RE = re.compile(r"24h\s*Vol:\s*([\d,.]+[KMB]?)\s*USDT", re.IGNORECASE)
LAST_VOL_RE = re.compile(r"Last\s*Vol:\s*([\d,.]+[KMB]?)\s*\(([\d,.]+)%?\)")
SPREAD_RE = re.compile(r"Spread:\s*([\d,.]+)%")
BEST_BID_RE = re.compile(r"Best\s+Bid\s+Vol:\s*([\d,.]+[KMB]?)\s*USDT", re.IGNORECASE)
BEST_ASK_RE = re.compile(r"Best\s+Ask\s+Vol:\s*([\d,.]+[KMB]?)\s*USDT", re.IGNORECASE)
OI_RE = re.compile(r"OI:\s*([\d,.]+[KMB]?)\s*\(([+-]?[\d,.]+)%?\)")
FUNDING_RE = re.compile(r"Funding:\s*([+-]?[\d,.]+)%")
SHORT_ALERT_RE = re.compile(r"Alert:\s*(\w+)/USDT\s*-\s*(pump|dump)", re.IGNORECASE)


def parse_whalebotpumps(post_id: int, content: str, post_ts) -> dict | None:
    """Parse a WhaleBotPumps post into structured fields."""
    if not content:
        return None
    result = {
        "source_post_id": post_id,
        "source": "telegram_historical",
        "channel": "WhaleBotPumps",
        "post_ts": post_ts,
    }

    m = PUMP_HEADER_RE.search(content)
    if m:
        ticker = m.group(1).upper()
        result["symbol"] = f"{ticker}USDT"
        result["signal_type"] = "pump" if "pump" in m.group(2).lower() else "dump"
        result["exchange"] = m.group(3)

        for regex, key in [
            (PRICE_RE, None),  # special: two groups
            (VOL_24H_RE, "vol_24h"),
            (SPREAD_RE, "spread_pct"),
            (BEST_BID_RE, "bid_vol"),
            (BEST_ASK_RE, "ask_vol"),
        ]:
            m2 = regex.search(content)
            if m2:
                if key:
                    result[key] = parse_number(m2.group(1))
                else:
                    result["price"] = parse_number(m2.group(1))
                    result["pct_change"] = parse_number(m2.group(2))

        m2 = LAST_VOL_RE.search(content)
        if m2:
            result["last_vol"] = parse_number(m2.group(1))
            result["vol_change_pct"] = parse_number(m2.group(2))

        m2 = OI_RE.search(content)
        if m2:
            result["oi"] = parse_number(m2.group(1))
            result["oi_change_pct"] = parse_number(m2.group(2))

        m2 = FUNDING_RE.search(content)
        if m2:
            result["funding_rate"] = parse_number(m2.group(1))

        return result

    m = SHORT_ALERT_RE.search(content)
    if m:
        result["symbol"] = f"{m.group(1).upper()}USDT"
        result["signal_type"] = "pump" if "pump" in m.group(2).lower() else "dump"
        return result

    return None


# ---------------------------------------------------------------------------
# DB: create table + insert
# ---------------------------------------------------------------------------
def create_bot_signals_table(conn):
    logger.info("Creating social_bot_signals table if not exists...")
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS social_bot_signals (
                id SERIAL PRIMARY KEY,
                source_post_id BIGINT,
                source TEXT NOT NULL,
                channel TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT,
                signal_type TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_bot_signals_symbol_ts
                ON social_bot_signals (symbol, post_ts);
            CREATE INDEX IF NOT EXISTS idx_bot_signals_type
                ON social_bot_signals (signal_type, post_ts);
        """)
    conn.commit()


def insert_bot_signals(conn, signals: list[dict]):
    """Insert parsed signals into social_bot_signals (truncate first for idempotency)."""
    logger.info("Inserting %d signals into social_bot_signals...", len(signals))
    with conn.cursor() as cur:
        cur.execute("TRUNCATE social_bot_signals RESTART IDENTITY")
        cols = [
            "source_post_id", "source", "channel", "symbol", "exchange",
            "signal_type", "price", "pct_change", "vol_24h", "last_vol",
            "vol_change_pct", "oi", "oi_change_pct", "funding_rate",
            "bid_vol", "ask_vol", "spread_pct", "post_ts",
        ]
        sql = f"""
            INSERT INTO social_bot_signals ({', '.join(cols)})
            VALUES ({', '.join(['%s'] * len(cols))})
        """
        batch = []
        for sig in signals:
            row = tuple(sig.get(c) for c in cols)
            batch.append(row)
            if len(batch) >= 500:
                cur.executemany(sql, batch)
                batch = []
        if batch:
            cur.executemany(sql, batch)
    conn.commit()
    logger.info("Insert complete.")


# ---------------------------------------------------------------------------
# Cluster detection
# ---------------------------------------------------------------------------
def detect_pump_clusters(signals: list[dict]) -> list[dict]:
    """Gap-based clustering: same symbol, ≤4h gap, ≥3 alerts."""
    pumps = [s for s in signals if s.get("signal_type") == "pump"]
    by_symbol = defaultdict(list)
    for s in pumps:
        by_symbol[s["symbol"]].append(s)

    clusters = []
    for symbol, sigs in by_symbol.items():
        sigs.sort(key=lambda x: x["post_ts"])
        current = [sigs[0]]
        for s in sigs[1:]:
            gap_h = (s["post_ts"] - current[-1]["post_ts"]).total_seconds() / 3600
            if gap_h <= CLUSTER_WINDOW_HOURS:
                current.append(s)
            else:
                if len(current) >= MIN_ALERTS_PER_CLUSTER:
                    clusters.append(_build_cluster(symbol, current))
                current = [s]
        if len(current) >= MIN_ALERTS_PER_CLUSTER:
            clusters.append(_build_cluster(symbol, current))

    clusters.sort(key=lambda c: c["cluster_start_ts"])
    return clusters


def _build_cluster(symbol: str, signals: list[dict]) -> dict:
    exchanges = set(s.get("exchange") for s in signals if s.get("exchange"))
    pct_changes = [s["pct_change"] for s in signals if s.get("pct_change") is not None]
    vol_changes = [s["vol_change_pct"] for s in signals if s.get("vol_change_pct") is not None]
    peak_sig = max(signals, key=lambda s: (s.get("pct_change") or 0))

    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "exchanges_seen": sorted(exchanges),
        "exchange_count": len(exchanges),
        "max_pct_change": max(pct_changes) if pct_changes else None,
        "max_vol_change_pct": max(vol_changes) if vol_changes else None,
        "funding_rate_at_peak": peak_sig.get("funding_rate"),
    }


# ---------------------------------------------------------------------------
# OHLCV outcome classification
# ---------------------------------------------------------------------------
def classify_cluster_outcomes(conn, clusters: list[dict]) -> list[dict]:
    """For each cluster, look up 1h OHLCV to classify outcome."""
    logger.info("Classifying %d cluster outcomes via OHLCV...", len(clusters))
    if not clusters:
        return clusters

    # Collect unique symbols
    symbols = sorted(set(c["symbol"] for c in clusters))
    logger.info("  %d unique symbols in clusters", len(symbols))

    # Time range needed
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=2)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=80)

    # Load OHLCV for all cluster symbols in bulk
    ohlcv_by_symbol = {}  # symbol -> sorted list of (ts, open, high, low, close)
    with conn.cursor() as cur:
        for sym in symbols:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close
                FROM ohlcv
                WHERE symbol = %s AND timeframe = '1h'
                  AND bar_start_ts >= %s AND bar_start_ts <= %s
                ORDER BY bar_start_ts
            """, (sym, earliest, latest))
            rows = cur.fetchall()
            if rows:
                ohlcv_by_symbol[sym] = rows

    logger.info("  OHLCV loaded for %d/%d symbols", len(ohlcv_by_symbol), len(symbols))

    for cluster in clusters:
        sym = cluster["symbol"]
        bars = ohlcv_by_symbol.get(sym)
        if not bars:
            cluster["outcome"] = "no_ohlcv"
            continue

        bar_times = [b[0] for b in bars]
        start_ts = cluster["cluster_start_ts"]

        # Pre-pump price: close of bar immediately before cluster start
        pre_idx = bisect.bisect_left(bar_times, start_ts) - 1
        if pre_idx < 0:
            cluster["outcome"] = "no_ohlcv"
            continue
        pre_pump_price = float(bars[pre_idx][4])  # close

        # Peak price: max high in 24h after cluster start
        end_24h = start_ts + timedelta(hours=24)
        end_48h = start_ts + timedelta(hours=48)
        end_72h = start_ts + timedelta(hours=72)

        start_bar_idx = bisect.bisect_left(bar_times, start_ts)
        end_24h_idx = bisect.bisect_right(bar_times, end_24h)
        end_48h_idx = bisect.bisect_right(bar_times, end_48h)
        end_72h_idx = bisect.bisect_right(bar_times, end_72h)

        window_24h = bars[start_bar_idx:end_24h_idx]
        if not window_24h:
            cluster["outcome"] = "no_ohlcv"
            continue

        peak_price = max(float(b[2]) for b in window_24h)  # max high

        # Post-pump prices (close of the bar nearest to each offset)
        def get_close_near(target_ts):
            idx = bisect.bisect_left(bar_times, target_ts)
            if idx >= len(bars):
                idx = len(bars) - 1
            return float(bars[idx][4])

        post_24h = get_close_near(end_24h) if end_24h_idx > 0 else None
        post_48h = get_close_near(end_48h) if end_48h_idx > 0 else None
        post_72h = get_close_near(end_72h) if end_72h_idx > 0 else None

        cluster["pre_pump_price"] = pre_pump_price
        cluster["peak_price"] = peak_price
        cluster["post_24h_price"] = post_24h
        cluster["post_48h_price"] = post_48h
        cluster["post_72h_price"] = post_72h

        # Peak move
        peak_move_pct = (peak_price - pre_pump_price) / pre_pump_price * 100

        # Classification
        if post_72h is not None:
            post_72h_move = (post_72h - pre_pump_price) / pre_pump_price * 100
        elif post_48h is not None:
            post_72h_move = (post_48h - pre_pump_price) / pre_pump_price * 100
        else:
            post_72h_move = None

        if post_48h is not None:
            post_48h_move = (post_48h - pre_pump_price) / pre_pump_price * 100
        else:
            post_48h_move = None

        if peak_move_pct < 5:
            cluster["outcome"] = "noise"
        elif post_72h_move is not None and post_72h_move > 5:
            cluster["outcome"] = "sustained_breakout"
        elif peak_move_pct > 10 and post_48h_move is not None and post_48h_move < 0:
            cluster["outcome"] = "pump_and_dump"
        elif peak_move_pct >= 5:
            cluster["outcome"] = "fade"
        else:
            cluster["outcome"] = "noise"

        cluster["peak_move_pct"] = round(peak_move_pct, 2)
        cluster["post_48h_move_pct"] = round(post_48h_move, 2) if post_48h_move is not None else None
        cluster["post_72h_move_pct"] = round(post_72h_move, 2) if post_72h_move is not None else None

        # Time to peak (hours from cluster start to the bar with highest high)
        if window_24h:
            peak_bar_idx = max(range(len(window_24h)), key=lambda i: float(window_24h[i][2]))
            peak_bar_ts = window_24h[peak_bar_idx][0]
            cluster["hours_to_peak"] = (peak_bar_ts - start_ts).total_seconds() / 3600
        else:
            cluster["hours_to_peak"] = None

    return clusters


# ---------------------------------------------------------------------------
# D1 cross-reference
# ---------------------------------------------------------------------------
def compute_d1_cross_reference(conn, clusters: list[dict]) -> dict:
    """
    Compute:
    1. Bot toxicity ratios for each D1 opportunity (48h and 7d windows)
    2. Cluster presence flag (pump cluster within ±6h of trigger)
    3. PF splits by cluster presence, toxicity quartile, Early SL correlation
    """
    logger.info("Computing D1 cross-reference...")

    # Load D1 simulation results (latest per opportunity)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT DISTINCT ON (sr.opportunity_id)
                sr.opportunity_id, sr.symbol, sr.exit_reason,
                sr.pnl_net, sr.r_multiple, sr.bars_held,
                sr.opportunity_created_ts AS trigger_ts
            FROM simulation_results sr
            WHERE sr.setup_id = 'D1' AND sr.data_available = true
            ORDER BY sr.opportunity_id, sr.created_at DESC
        """)
        d1_opps = cur.fetchall()
    logger.info("  Loaded %d D1 opportunities", len(d1_opps))

    if not d1_opps:
        return {"error": "No D1 simulation results found"}

    # Get unique D1 symbols
    d1_symbols = sorted(set(opp["symbol"] for opp in d1_opps))
    d1_symbols_set = set(d1_symbols)

    # Load social_posts for D1 symbols (all sources, for toxicity counting)
    logger.info("  Loading social posts for %d D1 symbols...", len(d1_symbols))
    # Build per-symbol sorted lists of (post_ts, is_bot)
    symbol_posts = defaultdict(list)  # symbol -> [(post_ts, is_bot)]

    with conn.cursor() as cur:
        # Use GIN index on symbols array
        cur.execute("""
            SELECT symbols, post_ts, source, channel
            FROM social_posts
            WHERE symbols && %s::text[]
              AND symbols IS NOT NULL
        """, (d1_symbols,))

        for row in cur:
            syms, post_ts, source, channel = row
            is_bot = (source, channel) in BOT_CHANNELS
            for sym in syms:
                if sym in d1_symbols_set:
                    symbol_posts[sym].append((post_ts, is_bot))

    # Sort each symbol's posts by timestamp
    for sym in symbol_posts:
        symbol_posts[sym].sort(key=lambda x: x[0])

    logger.info("  Social posts loaded for %d/%d symbols",
                len(symbol_posts), len(d1_symbols))

    # Build cluster lookup: symbol -> sorted list of cluster_start_ts
    cluster_starts_by_sym = defaultdict(list)
    for c in clusters:
        cluster_starts_by_sym[c["symbol"]].append(c["cluster_start_ts"])
    for sym in cluster_starts_by_sym:
        cluster_starts_by_sym[sym].sort()

    # For each D1 opp, compute toxicity + cluster presence
    results = []
    for opp in d1_opps:
        sym = opp["symbol"]
        trigger_ts = opp["trigger_ts"]

        # Classify trade
        if opp["pnl_net"] is not None and float(opp["pnl_net"]) > 0:
            group = "Winner"
        elif opp["exit_reason"] == "STOP_LOSS" and opp["bars_held"] is not None and opp["bars_held"] <= 3:
            group = "Early SL"
        else:
            group = "Late Exit"

        posts = symbol_posts.get(sym, [])
        post_times = [p[0] for p in posts]
        post_is_bot = [p[1] for p in posts]

        # Toxicity for 48h and 7d windows
        tox = {}
        for window_name, window_hours in [("48h", 48), ("7d", 168)]:
            window_start = trigger_ts - timedelta(hours=window_hours)
            start_idx = bisect.bisect_left(post_times, window_start)
            end_idx = bisect.bisect_left(post_times, trigger_ts)  # strict < trigger

            total = end_idx - start_idx
            bot_count = sum(1 for i in range(start_idx, end_idx) if post_is_bot[i])

            tox[f"bot_toxicity_{window_name}"] = bot_count / total if total > 0 else 0.0
            tox[f"total_mentions_{window_name}"] = total
            tox[f"bot_mentions_{window_name}"] = bot_count

        # Cluster presence: pump cluster within ±6h of trigger
        cluster_nearby = False
        sym_clusters = cluster_starts_by_sym.get(sym, [])
        if sym_clusters:
            # Find nearest cluster start to trigger
            idx = bisect.bisect_left(sym_clusters, trigger_ts)
            for check_idx in [idx - 1, idx]:
                if 0 <= check_idx < len(sym_clusters):
                    gap = abs((sym_clusters[check_idx] - trigger_ts).total_seconds()) / 3600
                    if gap <= 6:
                        cluster_nearby = True
                        break

        results.append({
            "opportunity_id": opp["opportunity_id"],
            "symbol": sym,
            "group": group,
            "pnl_net": float(opp["pnl_net"]) if opp["pnl_net"] is not None else 0.0,
            "cluster_nearby": cluster_nearby,
            **tox,
        })

    # --- Compute PF splits ---
    def compute_pf(opps_list):
        wins = sum(o["pnl_net"] for o in opps_list if o["pnl_net"] > 0)
        losses = abs(sum(o["pnl_net"] for o in opps_list if o["pnl_net"] < 0))
        n = len(opps_list)
        wr = sum(1 for o in opps_list if o["pnl_net"] > 0) / n * 100 if n else 0
        pf = wins / losses if losses > 0 else float("inf") if wins > 0 else 0
        return {"count": n, "pf": round(pf, 3), "win_rate": round(wr, 1),
                "total_pnl": round(wins - losses, 2)}

    # Split by cluster presence
    with_cluster = [r for r in results if r["cluster_nearby"]]
    without_cluster = [r for r in results if not r["cluster_nearby"]]

    cluster_presence = {
        "with_cluster": compute_pf(with_cluster),
        "without_cluster": compute_pf(without_cluster),
    }

    # Split by bot_toxicity_48h quartile
    tox_values = [r["bot_toxicity_48h"] for r in results]
    if tox_values:
        q25, q50, q75 = np.percentile(tox_values, [25, 50, 75])
    else:
        q25 = q50 = q75 = 0

    quartile_bins = {
        "Q1 (0-25%)": [r for r in results if r["bot_toxicity_48h"] <= q25],
        "Q2 (25-50%)": [r for r in results if q25 < r["bot_toxicity_48h"] <= q50],
        "Q3 (50-75%)": [r for r in results if q50 < r["bot_toxicity_48h"] <= q75],
        "Q4 (75-100%)": [r for r in results if r["bot_toxicity_48h"] > q75],
    }
    toxicity_quartiles = {k: compute_pf(v) for k, v in quartile_bins.items()}
    toxicity_quartiles["quartile_boundaries"] = {
        "q25": round(q25, 4), "q50": round(q50, 4), "q75": round(q75, 4),
    }

    # Early SL correlation: Mann-Whitney on toxicity
    from scipy.stats import mannwhitneyu
    winners_tox = [r["bot_toxicity_48h"] for r in results if r["group"] == "Winner"]
    early_sl_tox = [r["bot_toxicity_48h"] for r in results if r["group"] == "Early SL"]

    early_sl_corr = {
        "winners_count": len(winners_tox),
        "early_sl_count": len(early_sl_tox),
        "winners_mean_toxicity_48h": round(float(np.mean(winners_tox)), 4) if winners_tox else None,
        "early_sl_mean_toxicity_48h": round(float(np.mean(early_sl_tox)), 4) if early_sl_tox else None,
        "winners_median_toxicity_48h": round(float(np.median(winners_tox)), 4) if winners_tox else None,
        "early_sl_median_toxicity_48h": round(float(np.median(early_sl_tox)), 4) if early_sl_tox else None,
    }

    if len(winners_tox) >= 5 and len(early_sl_tox) >= 5:
        stat, pval = mannwhitneyu(early_sl_tox, winners_tox, alternative="two-sided")
        early_sl_corr["mann_whitney_u"] = round(float(stat), 2)
        early_sl_corr["p_value"] = round(float(pval), 6)
        early_sl_corr["significant_005"] = pval < 0.05
    else:
        early_sl_corr["mann_whitney_skipped"] = "Insufficient samples"

    # Also do 7d toxicity correlation
    winners_tox_7d = [r["bot_toxicity_7d"] for r in results if r["group"] == "Winner"]
    early_sl_tox_7d = [r["bot_toxicity_7d"] for r in results if r["group"] == "Early SL"]
    early_sl_corr["winners_mean_toxicity_7d"] = round(float(np.mean(winners_tox_7d)), 4) if winners_tox_7d else None
    early_sl_corr["early_sl_mean_toxicity_7d"] = round(float(np.mean(early_sl_tox_7d)), 4) if early_sl_tox_7d else None

    if len(winners_tox_7d) >= 5 and len(early_sl_tox_7d) >= 5:
        stat, pval = mannwhitneyu(early_sl_tox_7d, winners_tox_7d, alternative="two-sided")
        early_sl_corr["mann_whitney_u_7d"] = round(float(stat), 2)
        early_sl_corr["p_value_7d"] = round(float(pval), 6)
        early_sl_corr["significant_005_7d"] = pval < 0.05

    return {
        "total_d1_opps": len(results),
        "cluster_presence": cluster_presence,
        "toxicity_quartiles": toxicity_quartiles,
        "early_sl_correlation": early_sl_corr,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    logger.info("Output will be written to: %s", OUTPUT_PATH)

    with psycopg.connect(DATABASE_URL) as conn:
        # Step 1: Create table
        create_bot_signals_table(conn)

        # Step 2: Parse WhaleBotPumps
        logger.info("[2.1] Parsing WhaleBotPumps posts...")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, content, post_ts
                FROM social_posts
                WHERE source = 'telegram_historical' AND channel = 'WhaleBotPumps'
                ORDER BY post_ts
            """)
            raw_posts = cur.fetchall()

        signals = []
        parse_failures = 0
        for post_id, content, post_ts in raw_posts:
            parsed = parse_whalebotpumps(post_id, content, post_ts)
            if parsed:
                signals.append(parsed)
            else:
                parse_failures += 1

        pump_count = sum(1 for s in signals if s["signal_type"] == "pump")
        dump_count = sum(1 for s in signals if s["signal_type"] == "dump")
        logger.info("  Parsed %d/%d posts (%d pump, %d dump, %d failed)",
                     len(signals), len(raw_posts), pump_count, dump_count, parse_failures)

        # Insert into DB
        insert_bot_signals(conn, signals)

        # Step 3: Detect pump clusters
        logger.info("[2.2] Detecting pump clusters (window=%dh, min=%d alerts)...",
                     CLUSTER_WINDOW_HOURS, MIN_ALERTS_PER_CLUSTER)
        clusters = detect_pump_clusters(signals)
        logger.info("  Found %d pump clusters", len(clusters))

        # Step 4: Classify outcomes
        logger.info("[2.3] Classifying cluster outcomes via OHLCV...")
        clusters = classify_cluster_outcomes(conn, clusters)

        # Step 5: D1 cross-reference
        logger.info("[2.5-2.6] Computing D1 cross-reference + toxicity...")
        d1_xref = compute_d1_cross_reference(conn, clusters)

    # Step 6: Build output
    outcome_counts = defaultdict(int)
    for c in clusters:
        outcome_counts[c.get("outcome", "unknown")] += 1

    # Timing stats for classified clusters
    classified = [c for c in clusters if c.get("outcome") not in ("no_ohlcv", "noise", None)]
    pump_dumps = [c for c in clusters if c.get("outcome") == "pump_and_dump"]

    # Predictor analysis: does alert_count, exchange_count, or funding predict outcome?
    predictor_analysis = {}
    for outcome in ["pump_and_dump", "sustained_breakout", "fade", "noise"]:
        group = [c for c in clusters if c.get("outcome") == outcome]
        if group:
            predictor_analysis[outcome] = {
                "count": len(group),
                "mean_alert_count": round(np.mean([c["alert_count"] for c in group]), 1),
                "mean_exchange_count": round(np.mean([c["exchange_count"] for c in group]), 1),
                "mean_max_pct_change": round(np.mean([c["max_pct_change"] for c in group if c.get("max_pct_change") is not None]), 1) if any(c.get("max_pct_change") for c in group) else None,
            }
            # Funding rate at peak for those that have it
            funding_vals = [c["funding_rate_at_peak"] for c in group if c.get("funding_rate_at_peak") is not None]
            if funding_vals:
                predictor_analysis[outcome]["mean_funding_rate"] = round(np.mean(funding_vals), 4)
                predictor_analysis[outcome]["funding_sample_size"] = len(funding_vals)

    # Time-to-peak and time-to-dump stats
    peak_times = [c["hours_to_peak"] for c in classified if c.get("hours_to_peak") is not None]
    dump_times_72h = [c.get("post_72h_move_pct") for c in pump_dumps if c.get("post_72h_move_pct") is not None]

    total_classified = sum(v for k, v in outcome_counts.items() if k != "no_ohlcv")
    pd_rate = outcome_counts.get("pump_and_dump", 0) / total_classified * 100 if total_classified > 0 else 0

    output = {
        "metadata": {
            "generated_at": TIMESTAMP,
            "elapsed_seconds": round(time.time() - t0, 1),
            "description": "Workstream 2: Pump Cluster Analysis + D1 Cross-Reference",
        },
        "parsing_summary": {
            "total_whalebotpumps_posts": len(raw_posts),
            "parsed_successfully": len(signals),
            "parse_failed": parse_failures,
            "pump_signals": pump_count,
            "dump_signals": dump_count,
        },
        "pump_clusters": {
            "total_clusters": len(clusters),
            "cluster_params": {
                "window_hours": CLUSTER_WINDOW_HOURS,
                "min_alerts": MIN_ALERTS_PER_CLUSTER,
            },
            "outcome_distribution": dict(outcome_counts),
            "total_classified": total_classified,
            "pump_and_dump_rate_pct": round(pd_rate, 1),
            "viable_short_signal": pd_rate > 60,
            "timing": {
                "mean_hours_to_peak": round(float(np.mean(peak_times)), 1) if peak_times else None,
                "median_hours_to_peak": round(float(np.median(peak_times)), 1) if peak_times else None,
            },
            "predictor_analysis": predictor_analysis,
            "top_clusters": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for k, v in c.items()}
                for c in sorted(clusters, key=lambda x: x.get("alert_count", 0), reverse=True)[:20]
            ],
        },
        "d1_cross_reference": d1_xref,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Print summary
    print(f"\n{'='*80}")
    print("WORKSTREAM 2: PUMP CLUSTER ANALYSIS")
    print(f"{'='*80}")
    print(f"\nParsing: {len(signals)}/{len(raw_posts)} WhaleBotPumps posts parsed "
          f"({pump_count} pump, {dump_count} dump)")
    print(f"\nPump Clusters: {len(clusters)} total")
    print(f"  Outcome distribution:")
    for outcome, cnt in sorted(outcome_counts.items(), key=lambda x: -x[1]):
        pct = cnt / len(clusters) * 100 if clusters else 0
        print(f"    {outcome:<20}: {cnt:4d} ({pct:5.1f}%)")
    print(f"  Pump-and-dump rate (of classified): {pd_rate:.1f}%")
    print(f"  Viable short signal: {'YES' if pd_rate > 60 else 'NO'}")

    if peak_times:
        print(f"  Mean time to peak: {np.mean(peak_times):.1f}h (median {np.median(peak_times):.1f}h)")

    print(f"\nPredictor Analysis:")
    for outcome, stats in predictor_analysis.items():
        print(f"  {outcome}: n={stats['count']}, avg alerts={stats['mean_alert_count']}, "
              f"avg exchanges={stats['mean_exchange_count']}")

    print(f"\nD1 Cross-Reference: {d1_xref['total_d1_opps']} opportunities")
    cp = d1_xref["cluster_presence"]
    print(f"  With pump cluster:    n={cp['with_cluster']['count']:>5}, "
          f"PF={cp['with_cluster']['pf']:.3f}, WR={cp['with_cluster']['win_rate']:.1f}%")
    print(f"  Without pump cluster: n={cp['without_cluster']['count']:>5}, "
          f"PF={cp['without_cluster']['pf']:.3f}, WR={cp['without_cluster']['win_rate']:.1f}%")

    print(f"\n  Toxicity Quartiles (bot_toxicity_48h):")
    tq = d1_xref["toxicity_quartiles"]
    for qname in ["Q1 (0-25%)", "Q2 (25-50%)", "Q3 (50-75%)", "Q4 (75-100%)"]:
        q = tq.get(qname, {})
        print(f"    {qname}: n={q.get('count', 0):>5}, PF={q.get('pf', 0):.3f}, WR={q.get('win_rate', 0):.1f}%")

    esc = d1_xref["early_sl_correlation"]
    print(f"\n  Early SL Correlation:")
    print(f"    Winners mean tox_48h:  {esc.get('winners_mean_toxicity_48h')}")
    print(f"    Early SL mean tox_48h: {esc.get('early_sl_mean_toxicity_48h')}")
    if "p_value" in esc:
        sig = " **" if esc.get("significant_005") else ""
        print(f"    Mann-Whitney p={esc['p_value']:.6f}{sig}")

    print(f"\nOutput: {OUTPUT_PATH}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
