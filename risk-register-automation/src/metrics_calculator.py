"""Metrics calculator for risk register reporting.

Computes all metrics needed for the weekly summary report:
- Risk burndown (open risks over last N weeks)
- Risk velocity (closed per week)
- Average time to closure
- Review compliance rate trend
- Team performance (who's closing risks)
- Newly added risks

Usage:
    store = DataStore()
    calculator = MetricsCalculator(store)
    metrics = calculator.compute_all(current_risks)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .data_store import DataStore
from .models.risk import Risk

logger = logging.getLogger(__name__)


@dataclass
class BurndownPoint:
    """A single point on the burndown chart."""
    date: date
    total_open: int
    total_stale: int
    delta: Optional[int] = None  # Change from previous point


@dataclass
class TeamMetrics:
    """Metrics for a single team."""
    team_name: str
    open_count: int
    stale_count: int
    high_impact_count: int
    closed_this_week: int = 0


@dataclass
class WeeklyMetrics:
    """All metrics for a weekly report."""
    # Top-line numbers
    total_open: int
    total_stale: int
    compliance_rate: float
    newly_closed: int
    newly_added: int

    # Deltas from last week
    open_delta: Optional[int] = None  # + or - from last week
    compliance_delta: Optional[float] = None  # + or - percentage points

    # Burndown (last 4 weeks)
    burndown: List[BurndownPoint] = field(default_factory=list)

    # Velocity
    avg_closed_per_week: float = 0.0
    avg_time_to_closure_days: Optional[float] = None

    # By team
    team_metrics: List[TeamMetrics] = field(default_factory=list)

    # Oldest risks
    oldest_stale: List[Dict] = field(default_factory=list)

    # New this week
    new_risks: List[Dict] = field(default_factory=list)

    # Shout-outs (teams that closed risks)
    shout_outs: List[str] = field(default_factory=list)


class MetricsCalculator:
    """Computes weekly metrics from current risks + historical snapshots.

    Usage:
        store = DataStore()
        calc = MetricsCalculator(store)
        metrics = calc.compute_all(current_open_risks)
    """

    def __init__(self, data_store: DataStore):
        self.store = data_store

    def compute_all(self, current_risks: List[Risk]) -> WeeklyMetrics:
        """Compute all metrics for the weekly report.

        Args:
            current_risks: Current list of open risks from GUS.

        Returns:
            WeeklyMetrics with all fields populated.
        """
        today = date.today()
        last_week_date = today - timedelta(weeks=1)

        # Load last week's snapshot for deltas
        last_week_ids = self.store.get_all_risk_ids_on_date(last_week_date)
        current_ids = {r.id for r in current_risks}

        # Basic counts
        total_open = len(current_risks)
        stale_risks = [r for r in current_risks if r.is_stale]
        total_stale = len(stale_risks)
        compliance_rate = (total_open - total_stale) / total_open if total_open > 0 else 1.0

        # Week-over-week changes
        newly_closed = len(last_week_ids - current_ids) if last_week_ids else 0
        newly_added = len(current_ids - last_week_ids) if last_week_ids else 0

        # Deltas
        last_week_snapshot = self.store.load_snapshot(last_week_date) or self._find_nearest_snapshot(last_week_date)
        open_delta = None
        compliance_delta = None
        if last_week_snapshot:
            prev_open = last_week_snapshot.get("total_open", 0)
            prev_stale = last_week_snapshot.get("total_stale", 0)
            open_delta = total_open - prev_open
            prev_compliance = (prev_open - prev_stale) / prev_open if prev_open > 0 else 1.0
            compliance_delta = compliance_rate - prev_compliance

        # Burndown
        burndown = self._compute_burndown(current_risks, weeks=4)

        # Velocity
        avg_closed = self._compute_avg_velocity(weeks=4)

        # Time to closure (from risks that have closed_on dates)
        avg_closure_days = self._compute_avg_closure_time(current_risks)

        # Team breakdown
        team_metrics = self._compute_team_metrics(current_risks, last_week_ids)

        # Oldest stale
        oldest = sorted(stale_risks, key=lambda r: -(r.days_since_review or 0))[:10]
        oldest_stale = [
            {
                "name": r.name,
                "days": r.days_since_review,
                "owner": r.owner_name or r.owner_email or "Unknown",
                "team": r.team_name,
                "gus_url": r.gus_url,
            }
            for r in oldest
        ]

        # Newly added (risks in current but not in last week)
        new_risk_objects = [r for r in current_risks if r.id not in last_week_ids] if last_week_ids else []
        new_risks = [
            {"name": r.name, "impact": r.impact, "team": r.team_name}
            for r in new_risk_objects[:10]
        ]

        # Shout-outs
        shout_outs = self._compute_shout_outs(team_metrics)

        return WeeklyMetrics(
            total_open=total_open,
            total_stale=total_stale,
            compliance_rate=compliance_rate,
            newly_closed=newly_closed,
            newly_added=newly_added,
            open_delta=open_delta,
            compliance_delta=compliance_delta,
            burndown=burndown,
            avg_closed_per_week=avg_closed,
            avg_time_to_closure_days=avg_closure_days,
            team_metrics=team_metrics,
            oldest_stale=oldest_stale,
            new_risks=new_risks,
            shout_outs=shout_outs,
        )

    def _compute_burndown(self, current_risks: List[Risk], weeks: int = 4) -> List[BurndownPoint]:
        """Build burndown data from historical snapshots.

        Returns one point per week, plus today's point.
        """
        points = []
        today = date.today()

        # Historical points
        history = self.store.get_history(weeks=weeks)
        for snapshot in history:
            snap_date = date.fromisoformat(snapshot["date"])
            total = snapshot.get("total_open", 0)
            stale = snapshot.get("total_stale", 0)
            points.append(BurndownPoint(date=snap_date, total_open=total, total_stale=stale))

        # Today's point
        total_stale = sum(1 for r in current_risks if r.is_stale)
        points.append(BurndownPoint(date=today, total_open=len(current_risks), total_stale=total_stale))

        # Compute deltas
        for i in range(1, len(points)):
            points[i].delta = points[i].total_open - points[i - 1].total_open

        return points

    def _compute_avg_velocity(self, weeks: int = 4) -> float:
        """Compute average risks closed per week over last N weeks."""
        today = date.today()
        total_closed = 0
        weeks_with_data = 0

        for i in range(1, weeks + 1):
            week_start = today - timedelta(weeks=i)
            week_end = today - timedelta(weeks=i - 1)

            start_ids = self.store.get_all_risk_ids_on_date(week_start)
            end_ids = self.store.get_all_risk_ids_on_date(week_end)

            if start_ids and end_ids:
                closed = len(start_ids - end_ids)
                total_closed += closed
                weeks_with_data += 1

        return total_closed / weeks_with_data if weeks_with_data > 0 else 0.0

    def _compute_avg_closure_time(self, risks: List[Risk]) -> Optional[float]:
        """Compute average days from identification to closure.

        Uses identified_on and closed_on fields. Only works for closed risks
        that have both dates.
        """
        closure_times = []
        for risk in risks:
            if risk.closed_on and risk.identified_on:
                days = (risk.closed_on - risk.identified_on).days
                if days >= 0:
                    closure_times.append(days)

        return sum(closure_times) / len(closure_times) if closure_times else None

    def _compute_team_metrics(self, current_risks: List[Risk], last_week_ids: set) -> List[TeamMetrics]:
        """Compute per-team metrics."""
        from collections import defaultdict
        teams: Dict[str, List[Risk]] = defaultdict(list)

        for risk in current_risks:
            team = risk.team_name or "Unknown"
            teams[team].append(risk)

        # Figure out which risks each team closed
        current_ids = {r.id for r in current_risks}
        closed_ids = last_week_ids - current_ids if last_week_ids else set()

        # We need last week's snapshot to know which team owned the closed risks
        # For now, we can only track teams with current risks
        team_closed: Dict[str, int] = defaultdict(int)
        # TODO: Look up closed risk teams from last week's snapshot

        result = []
        for team_name, risks in sorted(teams.items(), key=lambda x: -len(x[1])):
            stale = sum(1 for r in risks if r.is_stale)
            high = sum(1 for r in risks if r.impact == "High")
            result.append(TeamMetrics(
                team_name=team_name,
                open_count=len(risks),
                stale_count=stale,
                high_impact_count=high,
                closed_this_week=team_closed.get(team_name, 0),
            ))

        return result

    def _compute_shout_outs(self, team_metrics: List[TeamMetrics]) -> List[str]:
        """Generate shout-out messages for teams doing well."""
        shout_outs = []

        for tm in team_metrics:
            if tm.closed_this_week >= 3:
                shout_outs.append(
                    f"*{tm.team_name}* closed {tm.closed_this_week} risks this week!"
                )
            elif tm.stale_count == 0 and tm.open_count > 0:
                shout_outs.append(
                    f"*{tm.team_name}* has zero stale risks — all {tm.open_count} reviewed within SLA!"
                )

        return shout_outs

    def _find_nearest_snapshot(self, target: date) -> Optional[Dict]:
        """Find nearest snapshot within 3 days of target."""
        for offset in range(4):
            for d in [1, -1]:
                check = target + timedelta(days=offset * d)
                snapshot = self.store.load_snapshot(check)
                if snapshot:
                    return snapshot
        return None
