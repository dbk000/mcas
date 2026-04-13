"""
Signal Engine
=============

Orchestrates:
1. OHLCV ingestion from exchange
2. Feature Factory (indicator computation)
3. Setup evaluation (S1, S2, ...)
4. Signal ranking and queue management

This is the "brain" that processes markets and produces ranked signals.
"""

import time
import json
import random
import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Union
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from governance.bootstrap import get_config, strategy_as_api_dict, strategy_active_setup_ids
from governance.json_logging import ctx_symbol
from indicators.indicators import FeatureFactory, IndicatorSnapshot, OHLCV
from core_logic.setups import S9Evaluator, S9Signal, S9State
from core_logic.setups import S10Evaluator, S10Signal
from governance.data_layer import log_signal_event
from governance.regime import fetch_fear_greed_index

logger = logging.getLogger(__name__)


def _bar_start_dt(snapshot: IndicatorSnapshot) -> datetime:
    """Extract bar_start_ts as UTC datetime from the last candle in the snapshot."""
    ts_ms = snapshot.ohlcv[-1].timestamp
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


# Concurrency settings
MAX_WORKERS = 10  # Parallel OHLCV fetches (reduced from 20 for rate limit compliance)
COMPUTE_WORKERS = 4  # Parallel indicator compute threads (Phase 2A)
BATCH_SIZE = 30   # Symbols per batch to avoid overwhelming Binance
BATCH_DELAY_S = 1.0  # Delay between batches
MAX_RETRIES = 3   # Retry attempts for rate-limited requests
MAX_RETRY_WAIT_S = 30  # Cap per-retry wait time
def _symbol_quality_filter() -> bool:
    """Read SYMBOL_QUALITY_FILTER from frozen ConfigRegistry."""
    return get_config().Worker.SYMBOL_QUALITY_FILTER


@dataclass
class RankedSignal:
    """Unified signal output for ranking."""
    symbol: str
    timeframe: str
    setup_id: str  # "S1".."S7"
    direction: str  # "long" or "short"
    setup_score: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    r_value: float
    timestamp: int
    
    # Full signal payload (for storage)
    payload: dict


class OHLCVCache:
    """
    In-memory cache for OHLCV data with per-key thread safety.
    Key: (symbol, timeframe)
    Value: list of OHLCV candles
    """

    def __init__(self, max_bars: int = 500):
        self.max_bars = max_bars
        self._cache: dict[tuple[str, str], list[OHLCV]] = {}
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._master_lock = threading.Lock()

    def _get_lock(self, key: tuple[str, str]) -> threading.Lock:
        if key not in self._locks:
            with self._master_lock:
                if key not in self._locks:
                    self._locks[key] = threading.Lock()
        return self._locks[key]

    def get(self, symbol: str, tf: str) -> Optional[list[OHLCV]]:
        key = (symbol, tf)
        with self._get_lock(key):
            return self._cache.get(key)

    def set(self, symbol: str, tf: str, candles: list[OHLCV]):
        key = (symbol, tf)
        with self._get_lock(key):
            self._cache[key] = candles[-self.max_bars:]

    def append(self, symbol: str, tf: str, candle: OHLCV):
        key = (symbol, tf)
        with self._get_lock(key):
            if key not in self._cache:
                self._cache[key] = []
            self._cache[key].append(candle)
            if len(self._cache[key]) > self.max_bars:
                self._cache[key] = self._cache[key][-self.max_bars:]


class IndicatorCache:
    """
    Cache for computed indicator values (for percentile calculations).
    """
    
    def __init__(self, max_history: int = 200):
        self.max_history = max_history
        self._bb_width: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._ma_gap: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._vwap_distance: dict[tuple[str, str], list[float]] = defaultdict(list)
    
    def add_bb_width(self, symbol: str, tf: str, value: float):
        key = (symbol, tf)
        self._bb_width[key].append(value)
        if len(self._bb_width[key]) > self.max_history:
            self._bb_width[key] = self._bb_width[key][-self.max_history:]
    
    def get_bb_width_history(self, symbol: str, tf: str) -> list[float]:
        return self._bb_width.get((symbol, tf), [])
    
    def add_ma_gap(self, symbol: str, tf: str, value: float):
        key = (symbol, tf)
        self._ma_gap[key].append(value)
        if len(self._ma_gap[key]) > self.max_history:
            self._ma_gap[key] = self._ma_gap[key][-self.max_history:]
    
    def get_ma_gap_history(self, symbol: str, tf: str) -> list[float]:
        return self._ma_gap.get((symbol, tf), [])

    def add_vwap_distance(self, symbol: str, tf: str, value: float):
        key = (symbol, tf)
        self._vwap_distance[key].append(abs(value))
        if len(self._vwap_distance[key]) > self.max_history:
            self._vwap_distance[key] = self._vwap_distance[key][-self.max_history:]

    def get_vwap_distance_history(self, symbol: str, tf: str) -> list[float]:
        return self._vwap_distance.get((symbol, tf), [])


class BinanceClient:
    """
    Simple Binance REST client for OHLCV data.
    """
    
    # Timeframe mapping
    TF_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
    
    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
    ) -> list[OHLCV]:
        """
        Fetch OHLCV candles from Binance with retry on rate limits.

        Returns list of OHLCV, oldest first.
        """
        url = get_config().Worker.BINANCE_KLINES_URL
        params = {
            "symbol": symbol,
            "interval": self.TF_MAP.get(interval, interval),
            "limit": limit,
        }

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                if resp.status_code in (418, 429):
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    wait = max(retry_after, min(2 ** attempt + random.uniform(0, 1),
                                                  MAX_RETRY_WAIT_S))
                    logger.warning("Binance rate limit, retrying", extra={
                        "event": "rate_limit_retry", "symbol": symbol,
                        "interval": interval, "attempt": attempt + 1,
                        "wait_s": round(wait, 1),
                    })
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break  # Success
            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt + random.uniform(0, 0.5))
                    continue
                logger.warning("Binance timeout after retries", extra={
                    "event": "timeout", "symbol": symbol, "interval": interval,
                })
                return []
            except requests.exceptions.HTTPError as e:
                logger.error("Binance API error", extra={
                    "event": "api_error", "symbol": symbol, "interval": interval,
                    "http_status": e.response.status_code if e.response is not None else None,
                })
                return []
            except Exception as e:
                logger.error("Binance fetch failed", extra={
                    "event": "api_error", "symbol": symbol, "interval": interval,
                    "error": str(e),
                })
                return []
        else:
            # All retries exhausted
            logger.warning("Binance retries exhausted", extra={
                "event": "retries_exhausted", "symbol": symbol, "interval": interval,
            })
            return []

        candles = []
        for k in data:
            candles.append(OHLCV(
                timestamp=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            ))

        return candles


class SignalEngine:
    """
    Main engine that processes markets and produces ranked signals.
    
    Usage:
        engine = SignalEngine(symbols=["BTCUSDT", "ETHUSDT"], timeframes=["1h", "4h"])
        signals = engine.scan()  # Returns list of RankedSignal, sorted by score
    """
    
    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        top_n: int = None,
        source: str = "rest",
        redis_client=None,
        run_id: str = None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.top_n = top_n
        self.source = source
        self.redis_client = redis_client
        self.run_id = run_id

        # Components
        self.client = BinanceClient()
        self.feature_factory = FeatureFactory()
        self.ohlcv_cache = OHLCVCache(max_bars=500)
        self.indicator_cache = IndicatorCache(max_history=200)

        # Thread-safety: protects indicator_cache mutations during parallel compute
        self._cache_lock = threading.Lock()

        # Symbol quality filter
        self._symbol_quality_filter_enabled = _symbol_quality_filter()
        self._excluded_symbols: set[str] = set()
        if self._symbol_quality_filter_enabled and self.redis_client:
            try:
                members = self.redis_client.smembers("symbol:excluded:quality")
                self._excluded_symbols = set(members) if members else set()
            except Exception:
                pass
    
    def refresh_ohlcv(self, symbol: str, tf: str) -> bool:
        """
        Fetch latest candles and update cache.
        Returns True if successful.
        """
        if self.source == "redis" and self.redis_client:
            from governance.cache_contract import get_candles_as_ohlcv
            candles = get_candles_as_ohlcv(self.redis_client, symbol, tf)
            if candles and len(candles) >= 201:
                self.ohlcv_cache.set(symbol, tf, candles)
                return True
            return False

        candles = self.client.fetch_klines(symbol, tf, limit=300)
        if not candles:
            return False

        self.ohlcv_cache.set(symbol, tf, candles)
        return True
    
    def compute_indicators(self, symbol: str, tf: str) -> Optional[IndicatorSnapshot]:
        """
        Compute indicators for symbol/timeframe.
        Returns IndicatorSnapshot or None if insufficient data.
        """
        candles = self.ohlcv_cache.get(symbol, tf)
        if not candles or len(candles) < 201:
            return None
        
        # Get HTF candles if available (e.g., 4h for 1h)
        htf_map = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "4h", "4h": "1d"}
        htf = htf_map.get(tf)
        htf_candles = None
        if htf:
            htf_candles = self.ohlcv_cache.get(symbol, htf)
        
        try:
            snapshot = self.feature_factory.compute(candles, htf_candles)

            # THREAD-SAFETY: only self.indicator_cache mutations here (protected by _cache_lock)
            with self._cache_lock:
                self.indicator_cache.add_bb_width(symbol, tf, snapshot.bb_width)
                self.indicator_cache.add_ma_gap(symbol, tf, snapshot.ma_gap_atr)
                if snapshot.vwap_distance_atr is not None:
                    self.indicator_cache.add_vwap_distance(symbol, tf, snapshot.vwap_distance_atr)

            return snapshot
        except Exception as e:
            logger.error(f"Indicator compute failed for {symbol} {tf}: {e}")
            return None
    
    def evaluate_setups(
        self,
        symbol: str,
        tf: str,
        snapshot: IndicatorSnapshot,
    ) -> list:
        """
        Run per-symbol setup evaluators and return any signals that triggered.

        S1-S8 archived. S9 is cross-sectional (evaluated separately).
        S10 is per-symbol MACD scalp on 5m timeframe.
        """
        signals = []

        # S10: MACD Scalp (5m only)
        if tf == "5m":
            try:
                s10 = S10Evaluator()
                state = s10.compute_state(snapshot)
                sig = s10.evaluate_trigger(snapshot, state, symbol, tf)
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                logger.debug("S10 eval error %s %s: %s", symbol, tf, e)

        return signals
    
    @staticmethod
    def _get_setup_name(setup_id: str) -> str:
        """Get setup display name from the sole authority (frozen StrategyRegistry)."""
        catalog = strategy_as_api_dict()
        info = catalog.get(setup_id, {})
        return info.get("label", f"{setup_id} (unknown)")

    @staticmethod
    def _active_setup_ids() -> list[str]:
        """Get active setup IDs from the sole authority (frozen StrategyRegistry)."""
        return strategy_active_setup_ids()

    def signal_to_ranked(self, signal) -> RankedSignal:
        """Convert setup-specific signal to unified RankedSignal."""
        now_ts = int(time.time())

        setup_id = signal.setup_id
        payload = {
            "setup_id": setup_id,
            "setup_name": self._get_setup_name(setup_id),
            "why_items": signal.explain.why_items if signal.explain else [],
            "time_stop_bars": signal.time_stop_bars,
        }

        if setup_id == "S9":
            payload["state"] = {
                "ret_4h": signal.ret_4h,
                "ret_24h": signal.ret_24h,
                "breadth": signal.breadth,
                "rank": signal.rank,
                "universe_size": signal.universe_size,
                "decile_size": signal.decile_size,
            }
            payload["score_components"] = {
                "rank_score": signal.rank_score,
                "breadth_score": signal.breadth_score,
                "magnitude_score": signal.magnitude_score,
            }
        elif setup_id == "S10":
            payload["state"] = {
                "macd_histogram": signal.macd_histogram,
                "rvol": signal.rvol,
                "sma_20": signal.sma_20,
                "flat_top_level": signal.flat_top_level,
                "trend_bias": signal.trend_bias,
            }
            payload["score_components"] = signal.score_components

        return RankedSignal(
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            setup_id=setup_id,
            direction=signal.direction.value,
            setup_score=signal.setup_score,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            r_value=signal.r_value,
            timestamp=now_ts,
            payload=payload,
        )
    
    def scan(self) -> list[RankedSignal]:
        """
        Scan all symbols/timeframes and return ranked signals.

        Uses concurrent fetching for efficiency at scale.
        200 symbols × 6 TFs = 1200 pairs, batched and parallelized.

        Returns:
            List of RankedSignal, sorted by setup_score descending, limited to top_n.
        """
        # Fast path: read pre-computed signals from Redis ZSETs
        if self.source == "redis" and self.redis_client:
            from governance.cache_contract import get_top_signals, is_cache_ready, check_staleness
            if not is_cache_ready(self.redis_client):
                logger.warning("Cache not ready, falling back to REST")
                self.source = "rest"
            else:
                from governance.bootstrap import macro_reference_symbol
                s = check_staleness(self.redis_client, macro_reference_symbol(), "1m")
                if s["stale"]:
                    logger.warning(f"Cache stale ({s['age_s']:.0f}s), falling back to REST")
                    self.source = "rest"

        if self.source == "redis" and self.redis_client:
            from governance.cache_contract import get_top_signals, is_cache_ready
            if is_cache_ready(self.redis_client):
                cached = get_top_signals(
                    self.redis_client,
                    setup_ids=self._active_setup_ids(),
                    timeframes=self.timeframes,
                    top_n=self.top_n or 9999,
                )
                if cached:
                    logger.info("Scan using pre-computed Redis signals", extra={
                        "event": "scan_redis_fast_path",
                        "signal_count": len(cached),
                    })
                    # Convert cached dicts to RankedSignal
                    ranked = []
                    for c in cached:
                        payload = c.get("payload", {})
                        ranked.append(RankedSignal(
                            symbol=c["symbol"],
                            timeframe=c["timeframe"],
                            setup_id=c["setup_id"],
                            direction=payload.get("direction", "long"),
                            setup_score=c["setup_score"],
                            entry_price=payload.get("entry_price", 0),
                            stop_price=payload.get("stop_price", 0),
                            tp1_price=payload.get("tp1_price", 0),
                            tp2_price=payload.get("tp2_price", 0),
                            r_value=payload.get("r_value", 0),
                            timestamp=int(c.get("as_of_ts", time.time())),
                            payload=payload,
                        ))
                    return ranked
            # Fall through to compute from Redis candles if no pre-computed signals

        all_signals: list[RankedSignal] = []

        # Build list of all (symbol, tf) pairs to fetch
        pairs = [(symbol, tf) for symbol in self.symbols for tf in self.timeframes]
        logger.info("Scan starting", extra={
            "event": "scan_start", "pair_count": len(pairs),
            "symbol_count": len(self.symbols),
            "timeframe_count": len(self.timeframes),
        })
        
        # Phase 1: Concurrent OHLCV fetching
        fetch_start = time.time()
        successful_pairs = []
        
        def fetch_pair(pair):
            symbol, tf = pair
            try:
                if self.refresh_ohlcv(symbol, tf):
                    return (symbol, tf, True)
                return (symbol, tf, False)
            except Exception as e:
                logger.debug(f"Fetch error {symbol} {tf}: {e}")
                return (symbol, tf, False)
        
        # Process in batches with inter-batch delay to respect rate limits
        for batch_start in range(0, len(pairs), BATCH_SIZE):
            batch = pairs[batch_start:batch_start + BATCH_SIZE]
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fetch_pair, pair): pair for pair in batch}
                for future in as_completed(futures):
                    symbol, tf, success = future.result()
                    if success:
                        successful_pairs.append((symbol, tf))
            # Inter-batch delay to respect Binance rate limits
            if batch_start + BATCH_SIZE < len(pairs):
                time.sleep(BATCH_DELAY_S)
        
        fetch_time = round(time.time() - fetch_start, 1)
        logger.info("OHLCV fetch complete", extra={
            "event": "ohlcv_fetched",
            "fetched": len(successful_pairs), "total": len(pairs),
            "failed": len(pairs) - len(successful_pairs),
            "duration_s": fetch_time,
        })
        
        # Phase 2A: Parallel indicator computation (CPU-bound, stateless per pair)
        compute_start = time.time()

        def _compute_one(pair):
            symbol, tf = pair
            return (symbol, tf, self.compute_indicators(symbol, tf))

        # Collect results — main thread builds dict from future results (no concurrent writes)
        snapshots = {}
        with ThreadPoolExecutor(max_workers=COMPUTE_WORKERS) as pool:
            futures = [pool.submit(_compute_one, pair) for pair in successful_pairs]
            for future in as_completed(futures):
                try:
                    symbol, tf, snapshot = future.result()
                    if snapshot is not None:
                        snapshots[(symbol, tf)] = snapshot
                except Exception as e:
                    logger.debug(f"Compute error: {e}")

        compute_time = round(time.time() - compute_start, 1)
        logger.info("Indicator compute complete", extra={
            "event": "indicators_computed",
            "snapshot_count": len(snapshots), "duration_s": compute_time,
        })

        # Phase 2B: Sequential setup evaluation — STABLE ORDER from successful_pairs
        # Iterates successful_pairs (not snapshots.items()) to guarantee deterministic
        # evaluation order for tie-breaking in ranking/scoring.
        eval_start = time.time()
        for symbol, tf in successful_pairs:
            if self._symbol_quality_filter_enabled and symbol in self._excluded_symbols:
                continue
            snapshot = snapshots.get((symbol, tf))
            if snapshot is None:
                continue
            token = ctx_symbol.set(symbol)
            try:
                signals = self.evaluate_setups(symbol, tf, snapshot)
                for sig in signals:
                    ranked = self.signal_to_ranked(sig)
                    all_signals.append(ranked)
            except Exception as e:
                logger.debug("Evaluate error", extra={
                    "symbol": symbol, "timeframe": tf, "error": str(e),
                })
            finally:
                ctx_symbol.reset(token)

        # Phase 2C: S9 cross-sectional evaluation (needs all symbols' data)
        s9_signals = self._evaluate_s9_cross_sectional(snapshots, successful_pairs)
        for sig in s9_signals:
            ranked = self.signal_to_ranked(sig)
            all_signals.append(ranked)

        eval_time = round(time.time() - eval_start, 1)
        logger.info("Setup evaluation complete", extra={
            "event": "setups_evaluated",
            "signal_count": len(all_signals), "duration_s": eval_time,
        })

        # Sort by score descending
        all_signals.sort(key=lambda s: s.setup_score, reverse=True)
        
        # One position per asset: keep only highest-scoring signal per symbol
        seen_symbols: set[str] = set()
        deduplicated: list[RankedSignal] = []
        for sig in all_signals:
            if sig.symbol not in seen_symbols:
                deduplicated.append(sig)
                seen_symbols.add(sig.symbol)
        
        # Canon §1.9: log RANK_CHANGE for each signal
        top_n_signals = deduplicated[:self.top_n] if self.top_n else deduplicated
        for rank_idx, sig in enumerate(top_n_signals, start=1):
            try:
                bar_dt = datetime.fromtimestamp(sig.timestamp, tz=timezone.utc)
                log_signal_event(
                    event_type="RANK_CHANGE",
                    symbol=sig.symbol,
                    timeframe=sig.timeframe,
                    setup_id=sig.setup_id,
                    direction=sig.direction,
                    bar_start_ts=bar_dt,
                    setup_score=sig.setup_score,
                    rank=rank_idx,
                    score_components=sig.payload.get("score_components"),
                    reason_codes=sig.payload.get("why_items"),
                )
            except Exception:
                logger.debug("RANK_CHANGE log failed for %s", sig.symbol, exc_info=True)

        total_time = round(time.time() - fetch_start, 1)
        logger.info("Scan complete", extra={
            "event": "scan_complete",
            "unique_signals": len(deduplicated),
            "top_n": self.top_n,
            "total_duration_s": total_time,
        })

        # Return top N
        return top_n_signals
    
    def _evaluate_s9_cross_sectional(self, snapshots, successful_pairs) -> list:
        """Cross-sectional S9 evaluation: short top-decile by ret_4h when breadth > 0.3.

        Runs after all per-symbol indicators are computed. Reads candle closes
        from cached OHLCV to compute 4h returns, market breadth, and rankings.
        """
        s9_evaluator = S9Evaluator()

        # Collect 1h candle data for each symbol
        symbol_data = {}
        for symbol, tf in successful_pairs:
            if tf != "1h":
                continue
            if self._symbol_quality_filter_enabled and symbol in self._excluded_symbols:
                continue
            candles = self.ohlcv_cache.get(symbol, "1h")
            if not candles or len(candles) < 25:
                continue
            close_now = candles[-1].close
            close_4h = candles[-5].close if len(candles) >= 5 else None
            close_24h = candles[-25].close if len(candles) >= 25 else None
            if (close_now and close_now > 0 and close_4h and close_4h > 0
                    and close_24h and close_24h > 0):
                ret_4h = close_now / close_4h - 1
                ret_24h = close_now / close_24h - 1
                symbol_data[symbol] = (ret_4h, ret_24h, close_now, candles[-1].timestamp)

        if len(symbol_data) < 20:
            return []

        # Compute market breadth
        n_positive = sum(1 for _, (_, r24, _, _) in symbol_data.items() if r24 > 0)
        breadth = n_positive / len(symbol_data)

        s9_cfg = get_config().S9
        if breadth <= s9_cfg.BREADTH_THRESHOLD:
            logger.debug("S9: breadth=%.3f <= threshold, skipping", breadth)
            return []

        # Fear & Greed regime gate (fail-closed)
        if s9_cfg.FNG_GATE_ENABLED:
            fng = fetch_fear_greed_index()
            fng_value = fng.get("value")
            if fng_value is None:
                logger.warning("S9 REST: FnG unavailable — suppressing signals (fail-closed)")
                return []
            if fng_value > s9_cfg.FNG_MAX_VALUE:
                logger.info("S9 REST: FnG=%d (>%d) — regime gate suppressing signals",
                            fng_value, s9_cfg.FNG_MAX_VALUE)
                return []

        # Rank by ret_4h descending, select top decile
        sorted_syms = sorted(symbol_data.items(), key=lambda x: x[1][0], reverse=True)
        decile_n = max(1, len(sorted_syms) // 10)
        top_decile = sorted_syms[:decile_n]

        # Magnitude gate: filter out coins with ret_4h below threshold
        min_ret = s9_cfg.MIN_RET_4H
        pre_filter_count = len(top_decile)
        top_decile = [(sym, rets) for sym, rets in top_decile if rets[0] >= min_ret]
        if not top_decile:
            logger.info("S9 REST: breadth OK but all %d top-decile ret_4h < %.1f%% — no outliers",
                        pre_filter_count, min_ret * 100)
            return []

        state = S9State(
            breadth=breadth,
            breadth_passes=True,
            universe_size=len(symbol_data),
            top_decile_threshold=top_decile[-1][1][0] if top_decile else 0.0,
            top_decile_count=decile_n,
        )

        signals = []
        for rank, (symbol, (ret_4h, ret_24h, close, bar_ts_ms)) in enumerate(top_decile):
            sig = S9Evaluator.build_signal(
                symbol=symbol, close=close,
                ret_4h=ret_4h, ret_24h=ret_24h,
                breadth=breadth, rank=rank,
                universe_size=len(symbol_data),
                decile_size=decile_n, state=state,
                bar_start_ts=str(bar_ts_ms),
            )
            signals.append(sig)

        logger.info("S9 cross-sectional: breadth=%.3f, %d/%d symbols → %d shorts",
                     breadth, decile_n, len(symbol_data), len(signals))
        return signals

    def scan_symbol(self, symbol: str, tf: str) -> Optional[RankedSignal]:
        """
        Scan a single symbol/timeframe.
        Returns the highest-scoring signal if any triggered.
        """
        if not self.refresh_ohlcv(symbol, tf):
            return None
        
        snapshot = self.compute_indicators(symbol, tf)
        if snapshot is None:
            return None
        
        signals = self.evaluate_setups(symbol, tf, snapshot)
        if not signals:
            return None
        
        # Return highest scoring
        best = max(signals, key=lambda s: s.setup_score)
        return self.signal_to_ranked(best)


def get_default_symbols() -> list[str]:
    """DEPRECATED: fallback only. Ingestor builds universe dynamically from Binance.

    Use Redis `universe:monitored` or `universe:known` SETs for the live universe.
    This hardcoded list is retained only as a last-resort fallback if Binance REST
    is unreachable during startup.
    """
    return [
        # Tier 1 - Majors
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        # Tier 2 - Large caps
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "ETCUSDT",
        "XLMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
        "FILUSDT", "LDOUSDT", "AAVEUSDT", "MKRUSDT", "GRTUSDT",
        "INJUSDT", "IMXUSDT", "STXUSDT", "RUNEUSDT", "EGLDUSDT",
        "THETAUSDT", "AXSUSDT", "SANDUSDT", "MANAUSDT", "GALAUSDT",
        "APEUSDT", "CHZUSDT", "FLOWUSDT", "ZILUSDT", "ENJUSDT",
        "BATUSDT", "ZECUSDT", "DASHUSDT", "NEOUSDT", "WAVESUSDT",
        "QNTUSDT", "KAVAUSDT", "MINAUSDT", "XTZUSDT", "SNXUSDT",
        # Tier 3 - Mid caps
        "ICPUSDT", "VETUSDT", "ALGOUSDT", "FTMUSDT", "HBARUSDT",
        "EOSUSDT", "XMRUSDT", "ARUSDT", "CAKEUSDT", "DYDXUSDT",
        "GMXUSDT", "PENDLEUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT",
        "BLURUSDT", "WLDUSDT", "FETUSDT", "AGIXUSDT", "OCEANUSDT",
        "RNDRUSDT", "KASUSDT", "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT",
        "BONKUSDT", "WIFUSDT", "BOMEUSDT", "MEMEUSDT", "TURBOUSDT",
        "ORDIUSDT", "SATSUSDT", "RATSUSDT", "1000SATSUSDT", "PYTHUSDT",
        "JUPUSDT", "WUSDT", "STRKUSDT", "MANTAUSDT", "ALTUSDT",
        "PIXELUSDT", "PORTALUSDT", "ACEUSDT", "XAIUSDT", "AIUSDT",
        "ONDOUSDT", "ENAUSDT", "ETHFIUSDT", "SAGAUSDT", "ZETAUSDT",
        # Tier 4 - Active mid/small caps
        "CFXUSDT", "CKBUSDT", "HOOKUSDT", "MAGICUSDT", "TOKENUSDT",
        "IDUSDT", "RDNTUSDT", "NTRNUSDT", "LQTYUSDT", "SSVUSDT",
        "ANKRUSDT", "IOTXUSDT", "SKLUSDT", "CELRUSDT", "ARPAUSDT",
        "CTSIUSDT", "BANDUSDT", "COTIUSDT", "STMXUSDT", "OGNUSDT",
        "NKNUSDT", "DGBUSDT", "SXPUSDT", "REEFUSDT", "DENTUSDT",
        "HOTUSDT", "WINUSDT", "BTTUSDT", "SCUSDT", "ZENUSDT",
        "ONTUSDT", "IOSTUSDT", "LSKUSDT", "RVNUSDT", "ICXUSDT",
        "COMPUSDT", "YFIUSDT", "CRVUSDT", "BALUSDT", "SUSHIUSDT",
        "1INCHUSDT", "CELOUSDT", "KSMUSDT", "ROSEUSDT", "GLMRUSDT",
        "MOVRUSDT", "AGLDUSDT", "LOOKSUSDT", "ENSUSDT", "CVXUSDT",
        # Tier 5 - Extended universe
        "API3USDT", "WOOUSDT", "JASMYUSDT", "GALUSDT", "DARUSDT",
        "GMTUSDT", "ACHUSDT", "LEVERUSDT", "AMBUSDT", "PHBUSDT",
        "LOOMUSDT", "OAXUSDT", "MDTUSDT", "KEYUSDT", "VOXELUSDT",
        "HIGHUSDT", "LITUSDT", "RSRUSDT", "UMAUSDT", "BADGERUSDT",
        "FISUSDT", "ALPACAUSDT", "QUICKUSDT", "REQUSDT", "PROMUSDT",
        "SUPERUSDT", "USTCUSDT", "LUNCUSDT", "LUNA2USDT", "ASTRUSDT",
        "AUCTIONUSDT", "BICOUSDT", "BLUEBIRDUSDT", "BONDUSDT", "BSWUSDT",
        "C98USDT", "CLVUSDT", "COSUSDT", "CTKUSDT", "DATAUSDT",
        "DEXEUSDT", "DFUSDT", "DOCKUSDT", "DREPUSDT", "DUSKUSDT",
        "EDUUSDT", "ELFUSDT", "EPXUSDT", "ERNUSDT", "FIDAUSDT",
    ]


def fetch_top_symbols_by_volume(n: int = 1024, quote: Optional[str] = None) -> list[str]:
    """
    Dynamically fetch top N symbols by 24h quote volume from Binance.
    Falls back to hardcoded list on error.

    Default N changed from 200 to KNOWN_UNIVERSE_SIZE (1024) to align
    with two-tier universe architecture (Canon §1.4C).
    """
    try:
        if quote is None:
            from governance.bootstrap import quote_currency as _qc
            quote = _qc()
        _ticker_base = get_config().Worker.BINANCE_TICKER_URL
        resp = requests.get(
            f"{_ticker_base}/24hr",
            timeout=15
        )
        resp.raise_for_status()
        tickers = resp.json()

        # Filter to pairs quoted in the current QUOTE_CURRENCY
        filtered = [
            t for t in tickers
            if t["symbol"].endswith(quote)
            and float(t["quoteVolume"]) > 0
        ]
        filtered.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)

        symbols = [t["symbol"] for t in filtered[:n]]
        logger.info("Fetched dynamic symbols", extra={
            "event": "dynamic_symbols", "count": len(symbols),
        })
        return symbols

    except requests.exceptions.Timeout:
        logger.warning("Dynamic symbols fetch timeout", extra={
            "event": "timeout", "endpoint": "ticker/24hr",
        })
        return get_default_symbols()[:n]
    except Exception as e:
        logger.warning("Dynamic symbols fetch failed, using defaults", extra={
            "event": "api_error", "endpoint": "ticker/24hr", "error": str(e),
        })
        return get_default_symbols()[:n]


def get_default_timeframes() -> list[str]:
    """All supported timeframes for full-scale scanning."""
    return ["1m", "5m", "15m", "1h", "4h", "1d"]
