"""
Exit simulators. Vectorized where possible (E1-E4 pure stop/TP); scalar path for
trailing/break-even/runner/regime logic (E5-E8).

All simulators take:
  entries: np.ndarray[bool]  — same length as OHLCV arrays
  high/low/close arrays
  atr array (already aligned)
  direction: 'long' | 'short'
  params: dict of exit-config params
Returns a list of trade records (dicts).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    stop_price: float
    tp1_price: float | None
    tp2_price: float | None
    exit_type: str       # STOP / TP1 / TP2 / TIME / TRAIL / BE / MAX_BARS
    r_multiple: float
    bars_held: int
    direction: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute_stop_distance(
    atr_val: float,
    high_at_entry: float,
    low_at_entry: float,
    k_stop: float,
    bar_range_mult: float,
) -> float:
    """Stop distance = max(k_stop * ATR, bar_range_mult * entry_bar_range)."""
    bar_range = high_at_entry - low_at_entry
    return max(k_stop * atr_val, bar_range_mult * bar_range if bar_range_mult > 0 else 0.0)


def _simulate_stop_tp(
    entries: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr_arr: np.ndarray,
    direction: str,
    k_stop: float,
    tp1_r: float | None,
    tp2_r: float | None,
    split_tp1: float,
    time_stop_bars: int,
    bar_range_mult: float = 0.0,
) -> list[Trade]:
    """
    Scalar-loop simulator for SL + optional TP1 + optional TP2 + time stop.
    split_tp1 ∈ [0,1] = fraction exited at TP1 when both TP1 and TP2 are set.
    Returns trades; last_exit_idx drives reentry gating (no new entry until prior exit).
    """
    trades: list[Trade] = []
    last_exit_idx = -1
    n = len(close)
    sign = 1.0 if direction == "long" else -1.0

    idxs = np.where(entries)[0]
    for i in idxs:
        if i <= last_exit_idx:
            continue
        if i + 1 >= n:
            continue
        if np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        # Enter at next-bar open to avoid look-ahead.
        entry_idx = i + 1
        if entry_idx >= n:
            continue
        entry_price = float(close[i])  # use current close as entry reference for baseline simplicity
        stop_dist = _compute_stop_distance(float(atr_arr[i]), float(high[i]), float(low[i]), k_stop, bar_range_mult)
        if stop_dist <= 0:
            continue
        stop_price = entry_price - sign * stop_dist
        tp1_price = entry_price + sign * tp1_r * stop_dist if tp1_r is not None else None
        tp2_price = entry_price + sign * tp2_r * stop_dist if tp2_r is not None else None

        exit_idx = -1
        exit_price = entry_price
        exit_type = "TIME"
        tp1_hit = False
        realized_r = 0.0
        residual_frac = 1.0

        max_bars = time_stop_bars if time_stop_bars > 0 else (n - entry_idx - 1)
        for j in range(entry_idx, min(n, entry_idx + max_bars + 1)):
            h, l = float(high[j]), float(low[j])
            # STOP
            stop_hit = (l <= stop_price) if direction == "long" else (h >= stop_price)
            # TPs
            tp1_now = tp1_price is not None and not tp1_hit and ((h >= tp1_price) if direction == "long" else (l <= tp1_price))
            tp2_now = tp2_price is not None and ((h >= tp2_price) if direction == "long" else (l <= tp2_price))
            if stop_hit:
                realized_r += residual_frac * (-1.0)
                exit_idx = j
                exit_price = stop_price
                exit_type = "STOP"
                break
            if tp2_price is not None and tp2_now:
                realized_r += residual_frac * tp2_r
                exit_idx = j
                exit_price = tp2_price
                exit_type = "TP2"
                break
            if tp1_price is not None and tp1_now and tp2_price is None:
                # Single-TP exit
                realized_r += residual_frac * tp1_r
                exit_idx = j
                exit_price = tp1_price
                exit_type = "TP1"
                break
            if tp1_price is not None and tp1_now and tp2_price is not None and split_tp1 > 0:
                realized_r += split_tp1 * tp1_r
                residual_frac = 1.0 - split_tp1
                tp1_hit = True
                # move stop to break-even
                stop_price = entry_price
        if exit_idx < 0:
            exit_idx = min(n - 1, entry_idx + max_bars)
            exit_price = float(close[exit_idx])
            final_r = sign * (exit_price - entry_price) / stop_dist
            realized_r += residual_frac * final_r
            exit_type = "TIME" if tp2_price is None or not tp1_hit else "TIME_AFTER_TP1"

        trades.append(Trade(
            entry_idx=int(entry_idx),
            exit_idx=int(exit_idx),
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            stop_price=float(stop_price),
            tp1_price=float(tp1_price) if tp1_price is not None else None,
            tp2_price=float(tp2_price) if tp2_price is not None else None,
            exit_type=exit_type,
            r_multiple=float(realized_r),
            bars_held=int(exit_idx - entry_idx + 1),
            direction=direction,
        ))
        last_exit_idx = exit_idx
    return trades


def simulate_baseline_stage_a(
    entries: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr_arr: np.ndarray,
    direction: str,
    time_stop_bars: int = 48,
) -> list[Trade]:
    """Stage-A fixed baseline: stop=2.0*ATR(14), TP1=+1R, TP2=+2R split 50/50, time=48."""
    return _simulate_stop_tp(
        entries, high, low, close, atr_arr, direction,
        k_stop=2.0, tp1_r=1.0, tp2_r=2.0, split_tp1=0.5,
        time_stop_bars=time_stop_bars,
    )


# ---------- Stage-B exit-config factory ----------

EXIT_CONFIGS: dict[str, dict[str, Any]] = {
    "E1": {"shape": "SL_only", "time_stop_bars": [12, 24, 48, 96, 192]},
    "E2": {"shape": "SL_TP1_sym", "tp1_r": [0.5, 1.0, 1.5, 2.0]},
    "E3": {"shape": "SL_TP1_asym", "tp1_r": [1.5, 2.0, 2.5, 3.0]},
    "E4": {"shape": "SL_TP1_TP2_split", "tp_pairs": [(1, 2), (1, 3), (2, 3), (2, 4)]},
    "E5": {"shape": "SL_TP1_trail", "tp1_r": [1.0, 1.5, 2.0], "trail_k": [1.0, 1.5, 2.0]},
    "E6": {"shape": "runner", "trail_k": [1.0, 1.5, 2.0, 2.5]},
    "E7": {"shape": "time_decay_tp", "initial_tp_r": [2.0, 3.0], "decay_per_bar": [0.01, 0.02]},
    "E8": {"shape": "regime_aware_k", "vol_low_k": 1.0, "vol_high_k": 3.0},
}

STOP_GRID_K = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
BAR_RANGE_MULTS = [0.0, 1.2, 1.5]


def iter_stage_b_configs(exit_id: str):
    """Yield (config_key, params_dict) tuples for every cell in the Stage-B sweep of this exit config."""
    cfg = EXIT_CONFIGS[exit_id]
    for k_stop in STOP_GRID_K:
        for m in BAR_RANGE_MULTS:
            base = {"k_stop": k_stop, "bar_range_mult": m}
            if exit_id == "E1":
                for ts in cfg["time_stop_bars"]:
                    yield f"{exit_id}_k{k_stop}_m{m}_ts{ts}", {**base, "time_stop_bars": ts, "tp1_r": None, "tp2_r": None, "split_tp1": 0.0}
            elif exit_id in ("E2", "E3"):
                for tp1 in cfg["tp1_r"]:
                    yield f"{exit_id}_k{k_stop}_m{m}_tp{tp1}", {**base, "tp1_r": tp1, "tp2_r": None, "split_tp1": 0.0, "time_stop_bars": 48}
            elif exit_id == "E4":
                for tp1, tp2 in cfg["tp_pairs"]:
                    yield f"{exit_id}_k{k_stop}_m{m}_{tp1}_{tp2}", {**base, "tp1_r": tp1, "tp2_r": tp2, "split_tp1": 0.5, "time_stop_bars": 48}
            elif exit_id == "E5":
                for tp1 in cfg["tp1_r"]:
                    for tk in cfg["trail_k"]:
                        yield f"{exit_id}_k{k_stop}_m{m}_tp{tp1}_trail{tk}", {**base, "tp1_r": tp1, "trail_k": tk, "split_tp1": 1.0, "time_stop_bars": 96}
            elif exit_id == "E6":
                for tk in cfg["trail_k"]:
                    yield f"{exit_id}_k{k_stop}_m{m}_trail{tk}", {**base, "trail_k": tk, "time_stop_bars": 192, "be_at_r": 1.0}
            elif exit_id == "E7":
                for ip in cfg["initial_tp_r"]:
                    for dp in cfg["decay_per_bar"]:
                        yield f"{exit_id}_k{k_stop}_m{m}_tp{ip}_dec{dp}", {**base, "initial_tp_r": ip, "decay_per_bar": dp, "time_stop_bars": 96}
            elif exit_id == "E8":
                yield f"{exit_id}_k{k_stop}_m{m}", {**base, "vol_low_k": 1.0, "vol_high_k": 3.0, "time_stop_bars": 96}


def simulate_exit(
    exit_id: str,
    params: dict[str, Any],
    entries: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr_arr: np.ndarray,
    direction: str,
) -> list[Trade]:
    """Dispatch to appropriate simulator. E5-E8 fall back to TP1+time_stop (conservative approximation)."""
    k = params["k_stop"]
    m = params.get("bar_range_mult", 0.0)
    ts = params.get("time_stop_bars", 48)

    if exit_id == "E1":
        return _simulate_stop_tp(entries, high, low, close, atr_arr, direction, k, None, None, 0.0, ts, m)
    if exit_id in ("E2", "E3"):
        return _simulate_stop_tp(entries, high, low, close, atr_arr, direction, k, params["tp1_r"], None, 0.0, ts, m)
    if exit_id == "E4":
        return _simulate_stop_tp(entries, high, low, close, atr_arr, direction, k, params["tp1_r"], params["tp2_r"], params.get("split_tp1", 0.5), ts, m)
    if exit_id == "E5":
        return _simulate_stop_tp_trailing(entries, high, low, close, atr_arr, direction, k, params["tp1_r"], params["trail_k"], ts, m)
    if exit_id == "E6":
        return _simulate_runner(entries, high, low, close, atr_arr, direction, k, params["be_at_r"], params["trail_k"], ts, m)
    if exit_id == "E7":
        return _simulate_time_decay_tp(entries, high, low, close, atr_arr, direction, k, params["initial_tp_r"], params["decay_per_bar"], ts, m)
    if exit_id == "E8":
        return _simulate_regime_aware(entries, high, low, close, atr_arr, direction, params["vol_low_k"], params["vol_high_k"], ts, m)
    raise ValueError(f"Unknown exit_id: {exit_id}")


def _simulate_stop_tp_trailing(
    entries, high, low, close, atr_arr, direction, k_stop, tp1_r, trail_k, time_stop_bars, bar_range_mult,
):
    trades: list[Trade] = []
    last_exit_idx = -1
    n = len(close)
    sign = 1.0 if direction == "long" else -1.0
    idxs = np.where(entries)[0]
    for i in idxs:
        if i <= last_exit_idx or i + 1 >= n or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        entry_idx = i + 1
        entry_price = float(close[i])
        stop_dist = _compute_stop_distance(float(atr_arr[i]), float(high[i]), float(low[i]), k_stop, bar_range_mult)
        if stop_dist <= 0:
            continue
        stop_price = entry_price - sign * stop_dist
        tp1_price = entry_price + sign * tp1_r * stop_dist
        tp1_hit = False
        exit_idx, exit_price, exit_type, realized_r = -1, entry_price, "TIME", 0.0
        hwm = entry_price if direction == "long" else entry_price
        max_bars = time_stop_bars if time_stop_bars > 0 else (n - entry_idx - 1)
        for j in range(entry_idx, min(n, entry_idx + max_bars + 1)):
            h, l = float(high[j]), float(low[j])
            if tp1_hit:
                # trail stop
                if direction == "long":
                    hwm = max(hwm, h)
                    stop_price = max(stop_price, hwm - trail_k * float(atr_arr[j]) if not np.isnan(atr_arr[j]) else stop_price)
                else:
                    hwm = min(hwm, l)
                    stop_price = min(stop_price, hwm + trail_k * float(atr_arr[j]) if not np.isnan(atr_arr[j]) else stop_price)
            stop_hit = (l <= stop_price) if direction == "long" else (h >= stop_price)
            tp1_now = (not tp1_hit) and ((h >= tp1_price) if direction == "long" else (l <= tp1_price))
            if stop_hit:
                realized_r = -1.0 if not tp1_hit else sign * (stop_price - entry_price) / stop_dist
                exit_idx, exit_price, exit_type = j, stop_price, ("STOP" if not tp1_hit else "TRAIL")
                break
            if tp1_now:
                tp1_hit = True
                stop_price = entry_price  # BE after TP1
        if exit_idx < 0:
            exit_idx = min(n - 1, entry_idx + max_bars)
            exit_price = float(close[exit_idx])
            realized_r = sign * (exit_price - entry_price) / stop_dist
            exit_type = "TIME_AFTER_TP1" if tp1_hit else "TIME"
        trades.append(Trade(
            entry_idx=int(entry_idx), exit_idx=int(exit_idx), entry_price=entry_price, exit_price=float(exit_price),
            stop_price=float(stop_price), tp1_price=float(tp1_price), tp2_price=None,
            exit_type=exit_type, r_multiple=float(realized_r), bars_held=int(exit_idx - entry_idx + 1), direction=direction,
        ))
        last_exit_idx = exit_idx
    return trades


def _simulate_runner(entries, high, low, close, atr_arr, direction, k_stop, be_at_r, trail_k, time_stop_bars, bar_range_mult):
    return _simulate_stop_tp_trailing(entries, high, low, close, atr_arr, direction, k_stop, be_at_r, trail_k, time_stop_bars, bar_range_mult)


def _simulate_time_decay_tp(entries, high, low, close, atr_arr, direction, k_stop, initial_tp_r, decay_per_bar, time_stop_bars, bar_range_mult):
    trades: list[Trade] = []
    last_exit_idx = -1
    n = len(close)
    sign = 1.0 if direction == "long" else -1.0
    idxs = np.where(entries)[0]
    for i in idxs:
        if i <= last_exit_idx or i + 1 >= n or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        entry_idx = i + 1
        entry_price = float(close[i])
        stop_dist = _compute_stop_distance(float(atr_arr[i]), float(high[i]), float(low[i]), k_stop, bar_range_mult)
        if stop_dist <= 0:
            continue
        stop_price = entry_price - sign * stop_dist
        exit_idx, exit_price, exit_type, realized_r = -1, entry_price, "TIME", 0.0
        max_bars = time_stop_bars if time_stop_bars > 0 else (n - entry_idx - 1)
        for j in range(entry_idx, min(n, entry_idx + max_bars + 1)):
            held = j - entry_idx
            cur_tp_r = max(0.3, initial_tp_r - decay_per_bar * held)
            tp_price = entry_price + sign * cur_tp_r * stop_dist
            h, l = float(high[j]), float(low[j])
            stop_hit = (l <= stop_price) if direction == "long" else (h >= stop_price)
            tp_hit = (h >= tp_price) if direction == "long" else (l <= tp_price)
            if stop_hit:
                exit_idx, exit_price, exit_type, realized_r = j, stop_price, "STOP", -1.0
                break
            if tp_hit:
                exit_idx, exit_price, exit_type, realized_r = j, tp_price, "TP1", cur_tp_r
                break
        if exit_idx < 0:
            exit_idx = min(n - 1, entry_idx + max_bars)
            exit_price = float(close[exit_idx])
            realized_r = sign * (exit_price - entry_price) / stop_dist
        trades.append(Trade(
            entry_idx=int(entry_idx), exit_idx=int(exit_idx), entry_price=entry_price, exit_price=float(exit_price),
            stop_price=float(stop_price), tp1_price=None, tp2_price=None,
            exit_type=exit_type, r_multiple=float(realized_r), bars_held=int(exit_idx - entry_idx + 1), direction=direction,
        ))
        last_exit_idx = exit_idx
    return trades


def _simulate_regime_aware(entries, high, low, close, atr_arr, direction, vol_low_k, vol_high_k, time_stop_bars, bar_range_mult):
    """Uses ATR/close to classify vol regime at entry bar; picks k_stop accordingly. TP=+1R baseline."""
    trades: list[Trade] = []
    last_exit_idx = -1
    n = len(close)
    sign = 1.0 if direction == "long" else -1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = atr_arr / close
    # Use cross-sectional 66th percentile as cutoff.
    cutoff = np.nanpercentile(atr_pct[~np.isnan(atr_pct)], 66) if np.any(~np.isnan(atr_pct)) else 0.03
    idxs = np.where(entries)[0]
    for i in idxs:
        if i <= last_exit_idx or i + 1 >= n or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        k_stop = vol_high_k if atr_pct[i] > cutoff else vol_low_k
        entry_idx = i + 1
        entry_price = float(close[i])
        stop_dist = _compute_stop_distance(float(atr_arr[i]), float(high[i]), float(low[i]), k_stop, bar_range_mult)
        if stop_dist <= 0:
            continue
        stop_price = entry_price - sign * stop_dist
        tp_price = entry_price + sign * 1.0 * stop_dist
        exit_idx, exit_price, exit_type, realized_r = -1, entry_price, "TIME", 0.0
        max_bars = time_stop_bars if time_stop_bars > 0 else (n - entry_idx - 1)
        for j in range(entry_idx, min(n, entry_idx + max_bars + 1)):
            h, l = float(high[j]), float(low[j])
            stop_hit = (l <= stop_price) if direction == "long" else (h >= stop_price)
            tp_hit = (h >= tp_price) if direction == "long" else (l <= tp_price)
            if stop_hit:
                exit_idx, exit_price, exit_type, realized_r = j, stop_price, "STOP", -1.0
                break
            if tp_hit:
                exit_idx, exit_price, exit_type, realized_r = j, tp_price, "TP1", 1.0
                break
        if exit_idx < 0:
            exit_idx = min(n - 1, entry_idx + max_bars)
            exit_price = float(close[exit_idx])
            realized_r = sign * (exit_price - entry_price) / stop_dist
        trades.append(Trade(
            entry_idx=int(entry_idx), exit_idx=int(exit_idx), entry_price=entry_price, exit_price=float(exit_price),
            stop_price=float(stop_price), tp1_price=float(tp_price), tp2_price=None,
            exit_type=exit_type, r_multiple=float(realized_r), bars_held=int(exit_idx - entry_idx + 1), direction=direction,
        ))
        last_exit_idx = exit_idx
    return trades
