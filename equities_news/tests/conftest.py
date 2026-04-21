"""Pytest conftest for equities_news tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source_authoring"))
sys.path.insert(0, str(ROOT / "source_authoring" / "phase_a"))
sys.path.insert(0, str(ROOT / "source_authoring" / "phase_b"))
sys.path.insert(0, str(ROOT / "source_authoring" / "verification"))
