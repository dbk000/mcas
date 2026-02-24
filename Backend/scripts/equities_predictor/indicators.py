"""Registry-driven technical indicators for equities features.

Every indicator function uses the signature:
`fn(bars: list[dict], idx: int, params: dict) -> float | None`.

`compute_all()` evaluates all registered indicators and returns only finite values.

Extension example:
    def compute_my_indicator(bars, idx, params):
        prepared = params["_prepared"]
        # use prepared.close / prepared.volume ...
        return 0.0

    INDICATOR_REGISTRY["my_indicator"] = compute_my_indicator
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from .calendar import ET, is_trading_day

IndicatorFn = Callable[[list[dict[str, Any]], int, dict[str, Any]], float | None]

_PREPARED_CACHE: dict[tuple[int, int, int], "_PreparedBars"] = {}
_MAX_CACHE_ITEMS = 32


@dataclass
class _PreparedBars:
    close: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    ts_epoch_s: np.ndarray
    session_id: np.ndarray
    is_rth: np.ndarray
    is_rth_open: np.ndarray
    open_gap_pct: np.ndarray
    overnight_return: np.ndarray
    cache: dict[str, np.ndarray]


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return np.nan
        out = float(value)
        if np.isfinite(out):
            return out
        return np.nan
    except Exception:
        return np.nan


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cache_key_for_bars(bars: list[dict[str, Any]]) -> tuple[int, int, int]:
    if not bars:
        return (0, 0, 0)
    first_ts = _as_utc_datetime(bars[0].get("bar_start_ts"))
    last_ts = _as_utc_datetime(bars[-1].get("bar_start_ts"))
    first_key = int(first_ts.timestamp() * 1000) if first_ts else 0
    last_key = int(last_ts.timestamp() * 1000) if last_ts else 0
    return (len(bars), first_key, last_key)


def _prepare_bars(bars: list[dict[str, Any]]) -> _PreparedBars:
    key = _cache_key_for_bars(bars)
    cached = _PREPARED_CACHE.get(key)
    if cached is not None:
        return cached

    n = len(bars)
    if n == 0:
        prepared = _PreparedBars(
            close=np.array([], dtype=float),
            open=np.array([], dtype=float),
            high=np.array([], dtype=float),
            low=np.array([], dtype=float),
            volume=np.array([], dtype=float),
            ts_epoch_s=np.array([], dtype=np.int64),
            session_id=np.array([], dtype=np.int64),
            is_rth=np.array([], dtype=bool),
            is_rth_open=np.array([], dtype=bool),
            open_gap_pct=np.array([], dtype=float),
            overnight_return=np.array([], dtype=float),
            cache={},
        )
        _PREPARED_CACHE[key] = prepared
        return prepared

    dts = [_as_utc_datetime(b.get("bar_start_ts")) for b in bars]
    dts = [dt if dt is not None else datetime.fromtimestamp(0, tz=timezone.utc) for dt in dts]
    dts_et = [dt.astimezone(ET) for dt in dts]

    session_state_values = [str(b.get("session_state", "")).upper() for b in bars]
    has_session_state = any(bool(x) for x in session_state_values)
    minutes = np.asarray([(dt.hour * 60) + dt.minute for dt in dts_et], dtype=np.int32)
    trading_mask = np.asarray([is_trading_day(dt.date()) for dt in dts_et], dtype=bool)

    if has_session_state:
        is_rth = np.asarray([s == "RTH" for s in session_state_values], dtype=bool)
        is_rth_open = is_rth & (minutes == ((9 * 60) + 30))
    else:
        is_rth = trading_mask & (minutes >= ((9 * 60) + 30)) & (minutes < (16 * 60))
        is_rth_open = trading_mask & (minutes == ((9 * 60) + 30))

    prepared = _PreparedBars(
        close=np.asarray([_as_float(b.get("close")) for b in bars], dtype=float),
        open=np.asarray([_as_float(b.get("open")) for b in bars], dtype=float),
        high=np.asarray([_as_float(b.get("high")) for b in bars], dtype=float),
        low=np.asarray([_as_float(b.get("low")) for b in bars], dtype=float),
        volume=np.asarray([_as_float(b.get("volume")) for b in bars], dtype=float),
        ts_epoch_s=np.asarray([int(dt.timestamp()) for dt in dts], dtype=np.int64),
        session_id=np.asarray([dt.date().toordinal() for dt in dts_et], dtype=np.int64),
        is_rth=is_rth,
        is_rth_open=is_rth_open,
        open_gap_pct=np.asarray([_as_float(b.get("open_gap_pct")) for b in bars], dtype=float),
        overnight_return=np.asarray([_as_float(b.get("overnight_return")) for b in bars], dtype=float),
        cache={},
    )

    _PREPARED_CACHE[key] = prepared
    if len(_PREPARED_CACHE) > _MAX_CACHE_ITEMS:
        _PREPARED_CACHE.pop(next(iter(_PREPARED_CACHE)))
    return prepared


def _cache_series(prepared: _PreparedBars, key: str, factory: Callable[[], np.ndarray]) -> np.ndarray:
    out = prepared.cache.get(key)
    if out is not None:
        return out
    out = factory()
    prepared.cache[key] = out
    return out


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=float)
    if window <= 0 or values.size < window:
        return out

    finite = np.isfinite(values)
    clean = np.where(finite, values, 0.0)
    ones = np.ones(window, dtype=float)
    sums = np.convolve(clean, ones, mode="valid")
    counts = np.convolve(finite.astype(float), ones, mode="valid")
    out[window - 1 :] = np.where(counts == window, sums / window, np.nan)
    return out


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=float)
    if window <= 1 or values.size < window:
        return out

    finite = np.isfinite(values)
    clean = np.where(finite, values, 0.0)
    ones = np.ones(window, dtype=float)
    sums = np.convolve(clean, ones, mode="valid")
    sums_sq = np.convolve(clean * clean, ones, mode="valid")
    counts = np.convolve(finite.astype(float), ones, mode="valid")
    mean = np.where(counts == window, sums / window, np.nan)
    var = np.where(counts == window, (sums_sq / window) - (mean * mean), np.nan)
    out[window - 1 :] = np.sqrt(np.maximum(var, 0.0))
    return out


def _ema_series(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=float)
    n = values.size
    if period <= 0 or n == 0:
        return out
    if not np.isfinite(values[0]):
        return out

    alpha = 2.0 / (period + 1.0)
    decay = 1.0 - alpha
    powers = decay ** np.arange(n, dtype=float)
    out[0] = values[0]

    if n > 1:
        kernel = alpha * (decay ** np.arange(n - 1, dtype=float))
        conv = np.convolve(values[1:], kernel, mode="full")[: n - 1]
        out[1:] = (values[0] * powers[1:]) + conv
    return out


def _true_range_series(prepared: _PreparedBars) -> np.ndarray:
    high = prepared.high
    low = prepared.low
    close = prepared.close
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce(
        [
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]
    )
    invalid = ~(np.isfinite(high) & np.isfinite(low) & np.isfinite(close))
    tr[invalid] = np.nan
    return tr


def _rsi_series(close: np.ndarray, period: int) -> np.ndarray:
    out = np.full(close.shape, np.nan, dtype=float)
    n = close.size
    if period <= 0 or n <= period:
        return out

    delta = np.diff(close)
    gains = np.clip(delta, 0.0, None)
    losses = np.clip(-delta, 0.0, None)
    avg_gain = _rolling_mean(gains, period)
    avg_loss = _rolling_mean(losses, period)

    rs = np.divide(
        avg_gain,
        avg_loss,
        out=np.full(avg_gain.shape, np.nan, dtype=float),
        where=avg_loss > 0,
    )
    rsi = 100.0 - (100.0 / (1.0 + rs))
    flat_mask = (avg_loss == 0.0) & (avg_gain == 0.0)
    rsi[flat_mask] = 50.0

    out[period:] = rsi[period - 1 :]
    return out


def _overnight_gap_series(prepared: _PreparedBars) -> np.ndarray:
    gap = np.full(prepared.close.shape, np.nan, dtype=float)
    meta_gap = np.where(np.isfinite(prepared.overnight_return), prepared.overnight_return, prepared.open_gap_pct)

    meta_mask = np.isfinite(meta_gap)
    gap[meta_mask] = meta_gap[meta_mask]

    rth_idx = np.flatnonzero(prepared.is_rth)
    if rth_idx.size == 0:
        return gap

    sid = prepared.session_id[rth_idx]
    session_ends = np.r_[sid[1:] != sid[:-1], True]
    session_close_idx = rth_idx[session_ends]

    session_open_idx = np.flatnonzero(prepared.is_rth_open)
    if session_open_idx.size == 0 or session_close_idx.size == 0:
        return gap

    lookup_pos = np.searchsorted(session_close_idx, session_open_idx, side="left") - 1
    valid = lookup_pos >= 0
    if not np.any(valid):
        return gap

    open_idx = session_open_idx[valid]
    prev_close_idx = session_close_idx[lookup_pos[valid]]
    prev_close = prepared.close[prev_close_idx]
    calc = np.divide(
        prepared.open[open_idx] - prev_close,
        prev_close,
        out=np.full(open_idx.shape, np.nan, dtype=float),
        where=np.isfinite(prev_close) & (prev_close != 0.0),
    )

    missing = ~np.isfinite(gap[open_idx])
    if np.any(missing):
        gap_idx = open_idx[missing]
        gap[gap_idx] = calc[missing]
    return gap


def _vwap_session_series(prepared: _PreparedBars) -> np.ndarray:
    out = np.full(prepared.close.shape, np.nan, dtype=float)
    valid_mask = prepared.is_rth & np.isfinite(prepared.volume) & (prepared.volume > 0.0)
    valid_mask = valid_mask & np.isfinite(prepared.high) & np.isfinite(prepared.low) & np.isfinite(prepared.close)
    idx = np.flatnonzero(valid_mask)
    if idx.size == 0:
        return out

    sid = prepared.session_id[idx]
    tp = (prepared.high[idx] + prepared.low[idx] + prepared.close[idx]) / 3.0
    vol = prepared.volume[idx]
    tpv = tp * vol

    is_new_session = np.r_[True, sid[1:] != sid[:-1]]
    group_id = np.cumsum(is_new_session) - 1
    group_starts = np.flatnonzero(is_new_session)

    cum_tpv = np.cumsum(tpv)
    cum_vol = np.cumsum(vol)

    group_tpv_offsets = np.zeros(group_starts.shape, dtype=float)
    group_vol_offsets = np.zeros(group_starts.shape, dtype=float)
    if group_starts.size > 1:
        group_tpv_offsets[1:] = cum_tpv[group_starts[1:] - 1]
        group_vol_offsets[1:] = cum_vol[group_starts[1:] - 1]

    session_tpv = cum_tpv - group_tpv_offsets[group_id]
    session_vol = cum_vol - group_vol_offsets[group_id]
    vwap_vals = np.divide(
        session_tpv,
        session_vol,
        out=np.full(session_tpv.shape, np.nan, dtype=float),
        where=session_vol > 0.0,
    )
    out[idx] = vwap_vals
    return out


def _series_value(series: np.ndarray, idx: int) -> float | None:
    if idx < 0 or idx >= series.size:
        return None
    val = float(series[idx])
    if not np.isfinite(val):
        return None
    return val


def compute_sma(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 20))
    prepared = params["_prepared"]
    key = f"sma_{period}"
    series = _cache_series(prepared, key, lambda: _rolling_mean(prepared.close, period))
    return _series_value(series, idx)


def compute_ema(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 21))
    prepared = params["_prepared"]
    key = f"ema_{period}"
    series = _cache_series(prepared, key, lambda: _ema_series(prepared.close, period))
    if idx < period:
        return None
    return _series_value(series, idx)


def compute_rsi(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 14))
    prepared = params["_prepared"]
    key = f"rsi_{period}"
    series = _cache_series(prepared, key, lambda: _rsi_series(prepared.close, period))
    return _series_value(series, idx)


def compute_atr(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 14))
    prepared = params["_prepared"]
    tr = _cache_series(prepared, "true_range", lambda: _true_range_series(prepared))
    key = f"atr_{period}"
    atr = _cache_series(prepared, key, lambda: _rolling_mean(tr, period))
    return _series_value(atr, idx)


def compute_bollinger_upper(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 20))
    stdev = float(params.get("stdev", 2.0))
    prepared = params["_prepared"]
    sma = _cache_series(prepared, f"sma_{period}", lambda: _rolling_mean(prepared.close, period))
    std = _cache_series(prepared, f"std_{period}", lambda: _rolling_std(prepared.close, period))
    series = sma + (stdev * std)
    return _series_value(series, idx)


def compute_bollinger_lower(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 20))
    stdev = float(params.get("stdev", 2.0))
    prepared = params["_prepared"]
    sma = _cache_series(prepared, f"sma_{period}", lambda: _rolling_mean(prepared.close, period))
    std = _cache_series(prepared, f"std_{period}", lambda: _rolling_std(prepared.close, period))
    series = sma - (stdev * std)
    return _series_value(series, idx)


def compute_bollinger_width(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 20))
    stdev = float(params.get("stdev", 2.0))
    prepared = params["_prepared"]
    sma = _cache_series(prepared, f"sma_{period}", lambda: _rolling_mean(prepared.close, period))
    std = _cache_series(prepared, f"std_{period}", lambda: _rolling_std(prepared.close, period))
    upper = sma + (stdev * std)
    lower = sma - (stdev * std)
    width = np.divide(
        upper - lower,
        sma,
        out=np.full(sma.shape, np.nan, dtype=float),
        where=np.isfinite(sma) & (sma != 0.0),
    )
    return _series_value(width, idx)


def compute_vwap_session(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    prepared = params["_prepared"]
    vwap = _cache_series(prepared, "vwap_session", lambda: _vwap_session_series(prepared))
    return _series_value(vwap, idx)


def compute_roc(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 5))
    prepared = params["_prepared"]
    if idx < period:
        return None
    prev = prepared.close[idx - period]
    cur = prepared.close[idx]
    if not np.isfinite(prev) or not np.isfinite(cur) or prev == 0.0:
        return None
    return float((cur / prev) - 1.0)


def compute_volume_ratio(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 20))
    prepared = params["_prepared"]
    sma_vol = _cache_series(prepared, f"volume_sma_{period}", lambda: _rolling_mean(prepared.volume, period))
    ratio = np.divide(
        prepared.volume,
        sma_vol,
        out=np.full(prepared.volume.shape, np.nan, dtype=float),
        where=np.isfinite(sma_vol) & (sma_vol != 0.0),
    )
    return _series_value(ratio, idx)


def compute_dollar_volume(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    prepared = params["_prepared"]
    dollar_vol = prepared.close * prepared.volume
    return _series_value(dollar_vol, idx)


def compute_realized_vol_20(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    period = int(params.get("period", 20))
    prepared = params["_prepared"]
    if prepared.close.size < period + 1:
        return None

    log_close = np.log(prepared.close)
    log_ret = np.diff(log_close)
    vol = _rolling_std(log_ret, period)
    timeframe = str(bars[0].get("timeframe", "")).lower() if bars else ""
    annualizer = np.sqrt(252.0 * 6.5) if timeframe == "1h" else np.sqrt(252.0)
    annualized = vol * annualizer
    expanded = np.full(prepared.close.shape, np.nan, dtype=float)
    expanded[period:] = annualized[period - 1 :]
    return _series_value(expanded, idx)


def compute_overnight_gap(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    prepared = params["_prepared"]
    series = _cache_series(prepared, "overnight_gap", lambda: _overnight_gap_series(prepared))
    return _series_value(series, idx)


def compute_sector_relative_return(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
    prepared = params["_prepared"]
    if idx <= 0 or idx >= prepared.close.size:
        return None

    prev_close = prepared.close[idx - 1]
    cur_close = prepared.close[idx]
    if not np.isfinite(prev_close) or not np.isfinite(cur_close) or prev_close == 0.0:
        return None
    asset_ret = float((cur_close / prev_close) - 1.0)

    row = bars[idx]
    row_prev = bars[idx - 1]
    direct_return_keys = ("benchmark_return", "benchmark_return_1", "spy_return", "spy_return_1")
    for key in direct_return_keys:
        val = _as_float(row.get(key))
        if np.isfinite(val):
            return float(asset_ret - val)

    close_key_candidates = ("benchmark_close", "spy_close")
    for key in close_key_candidates:
        bench_cur = _as_float(row.get(key))
        bench_prev = _as_float(row_prev.get(key))
        if np.isfinite(bench_cur) and np.isfinite(bench_prev) and bench_prev != 0.0:
            bench_ret = (bench_cur / bench_prev) - 1.0
            return float(asset_ret - bench_ret)

    benchmark_prepared = params.get("_benchmark_prepared")
    if isinstance(benchmark_prepared, _PreparedBars):
        ts = prepared.ts_epoch_s[idx]
        bench_ts = benchmark_prepared.ts_epoch_s
        pos = int(np.searchsorted(bench_ts, ts, side="left"))
        if pos < bench_ts.size and bench_ts[pos] == ts and pos > 0:
            bench_prev = benchmark_prepared.close[pos - 1]
            bench_cur = benchmark_prepared.close[pos]
            if np.isfinite(bench_prev) and np.isfinite(bench_cur) and bench_prev != 0.0:
                bench_ret = (bench_cur / bench_prev) - 1.0
                return float(asset_ret - bench_ret)
    return None


def _bind_params(fn: IndicatorFn, **defaults: Any) -> IndicatorFn:
    def _wrapped(bars: list[dict[str, Any]], idx: int, params: dict[str, Any]) -> float | None:
        merged = dict(defaults)
        merged.update(params)
        return fn(bars, idx, merged)

    _wrapped.__name__ = fn.__name__
    return _wrapped


INDICATOR_REGISTRY: dict[str, IndicatorFn] = {
    "sma_20": _bind_params(compute_sma, period=20),
    "sma_50": _bind_params(compute_sma, period=50),
    "sma_200": _bind_params(compute_sma, period=200),
    "ema_9": _bind_params(compute_ema, period=9),
    "ema_21": _bind_params(compute_ema, period=21),
    "rsi_14": _bind_params(compute_rsi, period=14),
    "atr_14": _bind_params(compute_atr, period=14),
    "bollinger_upper_20_2": _bind_params(compute_bollinger_upper, period=20, stdev=2.0),
    "bollinger_lower_20_2": _bind_params(compute_bollinger_lower, period=20, stdev=2.0),
    "bollinger_width_20_2": _bind_params(compute_bollinger_width, period=20, stdev=2.0),
    "vwap_session": compute_vwap_session,
    "roc_5": _bind_params(compute_roc, period=5),
    "roc_10": _bind_params(compute_roc, period=10),
    "roc_20": _bind_params(compute_roc, period=20),
    "volume_ratio_20": _bind_params(compute_volume_ratio, period=20),
    "dollar_volume": compute_dollar_volume,
    "realized_vol_20": _bind_params(compute_realized_vol_20, period=20),
    "overnight_gap": compute_overnight_gap,
    "sector_relative_return": compute_sector_relative_return,
}


def compute_all(
    bars: list[dict[str, Any]],
    idx: int,
    *,
    benchmark_bars: list[dict[str, Any]] | None = None,
    indicator_params: dict[str, dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Compute every registered indicator for `bars[idx]`.

    Returns only finite values; warmup/incomplete values are omitted.
    """
    if idx < 0 or idx >= len(bars):
        return {}

    prepared = _prepare_bars(bars)
    base_params: dict[str, Any] = {"_prepared": prepared}
    if benchmark_bars:
        base_params["_benchmark_prepared"] = _prepare_bars(benchmark_bars)

    per_indicator = indicator_params or {}
    out: dict[str, float] = {}

    for name, fn in INDICATOR_REGISTRY.items():
        params = dict(base_params)
        params.update(per_indicator.get(name, {}))
        try:
            value = fn(bars, idx, params)
        except Exception:
            continue
        if value is None:
            continue
        val = _as_float(value)
        if np.isfinite(val):
            out[name] = float(val)
    return out


__all__ = ["INDICATOR_REGISTRY", "compute_all"]
