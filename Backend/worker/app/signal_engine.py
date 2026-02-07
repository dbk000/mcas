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

import os
import time
import json
import random
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Union
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from json_logging import ctx_symbol
from indicators import FeatureFactory, IndicatorSnapshot, OHLCV
from setups import S1Evaluator, S1Signal, S2Evaluator, S2Signal
from data_layer import log_signal_event

logger = logging.getLogger(__name__)


def _bar_start_dt(snapshot: IndicatorSnapshot) -> datetime:
    """Extract bar_start_ts as UTC datetime from the last candle in the snapshot."""
    ts_ms = snapshot.ohlcv[-1].timestamp
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


# Concurrency settings
MAX_WORKERS = 10  # Parallel OHLCV fetches (reduced from 20 for rate limit compliance)
BATCH_SIZE = 30   # Symbols per batch to avoid overwhelming Binance
BATCH_DELAY_S = 1.0  # Delay between batches
MAX_RETRIES = 3   # Retry attempts for rate-limited requests
MAX_RETRY_WAIT_S = 30  # Cap per-retry wait time


@dataclass
class RankedSignal:
    """Unified signal output for ranking."""
    symbol: str
    timeframe: str
    setup_id: str  # "S1" or "S2"
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
    In-memory cache for OHLCV data.
    Key: (symbol, timeframe)
    Value: list of OHLCV candles
    """
    
    def __init__(self, max_bars: int = 500):
        self.max_bars = max_bars
        self._cache: dict[tuple[str, str], list[OHLCV]] = {}
    
    def get(self, symbol: str, tf: str) -> Optional[list[OHLCV]]:
        return self._cache.get((symbol, tf))
    
    def set(self, symbol: str, tf: str, candles: list[OHLCV]):
        # Trim to max_bars
        self._cache[(symbol, tf)] = candles[-self.max_bars:]
    
    def append(self, symbol: str, tf: str, candle: OHLCV):
        key = (symbol, tf)
        if key not in self._cache:
            self._cache[key] = []
        self._cache[key].append(candle)
        # Trim
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


class BinanceClient:
    """
    Simple Binance REST client for OHLCV data.
    """
    
    BASE_URL = "https://api.binance.com/api/v3"
    
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
        url = f"{self.BASE_URL}/klines"
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
                    wait = min(retry_after, 2 ** attempt + random.uniform(0, 1),
                               MAX_RETRY_WAIT_S)
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
                    "http_status": getattr(e.response, "status_code", None),
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
        top_n: int = 3,
        source: str = "rest",
        redis_client=None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.top_n = top_n
        self.source = source
        self.redis_client = redis_client

        # Components
        self.client = BinanceClient()
        self.feature_factory = FeatureFactory()
        self.ohlcv_cache = OHLCVCache(max_bars=500)
        self.indicator_cache = IndicatorCache(max_history=200)

        # Setup evaluators
        self.s1_evaluator = S1Evaluator()
        self.s2_evaluator = S2Evaluator()

        # State tracking (for S1 squeeze age)
        self._s1_prev_state: dict[tuple[str, str], any] = {}
    
    def refresh_ohlcv(self, symbol: str, tf: str) -> bool:
        """
        Fetch latest candles and update cache.
        Returns True if successful.
        """
        if self.source == "redis" and self.redis_client:
            from cache_contract import get_candles_as_ohlcv
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
            
            # Update indicator cache
            self.indicator_cache.add_bb_width(symbol, tf, snapshot.bb_width)
            self.indicator_cache.add_ma_gap(symbol, tf, snapshot.ma_gap_atr)
            
            return snapshot
        except Exception as e:
            logger.error(f"Indicator compute failed for {symbol} {tf}: {e}")
            return None
    
    def evaluate_setups(
        self,
        symbol: str,
        tf: str,
        snapshot: IndicatorSnapshot,
    ) -> list[Union[S1Signal, S2Signal]]:
        """
        Run all setup evaluators and return any signals that triggered.
        """
        signals = []
        
        # S1 - BB Squeeze ON
        bb_width_history = self.indicator_cache.get_bb_width_history(symbol, tf)
        prev_s1_state = self._s1_prev_state.get((symbol, tf))
        
        # Get BTC data for correlation filter
        btc_snapshot = None
        btc_bb_width_history = None
        if symbol != "BTCUSDT":
            btc_candles = self.ohlcv_cache.get("BTCUSDT", tf)
            if btc_candles and len(btc_candles) >= 201:
                try:
                    btc_snapshot = self.feature_factory.compute(btc_candles)
                    btc_bb_width_history = self.indicator_cache.get_bb_width_history("BTCUSDT", tf)
                except:
                    pass
        
        s1_state = self.s1_evaluator.compute_state(
            snapshot,
            bb_width_history,
            prev_s1_state,
            btc_snapshot,
            btc_bb_width_history,
        )
        self._s1_prev_state[(symbol, tf)] = s1_state
        
        s1_signal = self.s1_evaluator.evaluate_trigger(snapshot, s1_state, symbol, tf)
        if s1_signal:
            signals.append(s1_signal)
            # Canon §1.9: log SETUP_FIRED event
            try:
                log_signal_event(
                    event_type="SETUP_FIRED",
                    symbol=symbol,
                    timeframe=tf,
                    setup_id="S1",
                    direction=s1_signal.direction.value,
                    bar_start_ts=_bar_start_dt(snapshot),
                    setup_score=s1_signal.setup_score,
                    feature_snapshot={
                        "bb_width": snapshot.bb_width,
                        "bb_width_pctile": s1_state.bb_width_pctile,
                        "squeeze_age": s1_state.squeeze_age,
                        "bias_score": s1_state.bias_score,
                        "clv": snapshot.clv,
                        "volume_z": snapshot.volume_z,
                        "atr": snapshot.atr,
                    },
                    score_components={
                        "severity": s1_signal.severity,
                        "bias_strength": s1_signal.bias_strength,
                        "participation": s1_signal.participation,
                        "break_quality": s1_signal.break_quality,
                        "clean_close": s1_signal.clean_close,
                    },
                    reason_codes=s1_signal.explain.why_items if s1_signal.explain else None,
                )
            except Exception:
                logger.debug("S1 SETUP_FIRED log failed", exc_info=True)

        # S2 - Golden Cross Anticipation
        ma_gap_history = self.indicator_cache.get_ma_gap_history(symbol, tf)
        s2_state = self.s2_evaluator.compute_state(snapshot, ma_gap_history)

        s2_signal = self.s2_evaluator.evaluate_trigger(snapshot, s2_state, symbol, tf)
        if s2_signal:
            signals.append(s2_signal)
            # Canon §1.9: log SETUP_FIRED event
            try:
                log_signal_event(
                    event_type="SETUP_FIRED",
                    symbol=symbol,
                    timeframe=tf,
                    setup_id="S2",
                    direction=s2_signal.direction.value,
                    bar_start_ts=_bar_start_dt(snapshot),
                    setup_score=s2_signal.setup_score,
                    feature_snapshot={
                        "ma_gap_atr": s2_state.ma_gap_atr,
                        "dec_count": s2_state.dec_count,
                        "converging": s2_state.converging,
                        "clv": snapshot.clv,
                        "volume_z": snapshot.volume_z,
                        "sma50_slope_atr": snapshot.sma50_slope_atr,
                        "sma200_slope_atr": snapshot.sma200_slope_atr,
                    },
                    score_components={
                        "conv_tight": s2_signal.conv_tight,
                        "conv_speed": s2_signal.conv_speed,
                        "trend_strength": s2_signal.trend_strength,
                        "participation": s2_signal.participation,
                        "break_quality": s2_signal.break_quality,
                        "clean_close": s2_signal.clean_close,
                    },
                    reason_codes=s2_signal.explain.why_items if s2_signal.explain else None,
                )
            except Exception:
                logger.debug("S2 SETUP_FIRED log failed", exc_info=True)

        return signals
    
    def signal_to_ranked(self, signal: Union[S1Signal, S2Signal]) -> RankedSignal:
        """Convert setup-specific signal to unified RankedSignal."""
        now_ts = int(time.time())

        if isinstance(signal, S1Signal):
            setup_id = "S1"
            payload = {
                "setup_id": "S1",
                "setup_name": "BB Squeeze ON v2.1",
                "state": {
                    "squeeze_on": signal.state.squeeze_on,
                    "squeeze_age": signal.state.squeeze_age,
                    "bb_width_pctile": signal.state.bb_width_pctile,
                    "bias_score": signal.state.bias_score,
                    "box_high": signal.state.box_high,
                    "box_low": signal.state.box_low,
                },
                "score_components": {
                    "severity": signal.severity,
                    "bias_strength": signal.bias_strength,
                    "participation": signal.participation,
                    "break_quality": signal.break_quality,
                    "clean_close": signal.clean_close,
                },
                # Canon 7A: explainability
                "why_items": signal.explain.why_items if signal.explain else [],
                # Canon 5E: timing metadata
                "time_stop_bars": signal.time_stop_bars,
            }
        else:  # S2Signal
            setup_id = "S2"
            payload = {
                "setup_id": "S2",
                "setup_name": "Golden Cross Anticipation v1.4",
                "state": {
                    "converging": signal.state.converging,
                    "ma_gap_atr": signal.state.ma_gap_atr,
                    "dec_count": signal.state.dec_count,
                    "pre_cross_long": signal.state.pre_cross_long,
                    "pre_cross_short": signal.state.pre_cross_short,
                },
                "score_components": {
                    "conv_tight": signal.conv_tight,
                    "conv_speed": signal.conv_speed,
                    "trend_strength": signal.trend_strength,
                    "participation": signal.participation,
                    "break_quality": signal.break_quality,
                    "clean_close": signal.clean_close,
                },
                # Canon 7A: explainability
                "why_items": signal.explain.why_items if signal.explain else [],
                # Canon 5E: timing metadata
                "time_stop_bars": signal.time_stop_bars,
            }

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
            from cache_contract import get_top_signals, is_cache_ready, check_staleness
            if not is_cache_ready(self.redis_client):
                logger.warning("Cache not ready, falling back to REST")
                self.source = "rest"
            else:
                s = check_staleness(self.redis_client, "BTCUSDT", "1m")
                if s["stale"]:
                    logger.warning(f"Cache stale ({s['age_s']:.0f}s), falling back to REST")
                    self.source = "rest"

        if self.source == "redis" and self.redis_client:
            from cache_contract import get_top_signals, is_cache_ready
            if is_cache_ready(self.redis_client):
                cached = get_top_signals(
                    self.redis_client,
                    setup_ids=["S1", "S2"],
                    timeframes=self.timeframes,
                    top_n=self.top_n,
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
        
        # Phase 2: Compute indicators and evaluate setups (CPU-bound, sequential)
        compute_start = time.time()
        
        for symbol, tf in successful_pairs:
            token = ctx_symbol.set(symbol)
            try:
                # Compute indicators
                snapshot = self.compute_indicators(symbol, tf)
                if snapshot is None:
                    continue

                # Evaluate setups
                signals = self.evaluate_setups(symbol, tf, snapshot)

                # Convert to ranked signals
                for sig in signals:
                    ranked = self.signal_to_ranked(sig)
                    all_signals.append(ranked)
            except Exception as e:
                logger.debug("Compute error", extra={
                    "symbol": symbol, "timeframe": tf, "error": str(e),
                })
            finally:
                ctx_symbol.reset(token)
        
        compute_time = round(time.time() - compute_start, 1)
        logger.info("Indicator compute complete", extra={
            "event": "indicators_computed",
            "signal_count": len(all_signals), "duration_s": compute_time,
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
        
        # Canon §1.9: log RANK_CHANGE for each signal in the top-N
        top_n_signals = deduplicated[:self.top_n]
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
    """
    Get default symbol universe.
    Top 200 USDT pairs by trading activity.
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


def fetch_top_symbols_by_volume(n: int = 200, quote: str = "USDT") -> list[str]:
    """
    Dynamically fetch top N symbols by 24h quote volume from Binance.
    Falls back to hardcoded list on error.
    """
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            timeout=15
        )
        resp.raise_for_status()
        tickers = resp.json()
        
        # Filter to USDT pairs and sort by quote volume
        usdt_pairs = [
            t for t in tickers
            if t["symbol"].endswith(quote)
            and float(t["quoteVolume"]) > 0
        ]
        usdt_pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        
        symbols = [t["symbol"] for t in usdt_pairs[:n]]
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
