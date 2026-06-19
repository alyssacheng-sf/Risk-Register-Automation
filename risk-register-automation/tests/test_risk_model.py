"""Tests for the Risk data model."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from src.models.risk import Risk, _parse_date, _parse_datetime


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestRiskFromGusRecord:
    """Test Risk.from_gus_record() with real GUS data."""

    @pytest.fixture
    def sample_records(self):
        with open(FIXTURES_DIR / "sample_risks.json") as f:
            return json.load(f)

    def test_parses_all_sample_records(self, sample_records):
        """All sample records should parse without errors."""
        risks = [Risk.from_gus_record(r) for r in sample_records]
        assert len(risks) == len(sample_records)
        for risk in risks:
            assert risk.id is not None
            assert risk.name is not None

    def test_parses_basic_fields(self, sample_records):
        """Core fields are extracted correctly."""
        risk = Risk.from_gus_record(sample_records[0])
        assert risk.id.startswith("a1j")
        assert len(risk.name) > 0
        assert risk.status in ("Open", "Closed", "Escalated", "Accepted", None)

    def test_parses_owner_relationship(self, sample_records):
        """Owner name and email are extracted from nested object."""
        risk = Risk.from_gus_record(sample_records[0])
        assert risk.owner_name is not None
        assert risk.owner_email is not None
        assert "@" in risk.owner_email

    def test_parses_team_relationship(self, sample_records):
        """Team name is extracted from nested Team__r."""
        risk = Risk.from_gus_record(sample_records[0])
        assert risk.team_name is not None

    def test_handles_null_risk_owner(self):
        """Should handle records where Risk_Owner__r is null."""
        record = {
            "Id": "a1jTEST001",
            "Name": "Test Risk",
            "Status__c": "Open",
            "Risk_Owner__r": None,
            "Owner": {"Name": "Test", "Email": "test@salesforce.com"},
            "Team__r": {"Name": "MC Test"},
        }
        risk = Risk.from_gus_record(record)
        assert risk.risk_owner_name is None

    def test_handles_minimal_record(self):
        """Should work with only Id and Name."""
        record = {"Id": "a1jMINIMAL", "Name": "Minimal Risk"}
        risk = Risk.from_gus_record(record)
        assert risk.id == "a1jMINIMAL"
        assert risk.name == "Minimal Risk"
        assert risk.status is None
        assert risk.impact is None


class TestRiskProperties:
    """Test computed properties on Risk."""

    def _make_risk(self, **kwargs):
        """Helper to create a Risk with defaults."""
        defaults = {
            "id": "a1jTEST",
            "name": "Test Risk",
            "status": "Open",
            "impact": "High",
            "probability": "Medium",
        }
        defaults.update(kwargs)
        return Risk(**defaults)

    def test_days_since_review_with_recent_review(self):
        risk = self._make_risk(last_reviewed_date=date.today() - timedelta(days=5))
        assert risk.days_since_review == 5

    def test_days_since_review_with_no_review_uses_identified_on(self):
        risk = self._make_risk(identified_on=date.today() - timedelta(days=30))
        assert risk.days_since_review == 30

    def test_days_since_review_with_nothing_returns_none(self):
        risk = self._make_risk(
            last_reviewed_date=None,
            identified_on=None,
            created_date=None,
        )
        assert risk.days_since_review is None

    def test_is_stale_high_impact_7_days(self):
        """High impact risk is stale after 7 days."""
        risk = self._make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=8),
        )
        assert risk.is_stale is True

    def test_is_not_stale_high_impact_under_threshold(self):
        risk = self._make_risk(
            impact="High",
            last_reviewed_date=date.today() - timedelta(days=5),
        )
        assert risk.is_stale is False

    def test_is_stale_medium_impact_14_days(self):
        risk = self._make_risk(
            impact="Medium",
            last_reviewed_date=date.today() - timedelta(days=15),
        )
        assert risk.is_stale is True

    def test_is_stale_low_impact_30_days(self):
        risk = self._make_risk(
            impact="Low",
            last_reviewed_date=date.today() - timedelta(days=31),
        )
        assert risk.is_stale is True

    def test_is_stale_no_review_date(self):
        """Risk with no review date should be considered stale."""
        risk = self._make_risk(last_reviewed_date=None, identified_on=None, created_date=None)
        assert risk.is_stale is True

    def test_stale_threshold_days(self):
        assert self._make_risk(impact="High").stale_threshold_days == 7
        assert self._make_risk(impact="Medium").stale_threshold_days == 14
        assert self._make_risk(impact="Low").stale_threshold_days == 30
        assert self._make_risk(impact=None).stale_threshold_days == 14

    def test_risk_score(self):
        assert self._make_risk(impact="High", probability="High").risk_score == 9
        assert self._make_risk(impact="High", probability="Medium").risk_score == 6
        assert self._make_risk(impact="High", probability="Low").risk_score == 3
        assert self._make_risk(impact="Low", probability="Low").risk_score == 1

    def test_is_open(self):
        assert self._make_risk(status="Open").is_open is True
        assert self._make_risk(status="Escalated").is_open is True
        assert self._make_risk(status=None).is_open is True
        assert self._make_risk(status="Closed").is_open is False
        assert self._make_risk(status="Accepted").is_open is False

    def test_gus_url(self):
        risk = self._make_risk(id="a1jEE00000TEST")
        assert risk.gus_url == "https://gus.lightning.force.com/lightning/r/PPM_Project_Risk__c/a1jEE00000TEST/view"

    def test_to_dict(self):
        risk = self._make_risk(
            impact="High",
            probability="Medium",
            last_reviewed_date=date.today() - timedelta(days=5),
            team_name="MC Test",
            owner_email="test@salesforce.com",
        )
        d = risk.to_dict()
        assert d["id"] == "a1jTEST"
        assert d["impact"] == "High"
        assert d["days_since_review"] == 5
        assert d["is_stale"] is False
        assert d["risk_score"] == 6
        assert d["team_name"] == "MC Test"
        assert "gus_url" in d


class TestDateParsing:
    """Test date/datetime parsing helpers."""

    def test_parse_date_valid(self):
        assert _parse_date("2024-10-16") == date(2024, 10, 16)

    def test_parse_date_none(self):
        assert _parse_date(None) is None

    def test_parse_date_empty(self):
        assert _parse_date("") is None

    def test_parse_date_invalid(self):
        assert _parse_date("not-a-date") is None

    def test_parse_datetime_valid(self):
        result = _parse_datetime("2024-10-16T11:37:27.000+0000")
        assert result is not None
        assert result.year == 2024
        assert result.month == 10
        assert result.day == 16

    def test_parse_datetime_none(self):
        assert _parse_datetime(None) is None
