"""Tests for the GUS API client.

Includes both unit tests (mocked) and an optional integration test
that hits real GUS (skipped unless --run-integration is passed).
"""

import json
import subprocess
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from src.gus_client import GUSClient, GUSClientError, RISK_FIELDS, MCE_TEAM_FILTER


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def client():
    return GUSClient(target_org="gus", max_retries=2)


@pytest.fixture
def sample_records():
    with open(FIXTURES_DIR / "sample_risks.json") as f:
        return json.load(f)


@pytest.fixture
def mock_sf_success(sample_records):
    """Mock a successful sf data query response."""
    response = json.dumps({"result": {"records": sample_records}})
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = response
    mock_result.stderr = ""
    return mock_result


@pytest.fixture
def mock_sf_failure():
    """Mock a failed sf data query response."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "ERROR: Session expired or invalid"
    return mock_result


class TestGUSClientQueries:
    """Test query building and execution."""

    @patch("src.gus_client.subprocess.run")
    def test_get_open_risks_success(self, mock_run, client, mock_sf_success):
        mock_run.return_value = mock_sf_success
        risks = client.get_open_risks()
        assert len(risks) > 0
        # Verify sf was called with correct args
        call_args = mock_run.call_args[0][0]
        assert "sf" in call_args
        assert "--json" in call_args
        # Verify the query includes status filter
        query = call_args[call_args.index("-q") + 1]
        assert "Status__c IN ('Open', 'Escalated')" in query

    @patch("src.gus_client.subprocess.run")
    def test_get_all_risks_with_limit(self, mock_run, client, mock_sf_success):
        mock_run.return_value = mock_sf_success
        client.get_all_risks(limit=10)
        query = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-q") + 1]
        assert "LIMIT 10" in query

    @patch("src.gus_client.subprocess.run")
    def test_get_risks_by_team(self, mock_run, client, mock_sf_success):
        mock_run.return_value = mock_sf_success
        client.get_risks_by_team("MC MobileConnect")
        query = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-q") + 1]
        assert "MC MobileConnect" in query

    @patch("src.gus_client.subprocess.run")
    def test_get_stale_risks_filters_correctly(self, mock_run, client, mock_sf_success):
        """get_stale_risks should return only stale risks from the full set."""
        mock_run.return_value = mock_sf_success
        stale = client.get_stale_risks()
        # All returned risks should be stale
        for risk in stale:
            assert risk.is_stale is True

    @patch("src.gus_client.subprocess.run")
    def test_get_risk_by_id(self, mock_run, client, sample_records):
        single = json.dumps({"result": {"records": [sample_records[0]]}})
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = single
        mock_run.return_value = mock_result

        risk = client.get_risk_by_id("a1jEE00000TEST")
        assert risk is not None
        assert risk.id == sample_records[0]["Id"]


class TestGUSClientErrorHandling:
    """Test retry and error behavior."""

    @patch("src.gus_client.subprocess.run")
    @patch("src.gus_client.time.sleep")  # Don't actually sleep in tests
    def test_retries_on_failure(self, mock_sleep, mock_run, client, mock_sf_failure, mock_sf_success):
        """Should retry and succeed on second attempt."""
        mock_run.side_effect = [mock_sf_failure, mock_sf_success]
        risks = client.get_open_risks()
        assert len(risks) > 0
        assert mock_run.call_count == 2

    @patch("src.gus_client.subprocess.run")
    @patch("src.gus_client.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep, mock_run, client, mock_sf_failure):
        """Should raise GUSClientError after exhausting retries."""
        mock_run.return_value = mock_sf_failure
        with pytest.raises(GUSClientError):
            client.get_open_risks()
        assert mock_run.call_count == client.max_retries

    @patch("src.gus_client.subprocess.run")
    @patch("src.gus_client.time.sleep")
    def test_handles_timeout(self, mock_sleep, mock_run, client):
        """Should handle subprocess timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sf", timeout=120)
        with pytest.raises(GUSClientError, match="timed out"):
            client.get_open_risks()

    @patch("src.gus_client.subprocess.run")
    def test_handles_invalid_json(self, mock_run, client):
        """Should raise on malformed JSON response."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json at all"
        mock_run.return_value = mock_result
        with pytest.raises(GUSClientError, match="Invalid JSON"):
            client.get_open_risks()

    @patch("src.gus_client.subprocess.run")
    def test_skips_unparseable_records(self, mock_run, client):
        """Should skip records that fail to parse, not crash."""
        records = [
            {"Id": "good1", "Name": "Good Risk", "Status__c": "Open"},
            {"not_id": "bad"},  # Missing Id field
            {"Id": "good2", "Name": "Also Good", "Status__c": "Open"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"result": {"records": records}})
        mock_run.return_value = mock_result

        risks = client.get_open_risks()
        # Should get 2 good records, skip the bad one
        assert len(risks) == 2


class TestGUSClientHealthCheck:
    """Test health check method."""

    @patch("src.gus_client.subprocess.run")
    def test_health_check_success(self, mock_run, client):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        assert client.health_check() is True

    @patch("src.gus_client.subprocess.run")
    def test_health_check_failure(self, mock_run, client):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        assert client.health_check() is False


class TestQueryBuilding:
    """Test that queries are constructed correctly."""

    def test_risk_fields_includes_key_fields(self):
        """Verify all critical fields are in the query."""
        assert "Id" in RISK_FIELDS
        assert "Name" in RISK_FIELDS
        assert "Status__c" in RISK_FIELDS
        assert "Impact__c" in RISK_FIELDS
        assert "Probability__c" in RISK_FIELDS
        assert "Last_Reviewed_Date__c" in RISK_FIELDS
        assert "Owner.Name" in RISK_FIELDS
        assert "Owner.Email" in RISK_FIELDS
        assert "Team__r.Name" in RISK_FIELDS

    def test_mce_team_filter_covers_key_patterns(self):
        """Verify team filter catches MCE team naming patterns."""
        assert "MC " in MCE_TEAM_FILTER  # space after MC to avoid false matches
        assert "E360" in MCE_TEAM_FILTER
        assert "Engagement" in MCE_TEAM_FILTER
        assert "SFMC" in MCE_TEAM_FILTER


# --- Integration Tests (skip by default) ---

@pytest.mark.integration
class TestGUSClientIntegration:
    """Integration tests that hit real GUS. Run with: pytest -m integration"""

    def test_can_fetch_open_risks(self):
        client = GUSClient()
        risks = client.get_open_risks(limit=5)
        assert len(risks) > 0
        for risk in risks:
            assert risk.status in ("Open", "Escalated")

    def test_health_check_passes(self):
        client = GUSClient()
        assert client.health_check() is True
