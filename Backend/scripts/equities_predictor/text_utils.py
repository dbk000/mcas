"""Text utilities: normalization, hashing, sentiment, simhash, anti-bot scoring."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable


_WORD_RE = re.compile(r"[A-Za-z0-9$]{2,}")

POSITIVE_WORDS = {
    "beat", "beats", "upgrade", "upgrades", "growth", "record", "strong", "outperform",
    "surge", "expands", "expansion", "profit", "profits", "raised", "raise", "buyback",
    "approval", "approved", "win", "wins", "innovation", "momentum", "guidance",
}

NEGATIVE_WORDS = {
    "miss", "misses", "downgrade", "downgrades", "weak", "decline", "drops", "drop",
    "loss", "losses", "lawsuit", "probe", "investigation", "fraud", "cuts", "cut",
    "warning", "warns", "bankruptcy", "default", "recall", "delay", "delays",
}


def normalize_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def content_hash(*parts: str) -> str:
    normalized = "|".join(normalize_text(p) for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def simple_sentiment_score(text: str) -> float:
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    pos = sum(1 for t in tokens if t in POSITIVE_WORDS)
    neg = sum(1 for t in tokens if t in NEGATIVE_WORDS)
    return (pos - neg) / max(1, len(tokens))


def simhash_64(tokens: Iterable[str]) -> int:
    v = [0] * 64
    for token in tokens:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            bit = (h >> i) & 1
            v[i] += 1 if bit else -1
    out = 0
    for i, val in enumerate(v):
        if val >= 0:
            out |= (1 << i)
    # Persist as signed BIGINT-compatible value.
    if out >= (1 << 63):
        out -= (1 << 64)
    return out


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def anti_bot_score(title: str, publisher: str | None = None) -> float:
    text = (title or "")
    if not text:
        return 0.0

    score = 0.0

    upper_ratio = sum(1 for c in text if c.isupper()) / max(1, len(text))
    if upper_ratio > 0.45:
        score += 0.3

    dollar_tokens = re.findall(r"\$[A-Z]{1,5}", text)
    if len(dollar_tokens) >= 3:
        score += 0.3

    if "!!!" in text or "??" in text:
        score += 0.2

    if publisher and any(x in publisher.lower() for x in ["telegram", "discord", "unknown"]):
        score += 0.2

    return min(1.0, score)
