"""
User Strategy Evaluation — Pure math. No I/O. No Redis. No DB.

Evaluates AND-joined conditions against indicator snapshot values.
Supports literal thresholds and field-to-field comparisons.
Supports crossover operators with both literal and ref_field comparands.

Importable by: governance worker, future backtest, API health-check, unit tests.
"""

from __future__ import annotations


OPERATORS = {
    ">": lambda cur, val: cur > val,
    "<": lambda cur, val: cur < val,
    ">=": lambda cur, val: cur >= val,
    "<=": lambda cur, val: cur <= val,
}


def _crosses_above(
    cur: float, prev: float | None, val: float, prev_val: float | None,
) -> bool:
    """prev <= prev_val AND cur > val (transition detection)."""
    if prev is None or prev_val is None:
        return False
    return prev <= prev_val and cur > val


def _crosses_below(
    cur: float, prev: float | None, val: float, prev_val: float | None,
) -> bool:
    """prev >= prev_val AND cur < val (transition detection)."""
    if prev is None or prev_val is None:
        return False
    return prev >= prev_val and cur < val


def evaluate_conditions(
    conditions: list[dict],
    current: dict[str, float | None],
    previous: dict[str, float | None] | None,
) -> tuple[bool, dict[str, float]]:
    """Evaluate AND-joined conditions against indicator snapshot values.

    Args:
        conditions: [{field, op, value/ref_field}, ...]
        current: {field_name: current_value} from ind_snap
        previous: {field_name: previous_value} or None (first cycle)

    Returns:
        (all_passed, {field: actual_value} snapshot of evaluated conditions)
    """
    snapshot = {}
    all_passed = True

    for cond in conditions:
        field = cond.get("field")
        op = cond.get("op")
        ref_field = cond.get("ref_field")

        # Resolve current field value
        cur_val = current.get(field) if field else None
        if cur_val is None:
            all_passed = False
            continue

        # Convert to float (Redis values may be strings)
        try:
            cur_val = float(cur_val)
        except (ValueError, TypeError):
            all_passed = False
            continue

        snapshot[field] = cur_val

        # Resolve comparand: ref_field or literal value
        if ref_field:
            comparand = current.get(ref_field)
            if comparand is None:
                all_passed = False
                continue
            try:
                comparand = float(comparand)
            except (ValueError, TypeError):
                all_passed = False
                continue
            snapshot[ref_field] = comparand
        else:
            raw_val = cond.get("value")
            if raw_val is None:
                all_passed = False
                continue
            try:
                comparand = float(raw_val)
            except (ValueError, TypeError):
                all_passed = False
                continue

        # Evaluate operator
        if op in OPERATORS:
            if not OPERATORS[op](cur_val, comparand):
                all_passed = False
        elif op == "crosses_above":
            prev_field_val = _safe_float(previous.get(field)) if previous else None
            if ref_field:
                prev_comparand = _safe_float(previous.get(ref_field)) if previous else None
            else:
                prev_comparand = comparand  # literal doesn't change
            if not _crosses_above(cur_val, prev_field_val, comparand, prev_comparand):
                all_passed = False
        elif op == "crosses_below":
            prev_field_val = _safe_float(previous.get(field)) if previous else None
            if ref_field:
                prev_comparand = _safe_float(previous.get(ref_field)) if previous else None
            else:
                prev_comparand = comparand  # literal doesn't change
            if not _crosses_below(cur_val, prev_field_val, comparand, prev_comparand):
                all_passed = False
        else:
            # Unknown operator — condition fails
            all_passed = False

    return all_passed, snapshot


def _safe_float(val) -> float | None:
    """Convert to float safely, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
