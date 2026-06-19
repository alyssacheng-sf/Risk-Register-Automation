"""Tests for notification message builder."""

from datetime import date, timedelta

import pytest

from src.models.risk import Risk
from src.notification_builder import NotificationBuilder


def make_risk(id="a1jTEST", name="Test Risk", impact="High", probability="Medium",
              last_reviewed_date=None, team_name="MC Test", owner_email="test@sf.com",
              owner_name="Test User", **kwargs):
    return Risk(
        id=id, name=name, impact=impact, probability=probability,
        last_reviewed_date=last_reviewed_date, team_name=team_name,
        owner_email=owner_email, owner_name=owner_name, **kwargs,
    )


@pytest.fixture
def builder():
    return NotificationBuilder()


@pytest.fixture
def stale_risks():
    today = date.today()
    return [
        make_risk(id="r1", name="Auth token expiry", impact="High", probability="High",
                  last_reviewed_date=today - timedelta(days=15)),
        make_risk(id="r2", name="DB connection pool", impact="Medium", probability="Medium",
                  last_reviewed_date=today - timedelta(days=20)),
        make_risk(id="r3", name="Email template slow", impact="Low", probability="Low",
                  last_reviewed_date=today - timedelta(days=40)),
    ]


class TestOwnerAlert:
    """Test daily owner alert message."""

    def test_builds_valid_blocks(self, builder, stale_risks):
        msg = builder.build_owner_alert(stale_risks, "test@sf.com")
        assert "text" in msg
        assert "blocks" in msg
        assert len(msg["blocks"]) > 0

    def test_header_shows_count(self, builder, stale_risks):
        msg = builder.build_owner_alert(stale_risks, "test@sf.com")
        header = msg["blocks"][0]
        assert header["type"] == "header"
        assert "3" in header["text"]["text"]

    def test_includes_gus_link(self, builder, stale_risks):
        msg = builder.build_owner_alert(stale_risks, "test@sf.com")
        blocks_text = str(msg["blocks"])
        assert "gus.lightning.force.com" in blocks_text

    def test_includes_risk_details(self, builder, stale_risks):
        msg = builder.build_owner_alert(stale_risks, "test@sf.com")
        blocks_text = str(msg["blocks"])
        assert "Auth token expiry" in blocks_text
        assert "High" in blocks_text

    def test_empty_risks_returns_all_clear(self, builder):
        msg = builder.build_owner_alert([], "test@sf.com")
        assert "All clear" in str(msg["blocks"])

    def test_caps_at_10_risks(self, builder):
        """Should not exceed 10 risk sections to stay within Slack limits."""
        risks = [make_risk(id=f"r{i}", name=f"Risk {i}",
                          last_reviewed_date=date.today() - timedelta(days=30))
                 for i in range(15)]
        msg = builder.build_owner_alert(risks, "test@sf.com")
        # Count section blocks with risk content
        risk_sections = [b for b in msg["blocks"]
                        if b.get("type") == "section" and "accessory" in b]
        assert len(risk_sections) <= 10

    def test_overflow_notice_for_many_risks(self, builder):
        risks = [make_risk(id=f"r{i}", name=f"Risk {i}",
                          last_reviewed_date=date.today() - timedelta(days=30))
                 for i in range(12)]
        msg = builder.build_owner_alert(risks, "test@sf.com")
        blocks_text = str(msg["blocks"])
        assert "more stale risks" in blocks_text

    def test_includes_categories_if_provided(self, builder, stale_risks):
        categories = {"r1": ["security", "infrastructure"]}
        msg = builder.build_owner_alert(stale_risks, "test@sf.com", categories=categories)
        blocks_text = str(msg["blocks"])
        assert "security" in blocks_text

    def test_sorted_by_risk_score(self, builder):
        """Highest priority risks should appear first."""
        risks = [
            make_risk(id="low", name="Low Risk", impact="Low", probability="Low",
                     last_reviewed_date=date.today() - timedelta(days=40)),
            make_risk(id="high", name="High Risk", impact="High", probability="High",
                     last_reviewed_date=date.today() - timedelta(days=10)),
        ]
        msg = builder.build_owner_alert(risks, "test@sf.com")
        blocks_text = str(msg["blocks"])
        # High should appear before Low in the message
        assert blocks_text.index("High Risk") < blocks_text.index("Low Risk")


class TestEscalationAlert:
    """Test manager escalation message."""

    def test_builds_valid_message(self, builder, stale_risks):
        msg = builder.build_escalation_alert(stale_risks, "test@sf.com", "Manager Name")
        assert "text" in msg
        assert "blocks" in msg
        assert "Escalation" in msg["text"]

    def test_includes_owner_email(self, builder, stale_risks):
        msg = builder.build_escalation_alert(stale_risks, "owner@sf.com")
        blocks_text = str(msg["blocks"])
        assert "owner@sf.com" in blocks_text

    def test_includes_manager_greeting(self, builder, stale_risks):
        msg = builder.build_escalation_alert(stale_risks, "test@sf.com", "Jane Manager")
        blocks_text = str(msg["blocks"])
        assert "Jane Manager" in blocks_text


class TestWeeklySummary:
    """Test weekly summary report message."""

    def test_builds_valid_summary(self, builder, stale_risks):
        msg = builder.build_weekly_summary(
            total_open=10,
            total_stale=7,
            compliance_rate=0.3,
            by_team={"MC Test": stale_risks},
            top_stale=stale_risks,
            newly_closed=3,
            newly_added=2,
        )
        assert "text" in msg
        assert "blocks" in msg
        assert "Weekly Summary" in msg["text"]

    def test_includes_metrics(self, builder, stale_risks):
        msg = builder.build_weekly_summary(
            total_open=25, total_stale=18, compliance_rate=0.28,
            by_team={}, top_stale=[], newly_closed=5, newly_added=3,
        )
        blocks_text = str(msg["blocks"])
        assert "25" in blocks_text
        assert "18" in blocks_text
        assert "28%" in blocks_text

    def test_includes_team_breakdown(self, builder, stale_risks):
        msg = builder.build_weekly_summary(
            total_open=10, total_stale=7, compliance_rate=0.3,
            by_team={"MC Email Apps": stale_risks[:2], "MC CIAM": stale_risks[2:]},
            top_stale=stale_risks,
        )
        blocks_text = str(msg["blocks"])
        assert "MC Email Apps" in blocks_text
        assert "MC CIAM" in blocks_text

    def test_includes_dashboard_link(self, builder, stale_risks):
        msg = builder.build_weekly_summary(
            total_open=10, total_stale=7, compliance_rate=0.3,
            by_team={}, top_stale=[],
        )
        blocks_text = str(msg["blocks"])
        assert "01ZEE000001Bgkv2AC" in blocks_text


class TestPlainTextSummary:
    """Test plain text fallback."""

    def test_builds_readable_output(self, builder, stale_risks):
        text = builder.build_plain_text_summary(stale_risks)
        assert "MCE Risk Register" in text
        assert "Auth token expiry" in text
        assert "Dashboard:" in text

    def test_includes_all_risks(self, builder, stale_risks):
        text = builder.build_plain_text_summary(stale_risks)
        for risk in stale_risks:
            assert risk.name in text
