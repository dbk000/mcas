"""Phase B sector agent write-boundary enforcement tests."""
from __future__ import annotations

import pytest

from sector_agent import SectorAgent


def _base_entry(sid: str) -> dict:
    return {
        "source_id": sid,
        "source_name": "Test",
        "url": "https://example.com",
        "adapter_type": "rss",
        "jurisdiction": "US",
        "language": "en",
        "cost_tier": "free",
        "update_cadence": "daily",
        "event_types_covered": ["earnings_report"],
        "edge_weight_prior": 0.5,
        "typical_lead_time_vs_english_minutes": 0,
    }


def test_agent_rejects_unknown_sector_slug():
    with pytest.raises(ValueError):
        SectorAgent("nonexistent_sector")


def test_agent_rejects_wrong_sector_source_id():
    agent = SectorAgent("health_care")
    with pytest.raises(ValueError, match="write boundary"):
        # Try to author under 'sector.energy.*' while assigned health_care.
        agent.author_sector_yaml([_base_entry("sector.energy.fake_source")])


def test_agent_rejects_universal_source_id():
    agent = SectorAgent("information_technology")
    with pytest.raises(ValueError, match="write boundary"):
        agent.author_sector_yaml([_base_entry("universal.fake")])


def test_agent_rejects_geo_source_id():
    agent = SectorAgent("industrials")
    with pytest.raises(ValueError, match="write boundary"):
        agent.author_sector_yaml([_base_entry("geo.us.fake")])


def test_agent_rejects_bad_sub_industry_slug():
    agent = SectorAgent("health_care")
    with pytest.raises(ValueError, match="invalid"):
        agent.author_sub_industry_yamls({
            "BADSLUG-uppercase": [_base_entry("sub_industry.health_care.BADSLUG-uppercase.test")]
        })


def test_agent_rejects_sub_industry_under_wrong_sector():
    agent = SectorAgent("materials")
    with pytest.raises(ValueError, match="write boundary"):
        agent.author_sub_industry_yamls({
            "defence": [_base_entry("sub_industry.industrials.defence.fake_source")]
        })


def test_overlay_enum_enforced():
    agent = SectorAgent("consumer_staples")
    from sector_agent import SymbolRow
    row = SymbolRow(row_idx=5, ticker="TEST", company="Test Co", country="United States",
                    exchange="NYSE", cap_tier="Mega")
    with pytest.raises(ValueError, match="earnings_transcript_provider"):
        agent.fill_overlay(
            row,
            ir_url=None, press_release_feed_url=None, official_x_handle=None,
            official_linkedin_url=None, earnings_transcript_provider="invalid_provider",
            local_regulatory_filings_url=None, sub_industry_override=None,
            confidence=1.0,
        )
