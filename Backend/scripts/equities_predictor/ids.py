"""Deterministic ID helpers for equities predictor entities."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

_NAMESPACE = uuid.UUID("d8d0cf08-3613-46c5-a89b-f14c2f584be4")


def stable_key(*parts: Any) -> str:
    return "|".join(str(p) for p in parts)


def deterministic_uuid(*parts: Any) -> str:
    return str(uuid.uuid5(_NAMESPACE, stable_key(*parts)))


def stable_hash(*parts: Any, length: int = 16) -> str:
    digest = hashlib.sha256(stable_key(*parts).encode("utf-8")).hexdigest()
    return digest[:length]
