#!/usr/bin/env python3
"""
runner.py — Automated strategy execution for Bybit EU spot
============================================================

Runs the ridge-scored uptrend-pullback strategy on a 1h loop:
1. Fetch latest 1h OHLCV for 121 Bybit EU symbols
2. Compute features + regime indicator
3. If regime gate passes, score all symbols
4. Enter top-scoring symbols (market buy + stop-loss)
5. Manage exits (time stop at 8 bars, stop loss at 1.5×ATR)

Safety:
- Paper mode by default (--live required for real orders)
- Max position size as % of available balance
- Max concurrent positions enforced
- All actions logged to file
- Graceful shutdown on SIGINT/SIGTERM

Usage:
  # Paper trading (default):
  python3 -m trading.runner --config model_config.json

  # Live trading (requires explicit flag):
  python3 -m trading.runner --config model_config.json --live

  # With Docker:
  docker compose -f docker-compose.yml run --rm -T computers \\
      python3 -m trading.runner --config /backend/trading/model_config.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

# Resolve imports
if os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.bybit_client import BybitClient
from trading.ridge_model import (
    ModelConfig, load_config, compute_features_for_bar,
    score_bar, compute_regime, FEATURE_NAMES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("runner")

WHITELIST_PATHS = [
    "/backend/scripts/output/bybit_eu_spot_whitelist.txt",
    "Backend/scripts/output/bybit_eu_spot_whitelist.txt",
    "scripts/output/bybit_eu_spot_whitelist.txt",
]

# ── Position tracking ──

@dataclass
class Position:
    symbol: str
    bybit_symbol: str
    entry_time: datetime
    entry_price: float
    qty_base: float           # amount of base asset held
    qty_quote: float          # quote amount spent
    stop_price: float
    atr_at_entry: float
    bars_held: int = 0
    order_id: str = ""
    score_at_entry: float = 0.0


@dataclass
class TradeLog:
    symbol: str
    side: str
    entry_time: str
    exit_time: str | None
    entry_price: float
    exit_price: float | None
    qty_base: float
    pnl_quote: float | None
    exit_reason: str | None
    score: float
    regime_value: float


# ── Symbol mapping ──

def usdt_to_bybit_usdc(sym: str) -> str:
    """Convert our DB symbol (e.g. SOLUSDT) to Bybit USDC pair (e.g. SOLUSDC)."""
    if sym.endswith("USDT"):
        return sym[:-4] + "USDC"
    return sym


def load_whitelist() -> list[str]:
    for p in WHITELIST_PATHS:
        if os.path.isfile(p):
            with open(p) as f:
                return sorted([l.strip() for l in f if l.strip()])
    logger.error("No whitelist found at any of: %s", WHITELIST_PATHS)
    return []


# ── Main runner ──

class StrategyRunner:
    def __init__(self, client: BybitClient, config: ModelConfig,
                 max_concurrent: int = 15,
                 position_pct: float = 0.02,
                 min_order_usdc: float = 1.0):
        self.client = client
        self.config = config
        self.max_concurrent = max_concurrent
        self.position_pct = position_pct  # % of balance per position
        self.min_order_usdc = min_order_usdc
        self.positions: dict[str, Position] = {}  # symbol -> Position
        self.trade_log: list[dict] = []
        self.symbols = load_whitelist()
        self.running = True
        self._ohlcv_cache: dict[str, dict] = {}  # symbol -> {arrays}
        self._regime_history: list[float] = []  # rolling 24h regime values

        # Graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, *_):
        logger.info("Shutdown signal received")
        self.running = False

    def run(self):
        """Main loop — runs every hour aligned to bar close."""
        logger.info("=" * 60)
        logger.info("STRATEGY RUNNER STARTED")
        logger.info("  mode: %s", "PAPER" if self.client.paper else "*** LIVE ***")
        logger.info("  symbols: %d", len(self.symbols))
        logger.info("  max concurrent: %d", self.max_concurrent)
        logger.info("  position size: %.1f%% of balance", self.position_pct * 100)
        logger.info("  stop: %.1f×ATR, time stop: %d bars",
                     self.config.stop_mult, self.config.time_stop_bars)
        logger.info("  regime threshold: %.4f", self.config.regime_threshold)
        logger.info("=" * 60)

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.exception("Tick failed: %s", e)

            # Sleep until next hour + 5 minutes (wait for bar close + propagation)
            now = datetime.now(timezone.utc)
            next_hour = now.replace(minute=5, second=0, microsecond=0)
            if next_hour <= now:
                next_hour += timedelta(hours=1)
            wait = (next_hour - now).total_seconds()
            logger.info("Next tick at %s (%.0fs)", next_hour.strftime("%H:%M"), wait)

            # Sleep in small increments for graceful shutdown
            end = time.time() + wait
            while time.time() < end and self.running:
                time.sleep(min(10, end - time.time()))

        # Shutdown: log final state
        logger.info("Runner stopped. %d open positions, %d trades logged",
                     len(self.positions), len(self.trade_log))
        self._save_log()

    def _tick(self):
        """One strategy cycle."""
        now = datetime.now(timezone.utc)
        logger.info("─── TICK %s ───", now.strftime("%Y-%m-%d %H:%M"))

        # 1. Fetch OHLCV
        self._refresh_ohlcv()

        # 2. Compute regime
        regime_value = self._compute_regime()
        self._regime_history.append(regime_value)
        # Smooth: 24h rolling mean
        if len(self._regime_history) > 24:
            smoothed_regime = float(np.mean(self._regime_history[-24:]))
        else:
            smoothed_regime = regime_value

        regime_pass = smoothed_regime > self.config.regime_threshold
        logger.info("  regime: raw=%.4f smoothed=%.4f thresh=%.4f → %s",
                     regime_value, smoothed_regime, self.config.regime_threshold,
                     "PASS" if regime_pass else "FLAT")

        # 3. Manage existing positions (exits)
        self._manage_exits(now)

        # 4. If regime passes, score and enter
        if regime_pass:
            self._score_and_enter(now)
        else:
            logger.info("  regime gate: sitting out this bar")

        logger.info("  positions: %d open", len(self.positions))

    def _refresh_ohlcv(self):
        """Fetch latest 1h bars for all symbols from Bybit."""
        logger.info("  fetching OHLCV for %d symbols...", len(self.symbols))
        for sym in self.symbols:
            bybit_sym = usdt_to_bybit_usdc(sym)
            try:
                candles = self.client.get_klines(bybit_sym, interval="60", limit=200)
                if candles:
                    self._ohlcv_cache[sym] = {
                        "open": np.array([c["open"] for c in candles]),
                        "high": np.array([c["high"] for c in candles]),
                        "low": np.array([c["low"] for c in candles]),
                        "close": np.array([c["close"] for c in candles]),
                        "volume": np.array([c["volume"] for c in candles]),
                        "ts": np.array([c["ts"] for c in candles]),
                        "hours": np.array([
                            datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc).hour
                            for c in candles], dtype=np.float64),
                    }
            except Exception as e:
                logger.debug("OHLCV fetch failed for %s: %s", sym, e)
        logger.info("  OHLCV: %d/%d symbols cached", len(self._ohlcv_cache), len(self.symbols))

    def _compute_regime(self) -> float:
        """Cross-sectional mean 24h return."""
        ret24h = {}
        for sym, bars in self._ohlcv_cache.items():
            c = bars["close"]
            if len(c) >= 25:
                ret24h[sym] = float(c[-1] / c[-25] - 1)
        return compute_regime(ret24h)

    def _manage_exits(self, now: datetime):
        """Check stop loss and time stop for all open positions."""
        to_close = []
        for sym, pos in self.positions.items():
            pos.bars_held += 1
            bars = self._ohlcv_cache.get(sym)

            # Time stop
            if pos.bars_held >= self.config.time_stop_bars:
                to_close.append((sym, "TIME_STOP"))
                continue

            # Stop loss check (last bar's low)
            if bars and len(bars["low"]) > 0:
                last_low = bars["low"][-1]
                if last_low <= pos.stop_price:
                    to_close.append((sym, "STOP_LOSS"))
                    continue

        for sym, reason in to_close:
            self._close_position(sym, reason)

    def _score_and_enter(self, now: datetime):
        """Score all symbols, enter top-scoring ones."""
        scores = {}
        for sym in self.symbols:
            if sym in self.positions:
                continue  # already in position
            bars = self._ohlcv_cache.get(sym)
            if not bars or len(bars["close"]) < 200:
                continue
            feats = compute_features_for_bar(
                bars["close"], bars["high"], bars["low"],
                bars["volume"], bars["hours"])
            s = score_bar(feats, -1, self.config)  # score latest bar
            if s is not None:
                scores[sym] = (s, feats)

        if not scores:
            logger.info("  no valid scores")
            return

        # Select entries above threshold
        all_scores = np.array([s for s, _ in scores.values()])
        selected = {sym: (s, f) for sym, (s, f) in scores.items()
                    if s >= self.config.score_threshold}

        logger.info("  scored %d symbols, %d above threshold (%.4f)",
                     len(scores), len(selected), self.config.score_threshold)

        # Rank by score, fill available slots
        ranked = sorted(selected.items(), key=lambda x: x[1][0], reverse=True)
        available_slots = self.max_concurrent - len(self.positions)

        if available_slots <= 0:
            logger.info("  at max concurrent (%d), no new entries", self.max_concurrent)
            return

        # Get balance for position sizing
        balance = self.client.get_wallet_balance("USDC")
        avail = balance["available"]
        pos_size = avail * self.position_pct

        if pos_size < self.min_order_usdc:
            logger.warning("  position size $%.2f below minimum $%.2f",
                          pos_size, self.min_order_usdc)
            return

        entered = 0
        for sym, (score, feats) in ranked[:available_slots]:
            atr = feats["atr_raw"][-1]
            last_close = feats["close"][-1]
            stop_price = last_close - self.config.stop_mult * atr
            bybit_sym = usdt_to_bybit_usdc(sym)

            # Place order
            order = self.client.market_buy(
                bybit_sym,
                qty=f"{pos_size:.2f}",
                sl_price=f"{stop_price:.6f}",
            )

            if order:
                est_qty_base = pos_size / last_close if last_close > 0 else 0
                self.positions[sym] = Position(
                    symbol=sym,
                    bybit_symbol=bybit_sym,
                    entry_time=now,
                    entry_price=last_close,
                    qty_base=est_qty_base,
                    qty_quote=pos_size,
                    stop_price=stop_price,
                    atr_at_entry=atr,
                    bars_held=0,
                    order_id=order.order_id,
                    score_at_entry=score,
                )
                entered += 1
                logger.info("  ENTER %s score=%.4f price=%.4f stop=%.4f size=$%.2f",
                           sym, score, last_close, stop_price, pos_size)

        logger.info("  entered %d new positions", entered)

    def _close_position(self, symbol: str, reason: str):
        """Close a position and log the trade."""
        pos = self.positions.pop(symbol, None)
        if not pos:
            return

        bars = self._ohlcv_cache.get(symbol)
        exit_price = bars["close"][-1] if bars and len(bars["close"]) > 0 else pos.entry_price

        # Place sell order
        qty_str = f"{pos.qty_base:.8f}"
        order = self.client.market_sell(pos.bybit_symbol, qty_str)

        pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.qty_quote
        self.trade_log.append({
            "symbol": symbol,
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl_quote": round(pnl, 4),
            "exit_reason": reason,
            "bars_held": pos.bars_held,
            "score": pos.score_at_entry,
        })

        logger.info("  EXIT %s reason=%s bars=%d entry=%.4f exit=%.4f pnl=$%.4f",
                     symbol, reason, pos.bars_held, pos.entry_price, exit_price, pnl)

    def _save_log(self):
        """Persist trade log to disk."""
        log_dir = Path("/backend/scripts/output") if os.path.isdir("/backend") else Path("output")
        log_dir.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"trade_log_{ts}.json"
        with open(path, "w") as f:
            json.dump(self.trade_log, f, indent=2)
        logger.info("Trade log saved: %s (%d trades)", path, len(self.trade_log))


def main():
    ap = argparse.ArgumentParser(description="Bybit EU spot strategy runner")
    ap.add_argument("--config", required=True, help="Path to model_config.json")
    ap.add_argument("--live", action="store_true",
                    help="Enable LIVE trading (default: paper)")
    ap.add_argument("--max-concurrent", type=int, default=15)
    ap.add_argument("--position-pct", type=float, default=0.02,
                    help="Position size as fraction of available balance (default 2%%)")
    ap.add_argument("--min-order", type=float, default=1.0,
                    help="Minimum order size in USDC")
    args = ap.parse_args()

    # API keys from env
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    if not api_key or not api_secret:
        logger.error("BYBIT_API_KEY and BYBIT_API_SECRET must be set")
        sys.exit(1)

    config = load_config(args.config)
    paper = not args.live

    if not paper:
        logger.warning("=" * 60)
        logger.warning("  *** LIVE TRADING MODE ***")
        logger.warning("  Real orders will be placed on Bybit")
        logger.warning("=" * 60)

    client = BybitClient(api_key, api_secret, paper=paper)

    # Verify connectivity
    bal = client.get_wallet_balance("USDC")
    logger.info("Account balance: %.2f USDC (available: %.2f)",
                bal["total"], bal["available"])

    runner = StrategyRunner(
        client=client,
        config=config,
        max_concurrent=args.max_concurrent,
        position_pct=args.position_pct,
        min_order_usdc=args.min_order,
    )
    runner.run()


if __name__ == "__main__":
    main()
