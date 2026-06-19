"""Tests for the owner mapper (email -> Slack ID resolution)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.owner_mapper import OwnerMapper, NotificationTarget


@pytest.fixture
def tmp_cache(tmp_path):
    """Create a temporary cache file."""
    return tmp_path / "test_cache.json"


@pytest.fixture
def mapper_with_cache(tmp_cache):
    """Mapper with a pre-populated cache."""
    cache_data = {
        "alice@salesforce.com": "U001ALICE",
        "bob@salesforce.com": "U002BOB",
    }
    tmp_cache.write_text(json.dumps(cache_data))
    return OwnerMapper(cache_file=tmp_cache)


@pytest.fixture
def mapper_with_teams(tmp_cache):
    """Mapper with team channel mappings."""
    return OwnerMapper(
        cache_file=tmp_cache,
        team_channels={
            "MC CIAM": "C001CIAM",
            "MC Email Apps": "C002EMAIL",
        },
    )


class TestSlackIdLookup:
    """Test email -> Slack ID resolution."""

    def test_returns_from_cache(self, mapper_with_cache):
        assert mapper_with_cache.get_slack_id("alice@salesforce.com") == "U001ALICE"

    def test_case_insensitive(self, mapper_with_cache):
        assert mapper_with_cache.get_slack_id("Alice@Salesforce.com") == "U001ALICE"

    def test_returns_none_for_unknown(self, mapper_with_cache):
        assert mapper_with_cache.get_slack_id("unknown@salesforce.com") is None

    def test_returns_none_for_empty(self, mapper_with_cache):
        assert mapper_with_cache.get_slack_id("") is None
        assert mapper_with_cache.get_slack_id(None) is None

    def test_slack_api_fallback(self, tmp_cache):
        """Should call Slack API when not in cache."""
        mock_slack = MagicMock()
        mock_slack.users_lookupByEmail.return_value = {
            "ok": True,
            "user": {"id": "U003NEW"},
        }
        mapper = OwnerMapper(slack_client=mock_slack, cache_file=tmp_cache)
        result = mapper.get_slack_id("new@salesforce.com")
        assert result == "U003NEW"
        mock_slack.users_lookupByEmail.assert_called_once_with(email="new@salesforce.com")

    def test_caches_slack_api_result(self, tmp_cache):
        """Should save Slack API results to cache."""
        mock_slack = MagicMock()
        mock_slack.users_lookupByEmail.return_value = {
            "ok": True,
            "user": {"id": "U003NEW"},
        }
        mapper = OwnerMapper(slack_client=mock_slack, cache_file=tmp_cache)
        mapper.get_slack_id("new@salesforce.com")

        # Second call should hit cache, not API
        mapper.get_slack_id("new@salesforce.com")
        assert mock_slack.users_lookupByEmail.call_count == 1

    def test_handles_slack_api_failure(self, tmp_cache):
        """Should return None if Slack API fails."""
        mock_slack = MagicMock()
        mock_slack.users_lookupByEmail.side_effect = Exception("Rate limited")
        mapper = OwnerMapper(slack_client=mock_slack, cache_file=tmp_cache)
        result = mapper.get_slack_id("fail@salesforce.com")
        assert result is None


class TestNotificationTarget:
    """Test notification target resolution."""

    def test_direct_message_when_in_cache(self, mapper_with_cache):
        target = mapper_with_cache.get_notification_target("alice@salesforce.com")
        assert target.type == "direct_message"
        assert target.destination == "U001ALICE"
        assert target.is_resolved is True

    def test_channel_fallback_when_team_known(self, mapper_with_teams):
        target = mapper_with_teams.get_notification_target(
            "unknown@salesforce.com", team_name="MC CIAM"
        )
        assert target.type == "channel"
        assert target.destination == "C001CIAM"
        assert target.is_resolved is True

    def test_fallback_when_nothing_resolved(self, mapper_with_teams):
        target = mapper_with_teams.get_notification_target(
            "unknown@salesforce.com", team_name="Unknown Team"
        )
        assert target.type == "fallback"
        assert target.destination is None
        assert target.is_resolved is False

    def test_prefers_dm_over_channel(self, tmp_cache):
        """Even if team channel exists, prefer DM when Slack ID is known."""
        cache_data = {"known@salesforce.com": "U001KNOWN"}
        tmp_cache.write_text(json.dumps(cache_data))
        mapper = OwnerMapper(
            cache_file=tmp_cache,
            team_channels={"MC Test": "C001TEST"},
        )
        target = mapper.get_notification_target("known@salesforce.com", team_name="MC Test")
        assert target.type == "direct_message"


class TestBulkResolve:
    """Test bulk email resolution."""

    def test_resolves_multiple(self, mapper_with_cache):
        results = mapper_with_cache.bulk_resolve([
            "alice@salesforce.com",
            "bob@salesforce.com",
            "unknown@salesforce.com",
        ])
        assert results["alice@salesforce.com"] == "U001ALICE"
        assert results["bob@salesforce.com"] == "U002BOB"
        assert results["unknown@salesforce.com"] is None

    def test_deduplicates_emails(self, mapper_with_cache):
        """Should only look up each email once even if duplicated."""
        results = mapper_with_cache.bulk_resolve([
            "alice@salesforce.com",
            "alice@salesforce.com",
            "alice@salesforce.com",
        ])
        assert len(results) == 1


class TestCacheManagement:
    """Test cache persistence."""

    def test_add_to_cache(self, tmp_cache):
        mapper = OwnerMapper(cache_file=tmp_cache)
        mapper.add_to_cache("new@salesforce.com", "U999NEW")
        assert mapper.get_slack_id("new@salesforce.com") == "U999NEW"

    def test_cache_persists_to_disk(self, tmp_cache):
        mapper = OwnerMapper(cache_file=tmp_cache)
        mapper.add_to_cache("persisted@salesforce.com", "U999PERSIST")

        # Create new mapper instance — should load from disk
        mapper2 = OwnerMapper(cache_file=tmp_cache)
        assert mapper2.get_slack_id("persisted@salesforce.com") == "U999PERSIST"

    def test_handles_missing_cache_file(self, tmp_path):
        cache = tmp_path / "nonexistent" / "cache.json"
        mapper = OwnerMapper(cache_file=cache)
        assert mapper.get_slack_id("any@salesforce.com") is None

    def test_handles_corrupt_cache_file(self, tmp_cache):
        tmp_cache.write_text("not valid json {{{{")
        mapper = OwnerMapper(cache_file=tmp_cache)
        # Should not crash, just start with empty cache
        assert mapper.get_slack_id("any@salesforce.com") is None

    def test_cache_stats(self, mapper_with_cache):
        stats = mapper_with_cache.get_cache_stats()
        assert stats["total_cached"] == 2
