"""Historical data storage for risk trend analysis.

Manages daily snapshots of risk state to enable:
- Week-over-week burndown tracking
- Risk velocity (closed per week)
- Compliance trends
- Team performance over time

Storage: JSON files in data/snapshots/ (one per day).
Each snapshot is minimal: date, risk_id, status, impact, team, days_stale.

Usage:
    store = DataStore()
    store.save_snapshot(risks)
    history = store.get_history(weeks=4)
    burndown = store.get_burndown(weeks=4)
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models.risk import Risk

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent.parent / "data" / "snapshots"


class DataStore:
    """Manages historical risk snapshots for trend analysis.

    Stores one snapshot per day as a JSON file. Each snapshot contains
    minimal data needed for metrics: id, status, impact, team, staleness.

    Usage:
        store = DataStore()
        store.save_snapshot(open_risks)  # Call daily
        burndown = store.get_burndown(weeks=4)
    """

    def __init__(self, snapshot_dir: Optional[Path] = None):
        """Initialize the data store.

        Args:
            snapshot_dir: Directory for snapshot files. Default: data/snapshots/
        """
        self.snapshot_dir = snapshot_dir or SNAPSHOT_DIR
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, risks: List[Risk]) -> Path:
        """Save today's risk state as a snapshot.

        Args:
            risks: List of current open risks.

        Returns:
            Path to the saved snapshot file.
        """
        today = date.today()
        snapshot = {
            "date": today.isoformat(),
            "total_open": len(risks),
            "total_stale": sum(1 for r in risks if r.is_stale),
            "risks": [
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "impact": r.impact,
                    "team_name": r.team_name,
                    "owner_email": r.owner_email,
                    "owner_name": r.owner_name,
                    "days_since_review": r.days_since_review,
                    "is_stale": r.is_stale,
                    "risk_score": r.risk_score,
                }
                for r in risks
            ],
        }

        filepath = self.snapshot_dir / f"snapshot_{today.isoformat()}.json"
        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2)

        logger.info(f"Snapshot saved: {filepath.name} ({len(risks)} risks)")
        return filepath

    def load_snapshot(self, target_date: date) -> Optional[Dict]:
        """Load a specific day's snapshot.

        Args:
            target_date: The date to load.

        Returns:
            Snapshot dict or None if not found.
        """
        filepath = self.snapshot_dir / f"snapshot_{target_date.isoformat()}.json"
        if not filepath.exists():
            return None

        try:
            with open(filepath) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load snapshot {filepath}: {e}")
            return None

    def get_latest_snapshot(self) -> Optional[Dict]:
        """Load the most recent snapshot available."""
        snapshots = sorted(self.snapshot_dir.glob("snapshot_*.json"), reverse=True)
        if not snapshots:
            return None

        try:
            with open(snapshots[0]) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def get_history(self, weeks: int = 4) -> List[Dict]:
        """Load snapshots for the last N weeks (one per week, Mondays preferred).

        Args:
            weeks: Number of weeks of history to retrieve.

        Returns:
            List of snapshot dicts, oldest first.
        """
        history = []
        today = date.today()

        for i in range(weeks, 0, -1):
            target = today - timedelta(weeks=i)
            # Try the exact date, then search nearby
            snapshot = self._find_nearest_snapshot(target, tolerance_days=3)
            if snapshot:
                history.append(snapshot)

        return history

    def get_all_risk_ids_on_date(self, target_date: date) -> set:
        """Get the set of risk IDs that were open on a given date.

        Args:
            target_date: Date to check.

        Returns:
            Set of risk ID strings.
        """
        snapshot = self.load_snapshot(target_date)
        if not snapshot:
            return set()
        return {r["id"] for r in snapshot.get("risks", [])}

    def list_available_dates(self) -> List[date]:
        """List all dates that have snapshots available.

        Returns:
            Sorted list of dates (oldest first).
        """
        dates = []
        for filepath in self.snapshot_dir.glob("snapshot_*.json"):
            try:
                date_str = filepath.stem.replace("snapshot_", "")
                dates.append(date.fromisoformat(date_str))
            except ValueError:
                continue
        return sorted(dates)

    def _find_nearest_snapshot(self, target: date, tolerance_days: int = 3) -> Optional[Dict]:
        """Find the nearest snapshot to a target date within tolerance.

        Checks target date first, then +/- 1 day, +/- 2 days, etc.
        """
        for offset in range(tolerance_days + 1):
            for direction in [0, 1, -1] if offset == 0 else [1, -1]:
                check_date = target + timedelta(days=offset * direction)
                snapshot = self.load_snapshot(check_date)
                if snapshot:
                    return snapshot
        return None
