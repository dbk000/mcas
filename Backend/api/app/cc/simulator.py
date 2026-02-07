"""
Champion/Challenger Framework - Simulator
==========================================

Shadow trade simulation using the SAME execution model as paper trading.

Key responsibilities:
- Simulate trade entries at next bar open
- Simulate trade exits (STOP_LOSS, TIME_STOP, TP1)
- Track MAE/MFE, bars held, half-R progress
- Handle pending signals/exits with stale cancellation

Canon compliance:
- MUST reuse execution.py slippage/fee functions
- Entry at NEXT bar OPEN (not signal bar close)
- Time-stop uses bars_held >= N_dead (not >)
- Time-stop exit at next bar OPEN
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Any, TYPE_CHECKING

from .models import (
    ShadowTrade, ShadowPosition, ExitResult, PendingSignal,
    Direction, ExecutionContext,
    MAX_PENDING_BARS, CC_STATE_TTL_S,
)

if TYPE_CHECKING:
    from .repository import CCRepository

logger = logging.getLogger(__name__)


# =============================================================================
# EXECUTION CONTEXT GUARD
# =============================================================================

def assert_shadow_safe(context: ExecutionContext, operation: str) -> None:
    """
    Guard: Raise if attempting forbidden operation in SHADOW context.

    Call this at the top of any function that could have side effects.
    """
    if context == ExecutionContext.SHADOW:
        raise RuntimeError(
            f"SHADOW context violation: attempted '{operation}' in shadow mode. "
            f"Shadow runs MUST NOT have external effects."
        )


# =============================================================================
# SLIPPAGE/FEE HELPERS (reuse from execution.py)
# =============================================================================

def apply_slippage(
    price: float,
    direction: Direction,
    slippage_bps: float,
    is_entry: bool = True,
) -> float:
    """
    Apply slippage to a price (adverse to trader).

    This is a pure math function - safe to use in SHADOW context.

    Args:
        price: The base price
        direction: LONG or SHORT
        slippage_bps: Slippage in basis points
        is_entry: True for entry, False for exit

    Returns:
        Price with slippage applied
    """
    slip = price * (slippage_bps / 10000)

    if is_entry:
        # Entry: pay more for LONG, receive less for SHORT
        if direction == Direction.LONG:
            return price + slip
        else:
            return price - slip
    else:
        # Exit: receive less for LONG, pay more for SHORT
        if direction == Direction.LONG:
            return price - slip
        else:
            return price + slip


def calculate_fees(
    notional: float,
    is_maker: bool = False,
    fee_maker_bps: float = 2.0,
    fee_taker_bps: float = 4.0,
) -> float:
    """
    Calculate trading fees.

    This is a pure math function - safe to use in SHADOW context.

    Args:
        notional: Trade notional value
        is_maker: Whether this is a maker order
        fee_maker_bps: Maker fee in basis points
        fee_taker_bps: Taker fee in basis points

    Returns:
        Fee amount
    """
    bps = fee_maker_bps if is_maker else fee_taker_bps
    return notional * (bps / 10000)


# =============================================================================
# TIME STOP CONFIGURATION
# =============================================================================

def get_time_stop_bars(timeframe: str) -> int:
    """
    Get the time-stop window in bars for a given timeframe.

    Canon: Time stop checks if bars_held >= N_dead and !half_r_touched.
    """
    # Default time-stop windows by timeframe
    time_stop_bars = {
        "1m": 30,
        "3m": 20,
        "5m": 15,
        "15m": 12,
        "30m": 10,
        "1h": 8,
        "2h": 6,
        "4h": 4,
        "1d": 3,
    }
    return time_stop_bars.get(timeframe, 8)  # Default 8 bars


# =============================================================================
# SHADOW TRADE SIMULATOR
# =============================================================================

class ShadowTradeSimulator:
    """
    Simulates trade outcomes using the SAME execution model as paper trading.

    MUST reuse:
    - apply_slippage() for slippage calculation
    - calculate_fees() for fee calculation
    - Same exit priority as paper_trading.py

    This ensures CC metrics are comparable to real paper trading outcomes.

    v7: All operations are pure shadow - no external effects.
    """

    def __init__(
        self,
        execution_spec: dict,
        state_backend: Any,  # VariantStateBackend
        repository: "CCRepository",
    ):
        self.spec = execution_spec
        self.state_backend = state_backend
        self.repo = repository

        # Extract execution params
        self.slippage_entry_bps = execution_spec.get("slippage_entry_bps", 5.0)
        self.slippage_exit_bps = execution_spec.get("slippage_exit_bps", 5.0)
        self.fee_maker_bps = execution_spec.get("fee_maker_bps", 2.0)
        self.fee_taker_bps = execution_spec.get("fee_taker_bps", 4.0)

    # =========================================================================
    # PENDING SIGNALS
    # =========================================================================

    def record_signal(
        self,
        run_id: str,
        variant_id: str,
        signal: Any,
        symbol: str,
        tf: str,
    ) -> None:
        """
        Record a signal for next-bar entry.

        MCAS canonical entry is NEXT_OPEN: signal on bar N, enter at bar N+1 open.
        Store the signal and process entry on the next evaluate_bar() call.
        """
        pending = PendingSignal(
            run_id=run_id,
            variant_id=variant_id,
            symbol=symbol,
            timeframe=tf,
            signal_ts=signal.signal_ts if hasattr(signal, 'signal_ts') else datetime.now(timezone.utc),
            signal_bar_ts=signal.bar_start_ts if hasattr(signal, 'bar_start_ts') else datetime.now(timezone.utc),
            direction=signal.direction if hasattr(signal, 'direction') else Direction.LONG,
            stop_price=signal.stop_price if hasattr(signal, 'stop_price') else 0.0,
            tp1_price=signal.tp1_price if hasattr(signal, 'tp1_price') else None,
            tp2_price=signal.tp2_price if hasattr(signal, 'tp2_price') else None,
            r_value=signal.r_value if hasattr(signal, 'r_value') else 0.0,
            bars_pending=0,
            max_pending_bars=MAX_PENDING_BARS,
        )

        # Save to Postgres (Redis managed separately by state backend)
        self.repo.save_pending_signal(pending)
        logger.debug(f"Recorded pending signal for {symbol}:{tf} variant {variant_id}")

    def age_pending_signals(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        tf: str,
    ) -> None:
        """
        v7: Increment bars_pending for any signal not processed this bar.

        Called when a new bar arrives but the pending signal wasn't consumed.
        Prevents signals from waiting indefinitely on data gaps.
        """
        # This would require loading from Postgres and incrementing
        # For now, just log - actual aging happens in process_pending_entries
        pass

    def process_pending_entries(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        tf: str,
        snapshot: Any,
    ) -> Optional[ShadowTrade]:
        """
        Called at the START of each bar to process pending signals.

        Entry price: Current bar's OPEN + slippage (not previous bar's close).

        v7 MITIGATION: If signal has been pending > MAX_PENDING_BARS (data gap),
        cancel it and log a warning instead of entering stale.
        """
        # Check for pending signal in Postgres
        # (In full implementation, would check Redis first)
        # For now, skip if no signal processing mechanism is in place
        # This is a placeholder that would integrate with actual signal storage

        # TODO: Load pending signal from state backend
        # For now, return None (no pending signal)
        return None

    # =========================================================================
    # EXIT CHECKING
    # =========================================================================

    def check_exit(
        self,
        position: ShadowPosition,
        snapshot: Any,
    ) -> ExitResult:
        """
        Check exit conditions using SAME priority as paper trading.

        MVP implements: STOP_LOSS -> TIME_STOP -> TP1
        Full parity (future): STOP_LOSS -> FAIL_FAST -> TIME_STOP -> TRAILING_STOP -> TP1 -> TP2

        NOTE: FAIL_FAST and TRAILING_STOP are not implemented in MVP.
        """
        bar = self._get_current_bar(snapshot)
        if not bar:
            return ExitResult(should_exit=False)

        # Increment bars held
        position.bars_held += 1

        # Update MAE/MFE (LONG and SHORT symmetric)
        if position.direction == Direction.LONG:
            position.mae = min(position.mae, bar.low - position.entry_price_with_slippage)
            position.mfe = max(position.mfe, bar.high - position.entry_price_with_slippage)
        else:  # SHORT
            position.mae = min(position.mae, position.entry_price_with_slippage - bar.high)
            position.mfe = max(position.mfe, position.entry_price_with_slippage - bar.low)

        # Track 0.5R progress for time stop (BOTH directions)
        if position.direction == Direction.LONG:
            half_r_target = position.entry_price_with_slippage + 0.5 * position.r_value
            if bar.high >= half_r_target:
                position.half_r_touched = True
        else:  # SHORT
            half_r_target = position.entry_price_with_slippage - 0.5 * position.r_value
            if bar.low <= half_r_target:
                position.half_r_touched = True

        # =====================================================================
        # Exit checks (SAME priority as paper_trading.py)
        # =====================================================================

        # 1. Stop loss (touch model)
        if self._hit_stop(position, bar):
            return ExitResult(
                should_exit=True,
                exit_reason="STOP_LOSS",
                exit_price=position.stop_price,
            )

        # 2. Time stop (Canon: progress-to-0.5R within N_dead bars, exit at NEXT BAR OPEN)
        #    Window check: bars_held >= time_stop_bars (not >)
        #    Exit price: NEXT bar open, so we flag it and exit on next call
        time_stop_bars = get_time_stop_bars(position.timeframe)
        if position.bars_held >= time_stop_bars and not position.half_r_touched:
            # Flag for exit at next bar open
            return ExitResult(
                should_exit=True,
                exit_reason="TIME_STOP",
                exit_price=bar.close,  # Placeholder; actual exit at next bar open
                exit_at_next_open=True,  # Signal to caller to use next bar's open
            )

        # 3. TP1 (touch model)
        if position.tp1_price and self._hit_tp1(position, bar):
            return ExitResult(
                should_exit=True,
                exit_reason="TP1",
                exit_price=position.tp1_price,
            )

        return ExitResult(should_exit=False)

    def _hit_stop(self, position: ShadowPosition, bar: Any) -> bool:
        """Check if stop was hit (touch model, same as paper_trading.py)."""
        if position.direction == Direction.LONG:
            return bar.low <= position.stop_price
        else:  # SHORT
            return bar.high >= position.stop_price

    def _hit_tp1(self, position: ShadowPosition, bar: Any) -> bool:
        """Check if TP1 was hit (touch model, same as paper_trading.py)."""
        if not position.tp1_price:
            return False
        if position.direction == Direction.LONG:
            return bar.high >= position.tp1_price
        else:  # SHORT
            return bar.low <= position.tp1_price

    def _get_current_bar(self, snapshot: Any) -> Optional[Any]:
        """Get the current bar from snapshot."""
        if hasattr(snapshot, 'ohlcv') and snapshot.ohlcv:
            return snapshot.ohlcv[-1]
        return None

    # =========================================================================
    # EXIT EXECUTION
    # =========================================================================

    def finalize_pending_exit(
        self,
        run_id: str,
        variant_id: str,
        position: ShadowPosition,
        snapshot: Any,
    ) -> None:
        """
        Finalize TIME_STOP exits at this bar's OPEN.

        Called at START of bar processing, before entries.

        v7 MITIGATION: If pending exit has been waiting > MAX_PENDING_BARS,
        force-resolve at current bar's open with warning.
        """
        if not position.pending_exit:
            return

        bar = self._get_current_bar(snapshot)
        if not bar:
            return

        pending = position.pending_exit

        # v7: Track and warn on stale pending exits
        bars_pending = pending.bars_pending + 1
        if bars_pending > MAX_PENDING_BARS:
            logger.warning(
                f"Force-resolving stale pending exit for {position.symbol}:{position.timeframe} - "
                f"pending for {bars_pending} bars (max {MAX_PENDING_BARS}). "
                f"Likely data gap. Using current bar's open."
            )

        # Exit at this bar's OPEN (not previous bar's close)
        exit_price = bar.open
        exit_with_slippage = apply_slippage(
            exit_price,
            position.direction,
            self.slippage_exit_bps,
            is_entry=False,
        )

        # Calculate fees
        notional = exit_with_slippage * position.position_size
        fees = calculate_fees(
            notional=notional,
            is_maker=False,  # TIME_STOP is market order = taker
            fee_maker_bps=self.fee_maker_bps,
            fee_taker_bps=self.fee_taker_bps,
        )

        # Calculate R multiple and PnL
        r_multiple = self._calculate_r_multiple(
            position.direction,
            position.entry_price_with_slippage,
            exit_with_slippage,
            position.r_value,
        )

        pnl_gross = self._calculate_pnl(
            position.direction,
            position.entry_price_with_slippage,
            exit_with_slippage,
            position.position_size,
        )

        # Update and save trade
        trade = ShadowTrade(
            trade_id=position.trade_id,
            symbol=position.symbol,
            timeframe=position.timeframe,
            direction=position.direction,
            entry_ts=position.entry_ts,
            entry_price=position.entry_price,
            entry_price_with_slippage=position.entry_price_with_slippage,
            stop_price=position.stop_price,
            tp1_price=position.tp1_price,
            r_value=position.r_value,
            exit_ts=bar.bar_start_ts if hasattr(bar, 'bar_start_ts') else datetime.now(timezone.utc),
            exit_price=exit_price,
            exit_price_with_slippage=exit_with_slippage,
            exit_reason=pending.exit_reason,
            r_multiple=r_multiple,
            pnl_gross=pnl_gross,
            pnl_net=pnl_gross - fees,
            fees_paid=fees,
            bars_held=position.bars_held,
            mae=position.mae,
            mfe=position.mfe,
        )

        self.repo.save_trade(run_id, variant_id, trade)

        # Clear the position
        self.state_backend.clear_shadow_position(
            run_id, variant_id, position.symbol, position.timeframe
        )

        logger.debug(
            f"Finalized pending exit for {position.symbol}:{position.timeframe} "
            f"variant {variant_id}: {pending.exit_reason} @ {exit_with_slippage:.4f}"
        )

    def execute_immediate_exit(
        self,
        run_id: str,
        variant_id: str,
        position: ShadowPosition,
        exit_result: ExitResult,
        snapshot: Any,
    ) -> None:
        """
        Execute an immediate exit (STOP_LOSS, TP1).

        Uses bar.close_time to match paper_trading.py semantics
        (we don't know exact intra-bar time, so use bar close).
        """
        bar = self._get_current_bar(snapshot)
        if not bar:
            return

        exit_price = exit_result.exit_price or bar.close
        exit_with_slippage = apply_slippage(
            exit_price,
            position.direction,
            self.slippage_exit_bps,
            is_entry=False,
        )

        # Calculate fees
        notional = exit_with_slippage * position.position_size
        fees = calculate_fees(
            notional=notional,
            is_maker=False,  # STOP_LOSS/TP1 are market orders = taker
            fee_maker_bps=self.fee_maker_bps,
            fee_taker_bps=self.fee_taker_bps,
        )

        # Calculate R multiple and PnL
        r_multiple = self._calculate_r_multiple(
            position.direction,
            position.entry_price_with_slippage,
            exit_with_slippage,
            position.r_value,
        )

        pnl_gross = self._calculate_pnl(
            position.direction,
            position.entry_price_with_slippage,
            exit_with_slippage,
            position.position_size,
        )

        # Update and save trade
        trade = ShadowTrade(
            trade_id=position.trade_id,
            symbol=position.symbol,
            timeframe=position.timeframe,
            direction=position.direction,
            entry_ts=position.entry_ts,
            entry_price=position.entry_price,
            entry_price_with_slippage=position.entry_price_with_slippage,
            stop_price=position.stop_price,
            tp1_price=position.tp1_price,
            r_value=position.r_value,
            exit_ts=bar.close_time if hasattr(bar, 'close_time') else datetime.now(timezone.utc),
            exit_price=exit_price,
            exit_price_with_slippage=exit_with_slippage,
            exit_reason=exit_result.exit_reason,
            r_multiple=r_multiple,
            pnl_gross=pnl_gross,
            pnl_net=pnl_gross - fees,
            fees_paid=fees,
            bars_held=position.bars_held,
            mae=position.mae,
            mfe=position.mfe,
        )

        self.repo.save_trade(run_id, variant_id, trade)

        # Clear the position
        self.state_backend.clear_shadow_position(
            run_id, variant_id, position.symbol, position.timeframe
        )

        logger.debug(
            f"Immediate exit for {position.symbol}:{position.timeframe} "
            f"variant {variant_id}: {exit_result.exit_reason} @ {exit_with_slippage:.4f}"
        )

    def _calculate_r_multiple(
        self,
        direction: Direction,
        entry_price: float,
        exit_price: float,
        r_value: float,
    ) -> float:
        """Calculate R multiple for a trade."""
        if r_value == 0:
            return 0.0

        if direction == Direction.LONG:
            pnl = exit_price - entry_price
        else:
            pnl = entry_price - exit_price

        return pnl / r_value

    def _calculate_pnl(
        self,
        direction: Direction,
        entry_price: float,
        exit_price: float,
        size: float,
    ) -> float:
        """Calculate gross PnL for a trade."""
        if direction == Direction.LONG:
            return (exit_price - entry_price) * size
        else:
            return (entry_price - exit_price) * size
