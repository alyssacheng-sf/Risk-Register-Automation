"""Notification history tracking for deduplication.

Prevents sending duplicate notifications for the same risk to the same owner
within the same day. Uses a simple JSON file for persistence.

Design decisions:
- File-based storage (no external database dependency for MVP)
- Auto-prunes entries older than 30 days to prevent unbounded growth
- Thread-safe for single-process use (no concurrent writers expected)

Usage:
    history = NotificationHistory()
    if not history.was_notified_today(risk_id="r123", owner_email="user@sf.com"):
        # Send notification...
        history.record_notification("r123", "user@sf.com", "owner_alert")
    history.save()
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default storage location
DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "notification_history.json"

# Auto-prune entries older than this
MAX_HISTORY_DAYS = 30


class NotificationHistory:
    """Tracks which notifications have been sent to prevent duplicates.

    Storage format (JSON):
    {
        "notifications": [
            {
                "risk_id": "a1j...",
                "owner_email": "user@sf.com",
                "type": "owner_alert",
                "date": "2026-06-17",
                "timestamp": "2026-06-17T08:30:00"
            },
            ...
        ],
        "escalations": [
            {
                "owner_email": "user@sf.com",
                "date": "2026-06-17",
                "timestamp": "2026-06-17T08:30:00"
            },
            ...
        ],
        "dismissals": [
            {
                "risk_id": "a1j...",
                "dismissed_by": "user@sf.com",
                "dismiss_until": "2026-06-24",
                "timestamp": "2026-06-17T08:30:00"
            },
            ...
        ]
    }
    """

    def __init__(self, history_file: Optional[Path] = None):
        """Initialize notification history.

        Args:
            history_file: Path to the JSON history file. Defaults to data/notification_history.json.
        """
        self.history_file = history_file or HISTORY_FILE
        self._data = self._load()
        self._dirty = False

    def was_notified_today(self, risk_id: str, owner_email: str) -> bool:
        """Check if a notification was already sent today for this risk+owner.

        Args:
            risk_id: The GUS risk ID.
            owner_email: The owner's email address.

        Returns:
            True if already notified today (should skip).
        """
        today_str = date.today().isoformat()
        for entry in self._data.get("notifications", []):
            if (
                entry["risk_id"] == risk_id
                and entry["owner_email"] == owner_email.lower()
                and entry["date"] == today_str
            ):
                return True
        return False

    def was_escalated_today(self, owner_email: str) -> bool:
        """Check if an escalation was already sent today for this owner.

        Args:
            owner_email: The risk owner's email.

        Returns:
            True if already escalated today.
        """
        today_str = date.today().isoformat()
        for entry in self._data.get("escalations", []):
            if (
                entry["owner_email"] == owner_email.lower()
                and entry["date"] == today_str
            ):
                return True
        return False

    def is_dismissed(self, risk_id: str) -> bool:
        """Check if a risk has been dismissed (snoozed) and is still within the snooze window.

        Args:
            risk_id: The GUS risk ID.

        Returns:
            True if the risk is currently dismissed (should not notify).
        """
        today_str = date.today().isoformat()
        for entry in self._data.get("dismissals", []):
            if entry["risk_id"] == risk_id and entry["dismiss_until"] >= today_str:
                return True
        return False

    def record_notification(
        self, risk_id: str, owner_email: str, notification_type: str
    ) -> None:
        """Record that a notification was sent.

        Args:
            risk_id: The GUS risk ID.
            owner_email: The recipient's email.
            notification_type: Type of notification (owner_alert, escalation, etc).
        """
        entry = {
            "risk_id": risk_id,
            "owner_email": owner_email.lower(),
            "type": notification_type,
            "date": date.today().isoformat(),
            "timestamp": datetime.now().isoformat(),
        }
        self._data.setdefault("notifications", []).append(entry)
        self._dirty = True
        logger.debug(f"Recorded notification: {risk_id} -> {owner_email}")

    def record_escalation(self, owner_email: str) -> None:
        """Record that an escalation was sent for this owner.

        Args:
            owner_email: The risk owner whose risks were escalated.
        """
        entry = {
            "owner_email": owner_email.lower(),
            "date": date.today().isoformat(),
            "timestamp": datetime.now().isoformat(),
        }
        self._data.setdefault("escalations", []).append(entry)
        self._dirty = True
        logger.debug(f"Recorded escalation for: {owner_email}")

    def record_dismissal(self, risk_id: str, dismissed_by: str, days: int = 7) -> None:
        """Record that a user dismissed (snoozed) a risk notification.

        Args:
            risk_id: The GUS risk ID being dismissed.
            dismissed_by: Email of the person who dismissed it.
            days: Number of days to suppress notifications (default 7).
        """
        dismiss_until = (date.today() + timedelta(days=days)).isoformat()
        entry = {
            "risk_id": risk_id,
            "dismissed_by": dismissed_by.lower(),
            "dismiss_until": dismiss_until,
            "timestamp": datetime.now().isoformat(),
        }
        self._data.setdefault("dismissals", []).append(entry)
        self._dirty = True
        logger.info(f"Risk {risk_id} dismissed by {dismissed_by} until {dismiss_until}")

    def get_notification_count_today(self, owner_email: Optional[str] = None) -> int:
        """Get the number of notifications sent today.

        Args:
            owner_email: If provided, count only for this owner.

        Returns:
            Count of notifications sent today.
        """
        today_str = date.today().isoformat()
        count = 0
        for entry in self._data.get("notifications", []):
            if entry["date"] != today_str:
                continue
            if owner_email and entry["owner_email"] != owner_email.lower():
                continue
            count += 1
        return count

    def get_history_for_risk(self, risk_id: str) -> List[Dict]:
        """Get all notification history for a specific risk.

        Args:
            risk_id: The GUS risk ID.

        Returns:
            List of notification entries for this risk.
        """
        return [
            entry for entry in self._data.get("notifications", [])
            if entry["risk_id"] == risk_id
        ]

    def get_notification_days_for_owner(self, owner_email: str) -> int:
        """Count distinct days this owner has been notified.

        Used to gate escalation — only escalate after the owner has been
        warned on at least N separate days.

        Args:
            owner_email: The owner's email address.

        Returns:
            Number of distinct days notifications were sent.
        """
        days = set()
        for entry in self._data.get("notifications", []):
            if entry["owner_email"] == owner_email.lower():
                days.add(entry["date"])
        return len(days)

    def prune(self, max_age_days: int = MAX_HISTORY_DAYS) -> int:
        """Remove entries older than max_age_days.

        Args:
            max_age_days: Maximum age of entries to keep.

        Returns:
            Number of entries pruned.
        """
        cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
        pruned = 0

        for key in ("notifications", "escalations"):
            original = self._data.get(key, [])
            filtered = [e for e in original if e.get("date", "") >= cutoff]
            pruned += len(original) - len(filtered)
            self._data[key] = filtered

        # Prune expired dismissals
        today_str = date.today().isoformat()
        original_dismissals = self._data.get("dismissals", [])
        active_dismissals = [d for d in original_dismissals if d["dismiss_until"] >= today_str]
        pruned += len(original_dismissals) - len(active_dismissals)
        self._data["dismissals"] = active_dismissals

        if pruned > 0:
            self._dirty = True
            logger.info(f"Pruned {pruned} old history entries")

        return pruned

    def save(self) -> None:
        """Persist history to disk (only if changes were made)."""
        if not self._dirty:
            return

        # Auto-prune before saving
        self.prune()

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w") as f:
                json.dump(self._data, f, indent=2)
            self._dirty = False
            logger.debug(f"History saved to {self.history_file}")
        except IOError as e:
            logger.error(f"Failed to save notification history: {e}")

    def clear(self) -> None:
        """Clear all history (useful for testing)."""
        self._data = {"notifications": [], "escalations": [], "dismissals": []}
        self._dirty = True

    def stats(self) -> Dict:
        """Return summary statistics about the history."""
        return {
            "total_notifications": len(self._data.get("notifications", [])),
            "total_escalations": len(self._data.get("escalations", [])),
            "active_dismissals": len(self._data.get("dismissals", [])),
            "notifications_today": self.get_notification_count_today(),
        }

    # --- Private Methods ---

    def _load(self) -> Dict:
        """Load history from disk."""
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    data = json.load(f)
                logger.debug(f"Loaded history: {len(data.get('notifications', []))} entries")
                return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load history, starting fresh: {e}")
        return {"notifications": [], "escalations": [], "dismissals": []}
