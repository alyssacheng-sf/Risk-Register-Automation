"""Notification delivery orchestration system.

Manages the end-to-end delivery pipeline:
- Queues notifications for batch processing
- Groups risks by owner into single messages
- Respects quiet hours (no notifications outside work hours)
- Integrates with deduplication (notification_history)
- Handles fallback when Slack delivery fails

Usage:
    delivery = NotificationDelivery(
        slack_client=SlackClient(...),
        builder=NotificationBuilder(),
        history=NotificationHistory(),
    )
    delivery.deliver_owner_alerts(analysis_result)
    delivery.deliver_escalations(analysis_result, escalation_candidates)
    delivery.deliver_weekly_summary(analysis_result)
"""

import logging
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

from .models.risk import Risk
from .notification_builder import NotificationBuilder
from .notification_history import NotificationHistory
from .owner_mapper import OwnerMapper
from .risk_analyzer import AnalysisResult
from .slack_client import SlackClient

logger = logging.getLogger(__name__)


# Default quiet hours: no notifications before 8 AM or after 6 PM local time
DEFAULT_QUIET_START = time(18, 0)  # 6 PM
DEFAULT_QUIET_END = time(8, 0)  # 8 AM


class NotificationDelivery:
    """Orchestrates notification delivery across all recipient types.

    Responsibilities:
    - Decides WHAT to send (based on staleness + escalation rules)
    - Decides WHO to send to (via OwnerMapper)
    - Decides WHEN to send (respecting quiet hours)
    - Tracks WHAT WAS SENT (via NotificationHistory for dedup)

    Usage:
        delivery = NotificationDelivery(slack, builder, history)
        report = delivery.run_daily_notifications(analysis_result)
    """

    def __init__(
        self,
        slack_client: SlackClient,
        builder: NotificationBuilder,
        history: NotificationHistory,
        owner_mapper: Optional[OwnerMapper] = None,
        quiet_start: time = DEFAULT_QUIET_START,
        quiet_end: time = DEFAULT_QUIET_END,
        summary_channel: Optional[str] = None,
    ):
        """Initialize the delivery system.

        Args:
            slack_client: Configured Slack client for sending messages.
            builder: Message builder for formatting notifications.
            history: Notification history for deduplication.
            owner_mapper: Maps emails to Slack IDs (optional for DM routing).
            quiet_start: Start of quiet hours (no notifications sent after this).
            quiet_end: End of quiet hours (notifications resume after this).
            summary_channel: Slack channel ID for weekly summaries.
        """
        self.slack = slack_client
        self.builder = builder
        self.history = history
        self.owner_mapper = owner_mapper or OwnerMapper()
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
        self.summary_channel = summary_channel
        self._delivery_queue: List[Dict] = []
        self._delivery_report: DeliveryReport = DeliveryReport()

    def run_daily_notifications(self, result: AnalysisResult) -> "DeliveryReport":
        """Execute the full daily notification pipeline.

        Steps:
        1. Check quiet hours
        2. Build owner alerts (grouped by owner, deduplicated)
        3. Build escalation alerts for overdue risks
        4. Deliver all queued messages
        5. Record delivery history

        Args:
            result: Analysis result from RiskAnalyzer.

        Returns:
            DeliveryReport with statistics on what was sent/skipped.
        """
        self._delivery_report = DeliveryReport()

        # Check quiet hours
        if self.is_quiet_hours():
            logger.info("Currently in quiet hours — skipping notifications")
            self._delivery_report.skipped_reason = "quiet_hours"
            return self._delivery_report

        # Step 1: Owner alerts
        self._deliver_owner_alerts(result)

        # Step 2: Escalation alerts
        from .risk_analyzer import RiskAnalyzer
        analyzer = RiskAnalyzer()
        escalation_candidates = analyzer.get_escalation_candidates(result.stale_risks)
        if escalation_candidates:
            self._deliver_escalations(escalation_candidates, result)

        # Step 3: Save history
        self.history.save()

        logger.info(f"Daily notifications complete: {self._delivery_report.summary()}")
        return self._delivery_report

    def run_weekly_summary(self, result: AnalysisResult, newly_closed: int = 0, newly_added: int = 0) -> "DeliveryReport":
        """Execute the weekly summary delivery.

        Args:
            result: Analysis result from RiskAnalyzer.
            newly_closed: Count of risks closed this week.
            newly_added: Count of new risks this week.

        Returns:
            DeliveryReport with delivery status.
        """
        self._delivery_report = DeliveryReport()

        msg = self.builder.build_weekly_summary(
            total_open=result.total_open,
            total_stale=result.total_stale,
            compliance_rate=result.compliance_rate,
            by_team=result.by_team,
            top_stale=result.prioritized[:5],
            newly_closed=newly_closed,
            newly_added=newly_added,
        )

        # Send to summary channel if configured
        if self.summary_channel:
            success = self.slack.send_to_channel(self.summary_channel, msg)
            self._delivery_report.record(
                recipient=self.summary_channel,
                notification_type="weekly_summary",
                success=success,
            )
        else:
            # Fallback: send as DM to target user
            if self.slack.target_user:
                success = self.slack.send_dm(self.slack.target_user, msg)
                self._delivery_report.record(
                    recipient=self.slack.target_user,
                    notification_type="weekly_summary",
                    success=success,
                )

        self.history.save()
        return self._delivery_report

    def _deliver_owner_alerts(self, result: AnalysisResult) -> None:
        """Build and deliver grouped owner alerts with deduplication.

        Groups all stale risks by owner, then sends a single consolidated
        message per owner. Skips risks that were already notified today.
        """
        for owner_email, risks in result.by_owner.items():
            # Filter out risks already notified today
            new_risks = [
                r for r in risks
                if not self.history.was_notified_today(risk_id=r.id, owner_email=owner_email)
            ]

            if not new_risks:
                logger.debug(f"Skipping {owner_email}: all risks already notified today")
                self._delivery_report.record(
                    recipient=owner_email,
                    notification_type="owner_alert",
                    success=True,
                    skipped=True,
                    skip_reason="already_notified",
                )
                continue

            # Build the consolidated message
            msg = self.builder.build_owner_alert(
                new_risks,
                owner_email,
                categories=result.categorized,
            )

            # Deliver
            success = self.slack.send_dm(owner_email, msg)

            # Record in history
            if success:
                for risk in new_risks:
                    self.history.record_notification(
                        risk_id=risk.id,
                        owner_email=owner_email,
                        notification_type="owner_alert",
                    )

            self._delivery_report.record(
                recipient=owner_email,
                notification_type="owner_alert",
                success=success,
                risk_count=len(new_risks),
            )

    def _deliver_escalations(
        self, candidates: List[Risk], result: AnalysisResult, min_notify_days: int = 3
    ) -> None:
        """Deliver escalation alerts to managers.

        Groups escalation candidates by owner, then sends escalation to
        that owner's manager (or fallback channel).

        Only escalates if the owner has already been notified on at least
        `min_notify_days` separate days — prevents escalating someone who
        hasn't even been warned yet.

        Args:
            candidates: Risks that qualify for escalation by staleness.
            result: Full analysis result.
            min_notify_days: Minimum prior notification days before escalation fires.
        """
        # Group escalation candidates by owner
        from collections import defaultdict
        by_owner: Dict[str, List[Risk]] = defaultdict(list)
        for risk in candidates:
            email = risk.owner_email or "unknown"
            by_owner[email].append(risk)

        for owner_email, risks in by_owner.items():
            # Gate: don't escalate until owner has been warned N times
            prior_days = self.history.get_notification_days_for_owner(owner_email)
            if prior_days < min_notify_days:
                logger.info(
                    f"Skipping escalation for {owner_email}: only notified "
                    f"{prior_days}/{min_notify_days} days so far"
                )
                self._delivery_report.record(
                    recipient=f"manager_of:{owner_email}",
                    notification_type="escalation",
                    success=True,
                    skipped=True,
                    skip_reason=f"insufficient_prior_notifications ({prior_days}/{min_notify_days} days)",
                )
                continue

            # Check dedup: only escalate once per day per owner
            if self.history.was_escalated_today(owner_email):
                logger.debug(f"Skipping escalation for {owner_email}: already escalated today")
                self._delivery_report.record(
                    recipient=f"manager_of:{owner_email}",
                    notification_type="escalation",
                    success=True,
                    skipped=True,
                    skip_reason="already_escalated",
                )
                continue

            msg = self.builder.build_escalation_alert(
                risks,
                owner_email=owner_email,
                manager_name=None,  # Could resolve via directory service
            )

            # For now, send escalation to the configured target user
            # In production, would resolve to owner's manager via LDAP/directory
            success = self.slack.send_dm(owner_email, msg)

            if success:
                self.history.record_escalation(owner_email)

            self._delivery_report.record(
                recipient=f"manager_of:{owner_email}",
                notification_type="escalation",
                success=success,
                risk_count=len(risks),
            )

    def is_quiet_hours(self, now: Optional[datetime] = None) -> bool:
        """Check if current time is within quiet hours.

        Quiet hours span overnight: e.g., 6 PM to 8 AM means
        no notifications between 18:00 and 08:00.

        Args:
            now: Optional datetime for testing. Defaults to current time.

        Returns:
            True if notifications should NOT be sent right now.
        """
        current_time = (now or datetime.now()).time()

        # Quiet hours span midnight: start > end means overnight
        if self.quiet_start > self.quiet_end:
            # Quiet from 6 PM to 8 AM = quiet if time >= 6 PM OR time < 8 AM
            return current_time >= self.quiet_start or current_time < self.quiet_end
        else:
            # Quiet from 8 AM to 6 PM (unusual but supported)
            return self.quiet_start <= current_time < self.quiet_end

    def get_queue_size(self) -> int:
        """Return number of pending messages in queue."""
        return len(self._delivery_queue)


class DeliveryReport:
    """Tracks delivery outcomes for a single notification run."""

    def __init__(self):
        self.entries: List[Dict] = []
        self.skipped_reason: Optional[str] = None

    def record(
        self,
        recipient: str,
        notification_type: str,
        success: bool,
        skipped: bool = False,
        skip_reason: Optional[str] = None,
        risk_count: int = 0,
    ):
        """Record a delivery attempt."""
        self.entries.append({
            "recipient": recipient,
            "type": notification_type,
            "success": success,
            "skipped": skipped,
            "skip_reason": skip_reason,
            "risk_count": risk_count,
            "timestamp": datetime.now().isoformat(),
        })

    @property
    def total_sent(self) -> int:
        return sum(1 for e in self.entries if e["success"] and not e["skipped"])

    @property
    def total_skipped(self) -> int:
        return sum(1 for e in self.entries if e["skipped"])

    @property
    def total_failed(self) -> int:
        return sum(1 for e in self.entries if not e["success"])

    @property
    def total_risks_notified(self) -> int:
        return sum(e["risk_count"] for e in self.entries if e["success"] and not e["skipped"])

    def summary(self) -> str:
        """Human-readable summary of the delivery run."""
        if self.skipped_reason:
            return f"Run skipped: {self.skipped_reason}"
        return (
            f"Sent: {self.total_sent}, "
            f"Skipped (dedup): {self.total_skipped}, "
            f"Failed: {self.total_failed}, "
            f"Risks covered: {self.total_risks_notified}"
        )
