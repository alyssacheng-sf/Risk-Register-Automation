"""Tests for metrics calculator, data store, and report generator."""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.models.risk import Risk
from src.data_store import DataStore
from src.metrics_calculator import MetricsCalculator, WeeklyMetrics
from src.report_generator import ReportGenerator


def make_risk(id="a1jTEST", name="Test Risk", impact="High", probability="Medium",
              last_reviewed_date=None, team_name="MC Test", owner_email="test@sf.com",
              owner_name="Test User", status="Open", **kwargs):
    return Risk(
        id=id, name=name, impact=impact, probability=probability,
        last_reviewed_date=last_reviewed_date, team_name=team_name,
        owner_email=owner_email, owner_name=owner_name, status=status, **kwargs,
    )


@pytest.fixture
def temp_snapshot_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def store(temp_snapshot_dir):
    return DataStore(snapshot_dir=temp_snapshot_dir)


@pytest.fixture
def sample_risks():
    today = date.today()
    return [
        make_risk(id="r1", name="Auth timeout", impact="High", probability="High",
                  team_name="MC API Frameworks", owner_email="alice@sf.com",
                  last_reviewed_date=today - timedelta(days=15)),
        make_risk(id="r2", name="DB pool", impact="High", probability="Medium",
                  team_name="MC API Frameworks", owner_email="alice@sf.com",
                  last_reviewed_date=today - timedelta(days=20)),
        make_risk(id="r3", name="Email slow", impact="Medium", probability="Low",
                  team_name="MC Messaging - Outbound", owner_email="bob@sf.com",
                  last_reviewed_date=today - timedelta(days=30)),
        make_risk(id="r4", name="Push fail", impact="High", probability="High",
                  team_name="MC MobilePush Sending Experience", owner_email="charlie@sf.com",
                  last_reviewed_date=today - timedelta(days=50)),
        make_risk(id="r5", name="New risk", impact="Low", probability="Low",
                  team_name="MC CD", owner_email="dave@sf.com",
                  last_reviewed_date=today - timedelta(days=2)),  # NOT stale
    ]


# --- DataStore Tests ---


class TestDataStore:

    def test_save_and_load_snapshot(self, store, sample_risks):
        store.save_snapshot(sample_risks)
        snapshot = store.load_snapshot(date.today())
        assert snapshot is not None
        assert snapshot["total_open"] == 5
        assert len(snapshot["risks"]) == 5

    def test_load_missing_snapshot_returns_none(self, store):
        result = store.load_snapshot(date(2020, 1, 1))
        assert result is None

    def test_get_latest_snapshot(self, store, sample_risks):
        store.save_snapshot(sample_risks)
        latest = store.get_latest_snapshot()
        assert latest is not None
        assert latest["date"] == date.today().isoformat()

    def test_get_all_risk_ids_on_date(self, store, sample_risks):
        store.save_snapshot(sample_risks)
        ids = store.get_all_risk_ids_on_date(date.today())
        assert ids == {"r1", "r2", "r3", "r4", "r5"}

    def test_list_available_dates(self, store, sample_risks):
        store.save_snapshot(sample_risks)
        dates = store.list_available_dates()
        assert date.today() in dates

    def test_get_history_no_data(self, store):
        history = store.get_history(weeks=4)
        assert history == []

    def test_snapshot_contains_stale_count(self, store, sample_risks):
        store.save_snapshot(sample_risks)
        snapshot = store.load_snapshot(date.today())
        # r5 has last_reviewed 2 days ago with Low impact (threshold 30) — NOT stale
        # r1-r4 are stale
        assert snapshot["total_stale"] == 4


# --- MetricsCalculator Tests ---


class TestMetricsCalculator:

    def test_compute_all_basic(self, store, sample_risks):
        calc = MetricsCalculator(store)
        metrics = calc.compute_all(sample_risks)

        assert metrics.total_open == 5
        assert metrics.total_stale == 4  # r5 is not stale
        assert metrics.compliance_rate == pytest.approx(0.2)  # 1/5
        assert metrics.newly_closed == 0  # No previous snapshot
        assert metrics.newly_added == 0

    def test_compute_with_previous_snapshot(self, store, sample_risks, temp_snapshot_dir):
        # Create a "last week" snapshot with different risks
        last_week = date.today() - timedelta(days=7)
        old_snapshot = {
            "date": last_week.isoformat(),
            "total_open": 6,
            "total_stale": 5,
            "risks": [
                {"id": "r1", "name": "Auth timeout"},
                {"id": "r2", "name": "DB pool"},
                {"id": "r3", "name": "Email slow"},
                {"id": "r4", "name": "Push fail"},
                {"id": "r5", "name": "New risk"},
                {"id": "r_closed", "name": "This was closed"},
            ],
        }
        filepath = temp_snapshot_dir / f"snapshot_{last_week.isoformat()}.json"
        with open(filepath, "w") as f:
            json.dump(old_snapshot, f)

        calc = MetricsCalculator(store)
        metrics = calc.compute_all(sample_risks)

        # r_closed was in last week but not current = 1 closed
        assert metrics.newly_closed == 1
        # All current risks were in last week = 0 new
        assert metrics.newly_added == 0
        # Delta: 5 now vs 6 last week
        assert metrics.open_delta == -1

    def test_burndown_with_history(self, store, sample_risks, temp_snapshot_dir):
        # Create multiple historical snapshots
        for weeks_ago in [4, 3, 2, 1]:
            snap_date = date.today() - timedelta(weeks=weeks_ago)
            snapshot = {
                "date": snap_date.isoformat(),
                "total_open": 92 + weeks_ago,  # Decreasing
                "total_stale": 90 + weeks_ago,
                "risks": [],
            }
            filepath = temp_snapshot_dir / f"snapshot_{snap_date.isoformat()}.json"
            with open(filepath, "w") as f:
                json.dump(snapshot, f)

        calc = MetricsCalculator(store)
        metrics = calc.compute_all(sample_risks)

        # Should have historical points + today
        assert len(metrics.burndown) >= 2

    def test_team_metrics(self, store, sample_risks):
        calc = MetricsCalculator(store)
        metrics = calc.compute_all(sample_risks)

        assert len(metrics.team_metrics) > 0
        # MC API Frameworks has 2 risks
        api_team = next(t for t in metrics.team_metrics if t.team_name == "MC API Frameworks")
        assert api_team.open_count == 2
        assert api_team.high_impact_count == 2

    def test_oldest_stale(self, store, sample_risks):
        calc = MetricsCalculator(store)
        metrics = calc.compute_all(sample_risks)

        assert len(metrics.oldest_stale) > 0
        # r4 is oldest (50 days)
        assert metrics.oldest_stale[0]["name"] == "Push fail"
        assert metrics.oldest_stale[0]["days"] == 50

    def test_shout_outs_zero_stale_team(self, store):
        # Create a team with all-current risks
        today = date.today()
        risks = [
            make_risk(id="r1", name="Fresh risk", impact="High",
                      team_name="Star Team", last_reviewed_date=today - timedelta(days=1)),
        ]
        calc = MetricsCalculator(store)
        metrics = calc.compute_all(risks)

        assert any("Star Team" in s for s in metrics.shout_outs)


# --- ReportGenerator Tests ---


class TestReportGenerator:

    @pytest.fixture
    def metrics(self, store, sample_risks):
        calc = MetricsCalculator(store)
        return calc.compute_all(sample_risks)

    def test_builds_valid_slack_message(self, metrics):
        gen = ReportGenerator()
        msg = gen.build_weekly_report(metrics)

        assert "text" in msg
        assert "blocks" in msg
        assert "Weekly Summary" in msg["text"]

    def test_includes_key_metrics(self, metrics):
        gen = ReportGenerator()
        msg = gen.build_weekly_report(metrics)
        blocks_text = str(msg["blocks"])

        assert "Total Open Risks" in blocks_text
        assert "Stale" in blocks_text
        assert "Compliance" in blocks_text

    def test_includes_team_breakdown(self, metrics):
        gen = ReportGenerator()
        msg = gen.build_weekly_report(metrics)
        blocks_text = str(msg["blocks"])

        assert "MC API Frameworks" in blocks_text

    def test_includes_oldest_risks(self, metrics):
        gen = ReportGenerator()
        msg = gen.build_weekly_report(metrics)
        blocks_text = str(msg["blocks"])

        assert "Push fail" in blocks_text

    def test_includes_dashboard_link(self, metrics):
        gen = ReportGenerator()
        msg = gen.build_weekly_report(metrics)
        blocks_text = str(msg["blocks"])

        assert "01ZEE000001Bgkv2AC" in blocks_text

    def test_includes_shout_outs_when_present(self):
        """Report should show shout-outs section when teams are doing well."""
        gen = ReportGenerator()
        metrics = WeeklyMetrics(
            total_open=10, total_stale=5, compliance_rate=0.5,
            newly_closed=3, newly_added=1,
            shout_outs=["*Star Team* has zero stale risks — all 5 reviewed within SLA!"],
        )
        msg = gen.build_weekly_report(metrics)
        blocks_text = str(msg["blocks"])

        assert "Shout-Outs" in blocks_text
        assert "Star Team" in blocks_text

    def test_no_burndown_with_insufficient_data(self):
        """Burndown section should be skipped if less than 2 data points."""
        gen = ReportGenerator()
        metrics = WeeklyMetrics(
            total_open=10, total_stale=5, compliance_rate=0.5,
            newly_closed=0, newly_added=0,
            burndown=[],  # No history
        )
        msg = gen.build_weekly_report(metrics)
        blocks_text = str(msg["blocks"])

        assert "Burndown" not in blocks_text
