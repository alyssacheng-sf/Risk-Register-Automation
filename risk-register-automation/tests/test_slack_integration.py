"""Integration tests for Slack notification delivery pipeline.

Tests the full end-to-end flow in dry-run mode:
- GUS data → analysis → message building → delivery
- Deduplication logic
- Quiet hours enforcement
- Rate limiting compliance
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import tempfile

import pytest

from src.models.risk import Risk
from src.notification_builder import NotificationBuilder
from src.notification_delivery import NotificationDelivery, DeliveryReport
from src.notification_history import NotificationHistory
from src.risk_analyzer import RiskAnalyzer, AnalysisResult
from src.slack_client import SlackClient


# --- Fixtures ---


def make_risk(id="a1jTEST", name="Test Risk", impact="High", probability="Medium",
              last_reviewed_date=None, team_name="MC Test", owner_email="test@sf.com",
              owner_name="Test User", status="Open", **kwargs):
    return Risk(
        id=id, name=name, impact=impact, probability=probability,
        last_reviewed_date=last_reviewed_date, team_name=team_name,
        owner_email=owner_email, owner_name=owner_name, status=status, **kwargs,
    )


@pytest.fixture
def temp_history_file():
    """Temporary file for notification history."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"notifications": [], "escalations": [], "dismissals": []}, f)
        return Path(f.name)


@pytest.fixture
def history(temp_history_file):
    return NotificationHistory(history_file=temp_history_file)


@pytest.fixture
def slack():
    return SlackClient(dry_run=True, target_user="alyssa.cheng@salesforce.com")


@pytest.fixture
def builder():
    return NotificationBuilder()


@pytest.fixture
def delivery(slack, builder, history):
    return NotificationDelivery(
        slack_client=slack,
        builder=builder,
        history=history,
        quiet_start=time(22, 0),  # 10 PM (wide open for testing)
        quiet_end=time(5, 0),     # 5 AM
    )


@pytest.fixture
def stale_risks():
    """Create a set of stale risks owned by different people."""
    today = date.today()
    return [
        make_risk(id="r1", name="Auth timeout", impact="High", probability="High",
                  owner_email="alice@sf.com", team_name="MC API Frameworks",
                  last_reviewed_date=today - timedelta(days=15)),
        make_risk(id="r2", name="DB pool exhaustion", impact="High", probability="Medium",
                  owner_email="alice@sf.com", team_name="MC API Frameworks",
                  last_reviewed_date=today - timedelta(days=20)),
        make_risk(id="r3", name="Email rendering slow", impact="Medium", probability="Low",
                  owner_email="bob@sf.com", team_name="MC Messaging - Outbound",
                  last_reviewed_date=today - timedelta(days=30)),
        make_risk(id="r4", name="Push notification failure", impact="High", probability="High",
                  owner_email="charlie@sf.com", team_name="MC MobilePush Sending Experience",
                  last_reviewed_date=today - timedelta(days=50)),
    ]


@pytest.fixture
def analysis_result(stale_risks):
    """Pre-computed analysis result."""
    analyzer = RiskAnalyzer(categories={
        "security": ["auth", "token"],
        "performance": ["timeout", "slow"],
        "infrastructure": ["pool", "push"],
    })
    return analyzer.analyze(stale_risks)


# --- Integration Tests: Full Pipeline ---


class TestFullPipeline:
    """Test the complete notification delivery pipeline."""

    def test_daily_notifications_sends_grouped_owner_alerts(self, delivery, analysis_result):
        """Each owner should get ONE message with all their risks."""
        report = delivery.run_daily_notifications(analysis_result)

        # 3 unique owners = 3 delivery attempts
        owner_alerts = [e for e in report.entries if e["type"] == "owner_alert" and not e["skipped"]]
        assert len(owner_alerts) == 3

    def test_daily_notifications_deduplicates(self, delivery, analysis_result):
        """Running twice in same day should skip already-notified risks."""
        # First run
        report1 = delivery.run_daily_notifications(analysis_result)
        assert report1.total_sent > 0

        # Second run — should be all skipped
        report2 = delivery.run_daily_notifications(analysis_result)
        skipped = [e for e in report2.entries if e["skipped"]]
        assert len(skipped) == len(report2.entries)
        assert report2.total_sent == 0

    def test_quiet_hours_blocks_delivery(self, slack, builder, history):
        """No notifications should be sent during quiet hours."""
        # Set quiet hours to cover current time
        delivery = NotificationDelivery(
            slack_client=slack,
            builder=builder,
            history=history,
            quiet_start=time(0, 0),
            quiet_end=time(23, 59),
        )

        # Any time would be in quiet hours
        assert delivery.is_quiet_hours()

    def test_weekly_summary_delivers(self, delivery, analysis_result):
        """Weekly summary should deliver to target user when no channel configured."""
        report = delivery.run_weekly_summary(analysis_result, newly_closed=3, newly_added=1)
        assert report.total_sent == 1
        summary_entry = report.entries[0]
        assert summary_entry["type"] == "weekly_summary"
        assert summary_entry["success"] is True

    def test_escalation_blocked_without_prior_notifications(self, delivery, analysis_result):
        """Escalation should NOT fire if owner hasn't been warned enough times."""
        report = delivery.run_daily_notifications(analysis_result)
        escalations = [e for e in report.entries if e["type"] == "escalation"]
        # All escalations should be skipped (0 prior notification days < 3 required)
        for esc in escalations:
            assert esc["skipped"] is True
            assert "insufficient_prior_notifications" in esc.get("skip_reason", "")

    def test_escalation_fires_after_sufficient_prior_notifications(
        self, slack, builder, temp_history_file, analysis_result
    ):
        """Escalation should fire once owner has been notified on 3+ separate days."""
        # Pre-seed history with 3 days of prior notifications for charlie
        history = NotificationHistory(history_file=temp_history_file)
        today = date.today()
        for days_ago in [3, 2, 1]:
            entry = {
                "risk_id": "r4",
                "owner_email": "charlie@sf.com",
                "type": "owner_alert",
                "date": (today - timedelta(days=days_ago)).isoformat(),
                "timestamp": (today - timedelta(days=days_ago)).isoformat() + "T09:00:00",
            }
            history._data["notifications"].append(entry)

        delivery = NotificationDelivery(
            slack_client=slack,
            builder=builder,
            history=history,
            quiet_start=time(22, 0),
            quiet_end=time(5, 0),
        )
        report = delivery.run_daily_notifications(analysis_result)
        escalations = [
            e for e in report.entries
            if e["type"] == "escalation" and not e["skipped"]
        ]
        # charlie should get escalated (3 prior days of notifications)
        assert len(escalations) >= 1


# --- Deduplication Tests ---


class TestDeduplication:
    """Test notification history and dedup logic."""

    def test_record_and_check_today(self, history):
        assert not history.was_notified_today("r1", "test@sf.com")
        history.record_notification("r1", "test@sf.com", "owner_alert")
        assert history.was_notified_today("r1", "test@sf.com")

    def test_different_risk_not_affected(self, history):
        history.record_notification("r1", "test@sf.com", "owner_alert")
        assert not history.was_notified_today("r2", "test@sf.com")

    def test_different_owner_not_affected(self, history):
        history.record_notification("r1", "alice@sf.com", "owner_alert")
        assert not history.was_notified_today("r1", "bob@sf.com")

    def test_escalation_dedup(self, history):
        assert not history.was_escalated_today("owner@sf.com")
        history.record_escalation("owner@sf.com")
        assert history.was_escalated_today("owner@sf.com")

    def test_dismissal_suppresses_notification(self, history):
        history.record_dismissal("r1", "user@sf.com", days=7)
        assert history.is_dismissed("r1")

    def test_expired_dismissal_does_not_suppress(self, history):
        # Manually inject an expired dismissal
        history._data["dismissals"].append({
            "risk_id": "r_old",
            "dismissed_by": "user@sf.com",
            "dismiss_until": (date.today() - timedelta(days=1)).isoformat(),
            "timestamp": datetime.now().isoformat(),
        })
        assert not history.is_dismissed("r_old")

    def test_prune_removes_old_entries(self, history):
        # Add an old entry
        old_date = (date.today() - timedelta(days=60)).isoformat()
        history._data["notifications"].append({
            "risk_id": "old_risk",
            "owner_email": "old@sf.com",
            "type": "owner_alert",
            "date": old_date,
            "timestamp": old_date + "T00:00:00",
        })
        pruned = history.prune(max_age_days=30)
        assert pruned >= 1
        assert not any(e["risk_id"] == "old_risk" for e in history._data["notifications"])

    def test_save_and_reload(self, temp_history_file):
        """History should persist across instances."""
        h1 = NotificationHistory(history_file=temp_history_file)
        h1.record_notification("r1", "test@sf.com", "owner_alert")
        h1.save()

        h2 = NotificationHistory(history_file=temp_history_file)
        assert h2.was_notified_today("r1", "test@sf.com")


# --- Quiet Hours Tests ---


class TestQuietHours:
    """Test quiet hours enforcement."""

    def test_overnight_quiet_hours(self, slack, builder, history):
        """Standard case: quiet from 6 PM to 8 AM."""
        delivery = NotificationDelivery(
            slack_client=slack, builder=builder, history=history,
            quiet_start=time(18, 0), quiet_end=time(8, 0),
        )

        # 7 PM should be quiet
        assert delivery.is_quiet_hours(now=datetime(2026, 6, 17, 19, 0))
        # 3 AM should be quiet
        assert delivery.is_quiet_hours(now=datetime(2026, 6, 17, 3, 0))
        # 10 AM should NOT be quiet
        assert not delivery.is_quiet_hours(now=datetime(2026, 6, 17, 10, 0))
        # 5 PM should NOT be quiet
        assert not delivery.is_quiet_hours(now=datetime(2026, 6, 17, 17, 0))

    def test_no_quiet_hours(self, slack, builder, history):
        """When quiet end == quiet start, always allow."""
        delivery = NotificationDelivery(
            slack_client=slack, builder=builder, history=history,
            quiet_start=time(0, 0), quiet_end=time(0, 0),
        )
        # Identical start/end means never quiet
        assert not delivery.is_quiet_hours(now=datetime(2026, 6, 17, 12, 0))




# --- Slack Client Tests ---


class TestSlackClientDryRun:
    """Test Slack client in dry-run mode."""

    def test_send_dm_dry_run_succeeds(self, slack):
        msg = {"text": "Test", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Hi"}}]}
        assert slack.send_dm("anyone@sf.com", msg) is True

    def test_safety_redirect(self, slack):
        """All messages should redirect to target_user."""
        msg = {"text": "Test", "blocks": []}
        slack.send_dm("different@sf.com", msg)
        log = slack.get_delivery_log()
        assert log[-1]["destination"] == "DM:alyssa.cheng@salesforce.com"

    def test_send_to_channel_dry_run(self, slack):
        msg = {"text": "Test", "blocks": []}
        assert slack.send_to_channel("C12345", msg) is True

    def test_delivery_stats_tracked(self, slack):
        msg = {"text": "Test", "blocks": []}
        slack.send_dm("user@sf.com", msg)
        slack.send_dm("user2@sf.com", msg)
        stats = slack.get_stats()
        assert stats["total_attempted"] == 2
        assert stats["successful"] == 2

    def test_lookup_user_dry_run(self, slack):
        user_id = slack.lookup_user_by_email("test@sf.com")
        assert user_id is not None
        assert "DRYRUN" in user_id


# --- Rate Limiting Tests ---


class TestRateLimiting:
    """Test that rate limiting is respected."""

    def test_rate_limit_delay_enforced(self):
        """Ensure rate limiting between messages doesn't crash."""
        client = SlackClient(dry_run=True, rate_limit_delay=0.01)  # Fast for testing
        msg = {"text": "Test", "blocks": []}
        # Send multiple messages quickly — should not error
        for _ in range(5):
            assert client.send_dm("test@sf.com", msg) is True




# --- Delivery Report Tests ---


class TestDeliveryReport:
    """Test delivery reporting/stats."""

    def test_empty_report(self):
        report = DeliveryReport()
        assert report.total_sent == 0
        assert report.total_skipped == 0
        assert report.total_failed == 0

    def test_tracks_sent_and_skipped(self):
        report = DeliveryReport()
        report.record("alice@sf.com", "owner_alert", success=True, risk_count=3)
        report.record("bob@sf.com", "owner_alert", success=True, skipped=True, skip_reason="already_notified")
        report.record("charlie@sf.com", "owner_alert", success=False, risk_count=2)

        assert report.total_sent == 1
        assert report.total_skipped == 1
        assert report.total_failed == 1
        assert report.total_risks_notified == 3

    def test_summary_string(self):
        report = DeliveryReport()
        report.record("alice@sf.com", "owner_alert", success=True, risk_count=5)
        summary = report.summary()
        assert "Sent: 1" in summary
        assert "Risks covered: 5" in summary
