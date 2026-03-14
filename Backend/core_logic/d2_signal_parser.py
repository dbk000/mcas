"""
D2 Signal Parser — Shared WhaleBotPumps parsing logic
======================================================

Extracted from social_pump_cluster_analysis.py for reuse by:
- d2_cluster_detector.py (live cluster detection)
- social_pump_cluster_analysis.py (batch analysis)

Parses WhaleBotPumps Telegram messages into structured signal dicts.
"""

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------

def parse_number(s: str) -> float | None:
    """Parse numbers with K/M/B suffixes (e.g. '1.5M' -> 1500000.0)."""
    if not s:
        return None
    s = s.strip().replace(",", "")
    mult = 1
    if s.endswith("K"):
        mult = 1_000
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    elif s.endswith("B"):
        mult = 1_000_000_000
        s = s[:-1]
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
BEST_BID_RE = re.compile(
    r"Best\s+Bid\s+Vol:\s*([\d,.]+[KMB]?)\s*USDT", re.IGNORECASE
)
BEST_ASK_RE = re.compile(
    r"Best\s+Ask\s+Vol:\s*([\d,.]+[KMB]?)\s*USDT", re.IGNORECASE
)
OI_RE = re.compile(r"OI:\s*([\d,.]+[KMB]?)\s*\(([+-]?[\d,.]+)%?\)")
FUNDING_RE = re.compile(r"Funding:\s*([+-]?[\d,.]+)%")
SHORT_ALERT_RE = re.compile(
    r"Alert:\s*(\w+)/USDT\s*-\s*(pump|dump)", re.IGNORECASE
)


def parse_whalebotpumps(post_id: int, content: str, post_ts) -> dict | None:
    """Parse a WhaleBotPumps post into structured fields.

    Returns dict with keys:
        source_post_id, source, channel, post_ts, symbol, signal_type,
        exchange, price, pct_change, vol_24h, last_vol, vol_change_pct,
        oi, oi_change_pct, funding_rate, bid_vol, ask_vol, spread_pct
    Or None if content doesn't match known formats.
    """
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
            (PRICE_RE, None),
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
