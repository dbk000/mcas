"""
Price Feed Service
==================

Real-time price data for position monitoring.

Two modes:
1. Polling mode - Fetch prices every N seconds (simple, reliable)
2. WebSocket mode - Subscribe to Binance streams (faster, more complex)

> "For live trading simulation, the timeliness of data is crucial."
"""

import os
import time
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Callable
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import redis

logger = logging.getLogger(__name__)

BINANCE_API_BASE = "https://api.binance.com/api/v3"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


class PriceCache:
    """
    Thread-safe price cache backed by Redis.
    
    Stores latest prices for symbols with timestamps.
    """
    
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or REDIS_URL
        self._local_cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        
        try:
            self.r = redis.Redis.from_url(self.redis_url, decode_responses=True)
            self.r.ping()
            self.use_redis = True
            logger.info("PriceCache using Redis")
        except Exception as e:
            logger.warning(f"Redis not available, using local cache: {e}")
            self.use_redis = False
    
    def set_price(self, symbol: str, price: float, bid: float = None, ask: float = None):
        """Update price for a symbol."""
        data = {
            "price": price,
            "bid": bid or price,
            "ask": ask or price,
            "timestamp": time.time(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if self.use_redis:
            self.r.hset(f"price:{symbol}", mapping={
                "price": str(price),
                "bid": str(bid or price),
                "ask": str(ask or price),
                "timestamp": str(data["timestamp"]),
            })
            self.r.expire(f"price:{symbol}", 300)  # 5 min TTL
        
        with self._lock:
            self._local_cache[symbol] = data
    
    def get_price(self, symbol: str) -> Optional[dict]:
        """Get latest price for a symbol.

        Priority (invariant #8):
        1. candle_meta:{symbol}:1m (authoritative, ingestor-owned)
        2. price:{symbol} (legacy, ingestor-owned, backward compat)
        3. Local in-memory cache
        """
        from cache_contract import b2f

        # 1. Authoritative: candle_meta (ingestor-owned)
        if self.use_redis:
            try:
                meta = self.r.hgetall(f"candle_meta:{symbol}:1m")
                if meta and b2f(meta.get("as_of_ts", 0)) > time.time() - 30:
                    price = b2f(meta.get("last_close"))
                    if price > 0:
                        return {
                            "price": price,
                            "bid": price,
                            "ask": price,
                            "timestamp": b2f(meta.get("as_of_ts")),
                        }
            except Exception:
                pass

        # 2. Legacy: price:{symbol} (ingestor-owned, backward compat)
        if self.use_redis:
            try:
                raw = self.r.hgetall(f"price:{symbol}")
                if raw and "price" in raw:
                    return {
                        "price": b2f(raw.get("price", 0)),
                        "bid": b2f(raw.get("bid", 0)),
                        "ask": b2f(raw.get("ask", 0)),
                        "timestamp": b2f(raw.get("timestamp", 0)),
                    }
            except Exception as e:
                logger.debug(f"Redis get failed: {e}")

        # 3. Last resort: local cache
        with self._lock:
            if symbol in self._local_cache:
                data = self._local_cache[symbol]
                if time.time() - data["timestamp"] < 60:
                    return data

        return None
    
    def get_prices(self, symbols: list[str]) -> dict[str, dict]:
        """Get prices for multiple symbols."""
        result = {}
        for symbol in symbols:
            price_data = self.get_price(symbol)
            if price_data:
                result[symbol] = price_data
        return result
    
    def get_all_prices(self) -> dict[str, dict]:
        """Get all cached prices."""
        with self._lock:
            return dict(self._local_cache)


class PriceFeed:
    """
    Real-time price feed service.
    
    Modes:
    - POLLING: Fetch prices from REST API every N seconds
    - WEBSOCKET: Subscribe to Binance WebSocket streams (future)
    
    Usage:
        feed = PriceFeed(symbols=["BTCUSDT", "ETHUSDT"])
        feed.start()
        
        # Get current price
        price = feed.get_price("BTCUSDT")
        
        # Register callback for price updates
        feed.on_price_update(my_callback)
        
        feed.stop()
    """
    
    def __init__(
        self,
        symbols: list[str] = None,
        poll_interval: float = 2.0,
        max_workers: int = 10,
    ):
        self.symbols = set(symbols or [])
        self.poll_interval = poll_interval
        self.max_workers = max_workers
        
        self.cache = PriceCache()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable] = []
    
    def add_symbol(self, symbol: str):
        """Add a symbol to track."""
        self.symbols.add(symbol)
    
    def remove_symbol(self, symbol: str):
        """Remove a symbol from tracking."""
        self.symbols.discard(symbol)
    
    def on_price_update(self, callback: Callable):
        """
        Register a callback for price updates.
        
        Callback signature: callback(symbol: str, price: float, data: dict)
        """
        self._callbacks.append(callback)
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol."""
        data = self.cache.get_price(symbol)
        return data["price"] if data else None
    
    def get_ohlc(self, symbol: str) -> Optional[dict]:
        """Get current OHLC (from latest 1m candle)."""
        return self._fetch_latest_candle(symbol)
    
    def start(self):
        """Start the price feed."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"PriceFeed started: {len(self.symbols)} symbols, {self.poll_interval}s interval")
    
    def stop(self):
        """Stop the price feed."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("PriceFeed stopped")
    
    def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                if self.symbols:
                    self._fetch_all_prices()
            except Exception as e:
                logger.error(f"Poll error: {e}")
            
            time.sleep(self.poll_interval)
    
    def _fetch_all_prices(self):
        """Fetch prices for all tracked symbols."""
        symbols_list = list(self.symbols)
        
        if len(symbols_list) <= 20:
            # Small number - fetch individually with threads
            self._fetch_prices_parallel(symbols_list)
        else:
            # Large number - use batch endpoint
            self._fetch_prices_batch(symbols_list)
    
    def _fetch_prices_parallel(self, symbols: list[str]):
        """Fetch prices in parallel using threads."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._fetch_single_price, symbol): symbol
                for symbol in symbols
            }
            
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    price = future.result()
                    if price:
                        self.cache.set_price(symbol, price)
                        self._notify_callbacks(symbol, price)
                except Exception as e:
                    logger.debug(f"Failed to fetch {symbol}: {e}")
    
    def _fetch_prices_batch(self, symbols: list[str]):
        """Fetch all prices in one batch request."""
        try:
            # Use 24hr ticker endpoint for batch prices
            resp = requests.get(
                f"{BINANCE_API_BASE}/ticker/price",
                timeout=10,
            )
            resp.raise_for_status()
            
            all_prices = {item["symbol"]: float(item["price"]) for item in resp.json()}
            
            for symbol in symbols:
                if symbol in all_prices:
                    price = all_prices[symbol]
                    self.cache.set_price(symbol, price)
                    self._notify_callbacks(symbol, price)
        
        except requests.exceptions.Timeout:
            logger.warning("Batch price fetch timeout", extra={
                "event": "timeout", "endpoint": "ticker/price",
                "symbol_count": len(symbols),
            })
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.error("Batch price fetch failed", extra={
                "event": "api_error", "endpoint": "ticker/price",
                "symbol_count": len(symbols),
                "http_status": status,
                "error": str(e),
            })

    def _fetch_single_price(self, symbol: str) -> Optional[float]:
        """Fetch price for a single symbol."""
        try:
            resp = requests.get(
                f"{BINANCE_API_BASE}/ticker/price",
                params={"symbol": symbol},
                timeout=5,
            )
            if resp.status_code in (418, 429):
                logger.warning("Binance rate limit hit (price feed)", extra={
                    "event": "rate_limit", "endpoint": "ticker/price",
                    "symbol": symbol, "http_status": resp.status_code,
                    "retry_after": resp.headers.get("Retry-After", "unknown"),
                })
                return None
            resp.raise_for_status()
            return float(resp.json()["price"])
        except requests.exceptions.Timeout:
            logger.warning("Single price fetch timeout", extra={
                "event": "timeout", "endpoint": "ticker/price",
                "symbol": symbol,
            })
            return None
        except Exception as e:
            logger.debug("Single price fetch failed", extra={
                "event": "api_error", "endpoint": "ticker/price",
                "symbol": symbol, "error": str(e),
            })
            return None

    def _fetch_latest_candle(self, symbol: str) -> Optional[dict]:
        """Fetch the latest 1-minute candle."""
        try:
            resp = requests.get(
                f"{BINANCE_API_BASE}/klines",
                params={
                    "symbol": symbol,
                    "interval": "1m",
                    "limit": 1,
                },
                timeout=5,
            )
            if resp.status_code in (418, 429):
                logger.warning("Binance rate limit hit (candle)", extra={
                    "event": "rate_limit", "endpoint": "klines",
                    "symbol": symbol, "http_status": resp.status_code,
                })
                return None
            resp.raise_for_status()
            data = resp.json()

            if data:
                candle = data[0]
                return {
                    "bar_start_ts": candle[0],
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                }
        except requests.exceptions.Timeout:
            logger.warning("Candle fetch timeout", extra={
                "event": "timeout", "endpoint": "klines", "symbol": symbol,
            })
        except Exception as e:
            logger.debug("Candle fetch failed", extra={
                "event": "api_error", "endpoint": "klines",
                "symbol": symbol, "error": str(e),
            })
        
        return None
    
    def _notify_callbacks(self, symbol: str, price: float):
        """Notify all registered callbacks."""
        data = self.cache.get_price(symbol)
        for callback in self._callbacks:
            try:
                callback(symbol, price, data)
            except Exception as e:
                logger.error(f"Callback error: {e}")


class PositionMonitor:
    """
    Monitors open positions against live prices.
    
    Checks if stop-loss or take-profit levels are hit
    and triggers position closure when appropriate.
    
    Usage:
        monitor = PositionMonitor(
            database_url=DATABASE_URL,
            price_feed=feed,
            on_position_close=my_handler,
        )
        monitor.start()
    """
    
    def __init__(
        self,
        database_url: str,
        price_feed: PriceFeed,
        check_interval: float = 1.0,
        on_position_close: Callable = None,
    ):
        self.database_url = database_url
        self.price_feed = price_feed
        self.check_interval = check_interval
        self.on_position_close = on_position_close
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start monitoring positions."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("PositionMonitor started")
    
    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("PositionMonitor stopped")
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        import psycopg
        
        while self._running:
            try:
                with psycopg.connect(self.database_url) as conn:
                    self._check_positions(conn)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
            
            time.sleep(self.check_interval)
    
    def _check_positions(self, conn):
        """Check all open positions against current prices.
        
        Exit conditions checked:
        1. STOP_LOSS - Price hits stop level
        2. TP1/TP2 - Price hits take-profit levels
        3. FAIL_FAST - Price re-enters breakout zone within N bars (S1 only)
        4. TIME_STOP - Position held too long without progress (T_dead)
        5. TRAILING_STOP - Price retraces from high water mark
        """
        with conn.cursor() as cur:
            # Get all open positions with additional fields for exit checks
            cur.execute("""
                SELECT id, run_id, symbol, side, entry_price_with_slippage,
                       stop_price, tp1_price, tp2_price, r_value, risk_amount,
                       position_size, notional_value, fees_paid, high_water_mark,
                       setup_id, bars_held, entry_ts, trace_json
                FROM paper_positions
                WHERE status = 'OPEN';
            """)
            positions = cur.fetchall()
        
        for pos in positions:
            (pos_id, run_id, symbol, side, entry, stop, tp1, tp2, r_val, risk, 
             size, notional, fees, hwm, setup_id, bars_held, entry_ts, trace_json) = pos
            
            # Get current price
            price = self.price_feed.get_price(symbol)
            if not price:
                continue
            
            # Also get high/low from latest candle for intrabar fills
            candle = self.price_feed.get_ohlc(symbol)
            if candle:
                high = candle["high"]
                low = candle["low"]
            else:
                high = low = price
            
            # Parse trace for fail-fast box levels
            box_high = None
            box_low = None
            fail_fast_bars = 3
            time_stop_bars = 24  # Default S2
            
            if trace_json:
                try:
                    import json
                    trace = json.loads(trace_json)
                    box_high = trace.get("box_high")
                    box_low = trace.get("box_low")
                    fail_fast_bars = trace.get("fail_fast_bars", 3)
                    time_stop_bars = trace.get("time_stop_bars", 24)
                    
                    # S1 has shorter time stop (4h = 4 bars on 1h)
                    if setup_id == "S1":
                        time_stop_bars = trace.get("time_stop_bars", 4)
                except:
                    pass
            
            # Check exit conditions in priority order
            exit_reason, exit_price = self._check_exit_v2(
                side=side,
                entry=entry,
                stop=stop,
                tp1=tp1,
                tp2=tp2,
                high=high,
                low=low,
                close=price,
                setup_id=setup_id,
                bars_held=bars_held or 0,
                hwm=hwm,
                r_val=r_val,
                box_high=box_high,
                box_low=box_low,
                fail_fast_bars=fail_fast_bars,
                time_stop_bars=time_stop_bars,
            )
            
            if exit_reason:
                self._close_position(
                    conn, pos_id, run_id, symbol, side, entry,
                    exit_reason, exit_price, size, risk, notional, fees
                )
            else:
                # Update current price, high water mark, and bars_held
                new_hwm = max(hwm or entry, high) if side == "long" else min(hwm or entry, low)
                unrealized = ((price - entry) * size if side == "long"
                              else (entry - price) * size)
                
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE paper_positions
                        SET current_price = %s,
                            unrealized_pnl = %s,
                            high_water_mark = %s,
                            bars_held = bars_held + 1,
                            updated_at = %s
                        WHERE id = %s;
                    """, (price, unrealized, new_hwm, datetime.now(timezone.utc), pos_id))
                conn.commit()
    
    def _check_exit_v2(
        self,
        side: str,
        entry: float,
        stop: float,
        tp1: Optional[float],
        tp2: Optional[float],
        high: float,
        low: float,
        close: float,
        setup_id: str,
        bars_held: int,
        hwm: Optional[float],
        r_val: float,
        box_high: Optional[float],
        box_low: Optional[float],
        fail_fast_bars: int,
        time_stop_bars: int,
    ) -> tuple[Optional[str], float]:
        """
        Enhanced exit condition checker.
        
        Exit priority:
        1. Stop loss (always checked first)
        2. Fail-fast (S1 only, if price back in box within N bars)
        3. Time stop (position dead, hasn't made 0.5R progress)
        4. Trailing stop (if we've hit TP1, trail from HWM)
        5. Take profits (TP1, TP2)
        """
        # 1. Stop Loss
        if side == "long":
            if low <= stop:
                return ("STOP_LOSS", stop)
        else:  # short
            if high >= stop:
                return ("STOP_LOSS", stop)
        
        # 2. Fail-Fast (S1 only: price re-enters squeeze box)
        if setup_id == "S1" and bars_held <= fail_fast_bars:
            if box_high is not None and box_low is not None:
                if side == "long" and close < box_high:
                    return ("FAIL_FAST", close)
                elif side == "short" and close > box_low:
                    return ("FAIL_FAST", close)
        
        # 3. Time Stop (Canon v2: wick-based 0.5R progress check)
        # Check if price has EVER reached Entry ± 0.5R using high water mark.
        # If not reached within time_stop_bars, exit at next bar (bars_held > time_stop_bars).
        if bars_held > time_stop_bars and r_val > 0:
            half_r_target = 0.5 * r_val
            progress_made = False

            if hwm is not None:
                if side == "long":
                    # Progress made if HWM ever reached Entry + 0.5R
                    progress_made = hwm >= entry + half_r_target
                else:
                    # Progress made if HWM (min price for short) ever reached Entry - 0.5R
                    progress_made = hwm <= entry - half_r_target

            if not progress_made:
                return ("TIME_STOP", close)
        
        # 4. Trailing Stop (after TP1 hit, trail from HWM at 1.5 ATR)
        # We know TP1 was hit if HWM >= TP1 (for long) or HWM <= TP1 (for short)
        if hwm is not None and tp1 is not None:
            if side == "long":
                if hwm >= tp1:
                    # TP1 was hit, now trailing
                    # Trailing stop at entry + 0.5R (breakeven + buffer)
                    trail_stop = entry + 0.5 * r_val
                    if close <= trail_stop:
                        return ("TRAILING_STOP", trail_stop)
            else:  # short
                if hwm <= tp1:
                    # TP1 was hit, now trailing
                    trail_stop = entry - 0.5 * r_val
                    if close >= trail_stop:
                        return ("TRAILING_STOP", trail_stop)
        
        # 5. Take Profits
        if side == "long":
            if tp1 and high >= tp1:
                return ("TP1", tp1)
            if tp2 and high >= tp2:
                return ("TP2", tp2)
        else:  # short
            if tp1 and low <= tp1:
                return ("TP1", tp1)
            if tp2 and low <= tp2:
                return ("TP2", tp2)
        
        return (None, 0)
    
    def _close_position(
        self,
        conn,
        pos_id: str,
        run_id: str,
        symbol: str,
        side: str,
        entry: float,
        exit_reason: str,
        exit_price: float,
        size: float,
        risk: float,
        notional: float,
        fees: float,
    ):
        """Close a position and update account."""
        now = datetime.now(timezone.utc)
        
        # Apply exit slippage (5 bps default)
        slip_bps = 5.0
        slip = exit_price * (slip_bps / 10000)
        if side == "long":
            exit_with_slip = exit_price - slip
        else:
            exit_with_slip = exit_price + slip
        
        # Calculate PnL
        if side == "long":
            pnl = (exit_with_slip - entry) * size
        else:
            pnl = (entry - exit_with_slip) * size
        
        # Exit fees
        exit_fee = exit_price * size * (4.0 / 10000)  # 4 bps taker
        total_fees = fees + exit_fee
        
        realized_pnl = pnl - total_fees
        r_multiple = pnl / risk if risk > 0 else 0
        
        with conn.cursor() as cur:
            # Update position
            cur.execute("""
                UPDATE paper_positions
                SET status = 'CLOSED',
                    current_price = %s,
                    exit_ts = %s,
                    exit_price = %s,
                    exit_price_with_slippage = %s,
                    exit_reason = %s,
                    realized_pnl = %s,
                    fees_paid = %s,
                    r_multiple = %s,
                    updated_at = %s
                WHERE id = %s;
            """, (
                exit_price, now, exit_price, exit_with_slip,
                exit_reason, realized_pnl, total_fees, r_multiple,
                now, pos_id,
            ))
            
            # Update account
            win = 1 if realized_pnl > 0 else 0
            loss = 1 if realized_pnl <= 0 else 0
            
            cur.execute("""
                UPDATE paper_accounts
                SET current_capital = current_capital + %s,
                    current_exposure = current_exposure - %s,
                    total_trades = total_trades + 1,
                    winning_trades = winning_trades + %s,
                    losing_trades = losing_trades + %s,
                    total_pnl = total_pnl + %s,
                    total_fees = total_fees + %s,
                    daily_pnl = daily_pnl + %s,
                    updated_at = %s
                WHERE run_id = %s;
            """, (
                realized_pnl, notional,
                win, loss,
                realized_pnl, total_fees, realized_pnl,
                now, run_id,
            ))
            
            # Update peak/drawdown
            cur.execute("""
                UPDATE paper_accounts
                SET peak_capital = GREATEST(peak_capital, current_capital),
                    max_drawdown = GREATEST(
                        max_drawdown,
                        (peak_capital - current_capital) / NULLIF(peak_capital, 0)
                    )
                WHERE run_id = %s;
            """, (run_id,))
        
        conn.commit()
        
        logger.info(
            f"Closed position: {symbol} {exit_reason} @ {exit_with_slip:.4f} "
            f"(PnL: ${realized_pnl:.2f}, R: {r_multiple:.2f})"
        )
        
        # Notify callback
        if self.on_position_close:
            try:
                self.on_position_close({
                    "position_id": pos_id,
                    "run_id": run_id,
                    "symbol": symbol,
                    "side": side,
                    "exit_reason": exit_reason,
                    "exit_price": exit_with_slip,
                    "realized_pnl": realized_pnl,
                    "r_multiple": r_multiple,
                })
            except Exception as e:
                logger.error(f"Close callback error: {e}")
