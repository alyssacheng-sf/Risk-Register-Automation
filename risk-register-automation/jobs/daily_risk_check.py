#!/usr/bin/env python3
"""Daily Risk Check Job — Phase 4.

Automated daily job that:
1. Fetches all open risks from GUS
2. Identifies stale risks
3. Groups by owner
4. Sends Slack notifications (with deduplication)
5. Logs results with structured JSON

Designed to run via cron at 8 AM PST (15:00 UTC) Monday–Friday.

Usage:
    # Normal execution (dry-run by default)
    python3 jobs/daily_risk_check.py

    # Live mode
    SLACK_BOT_TOKEN=xoxb-... DRY_RUN=false python3 jobs/daily_risk_check.py

    # With ops channel notifications
    OPS_CHANNEL=C0123456789 python3 jobs/daily_risk_check.py

Exit codes:
    0 - Success (notifications sent or skipped due to dedup/quiet hours)
    1 - Partial failure (some notifications failed, others succeeded)
    2 - Fatal failure (cannot connect to GUS or critical error)
"""

import os
import sys
import time
from datetime import time as dt_time

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gus_client import GUSClient, GUSClientError
from src.risk_analyzer import RiskAnalyzer
from src.notification_builder import NotificationBuilder
from src.notification_delivery import NotificationDelivery
from src.notification_history import NotificationHistory
from src.slack_client import SlackClient
from jobs.job_logging import setup_logging, log_job_metrics

# === CONFIGURATION ===
TARGET_USER = os.environ.get("TARGET_USER", "alyssa.cheng@salesforce.com")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", None)
OPS_CHANNEL = os.environ.get("OPS_CHANNEL", None)
SUMMARY_CHANNEL = os.environ.get("SUMMARY_CHANNEL", None)


def main() -> int:
    """Execute the daily risk check pipeline.

    Returns:
        Exit code: 0=success, 1=partial failure, 2=fatal.
    """
    logger = setup_logging("daily_risk_check")
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("Daily Risk Check - Starting")
    logger.info(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    logger.info(f"Target user: {TARGET_USER}")
    logger.info("=" * 60)

    metrics = {
        "job": "daily_risk_check",
        "mode": "dry_run" if DRY_RUN else "live",
        "target_user": TARGET_USER,
    }

    try:
        # --- Step 1: Fetch data from GUS ---
        logger.info("Step 1: Fetching open risks from GUS...")
        gus = GUSClient()

        if not gus.health_check():
            logger.error("GUS health check failed — cannot proceed")
            metrics["error"] = "gus_health_check_failed"
            log_job_metrics(logger, metrics, start_time)
            return 2

        open_risks = gus.get_open_risks()
        metrics["total_open_risks"] = len(open_risks)
        logger.info(f"  Fetched {len(open_risks)} open risks")

        if not open_risks:
            logger.warning("No open risks found — exiting early")
            metrics["result"] = "no_risks"
            log_job_metrics(logger, metrics, start_time)
            return 0

        # --- Step 2: Analyze ---
        logger.info("Step 2: Analyzing risks...")
        analyzer = RiskAnalyzer()
        result = analyzer.analyze(open_risks)

        metrics["total_stale"] = result.total_stale
        metrics["compliance_rate"] = result.compliance_rate_pct
        metrics["owners_to_notify"] = len(result.by_owner)
        metrics["teams_affected"] = len(result.by_team)

        logger.info(f"  Stale: {result.total_stale}/{result.total_open}")
        logger.info(f"  Compliance: {result.compliance_rate_pct}")
        logger.info(f"  Owners to notify: {len(result.by_owner)}")

        if result.total_stale == 0:
            logger.info("No stale risks — nothing to notify")
            metrics["result"] = "all_current"
            log_job_metrics(logger, metrics, start_time)
            return 0

        # --- Step 3: Deliver notifications ---
        logger.info("Step 3: Delivering notifications...")

        slack = SlackClient(
            bot_token=BOT_TOKEN,
            dry_run=DRY_RUN,
            target_user=TARGET_USER,
        )

        history = NotificationHistory()
        builder = NotificationBuilder()

        delivery = NotificationDelivery(
            slack_client=slack,
            builder=builder,
            history=history,
            summary_channel=SUMMARY_CHANNEL,
            quiet_start=dt_time(0, 0),  # TEMP: disable quiet hours for demo
            quiet_end=dt_time(0, 0),    # TEMP: disable quiet hours for demo
        )

        report = delivery.run_daily_notifications(result)

        metrics["notifications_sent"] = report.total_sent
        metrics["notifications_skipped"] = report.total_skipped
        metrics["notifications_failed"] = report.total_failed
        metrics["risks_notified"] = report.total_risks_notified

        logger.info(f"  Delivery report: {report.summary()}")

        # --- Step 4: Send ops summary (if configured) ---
        if OPS_CHANNEL:
            ops_msg = _build_ops_summary(result, report, start_time)
            slack.send_to_channel(OPS_CHANNEL, ops_msg)

        # --- Determine exit code ---
        if report.total_failed > 0 and report.total_sent == 0:
            metrics["result"] = "all_failed"
            log_job_metrics(logger, metrics, start_time)
            return 2
        elif report.total_failed > 0:
            metrics["result"] = "partial_failure"
            log_job_metrics(logger, metrics, start_time)
            return 1
        else:
            metrics["result"] = "success"
            log_job_metrics(logger, metrics, start_time)
            return 0

    except GUSClientError as e:
        logger.error(f"GUS error: {e}")
        metrics["error"] = f"gus_error: {str(e)[:100]}"
        log_job_metrics(logger, metrics, start_time)
        return 2

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        metrics["error"] = f"unexpected: {str(e)[:100]}"
        log_job_metrics(logger, metrics, start_time)
        return 2


def _build_ops_summary(result, report, start_time: float) -> dict:
    """Build a short ops channel message summarizing the job run."""
    elapsed = time.time() - start_time
    status = "✅" if report.total_failed == 0 else "⚠️"

    text = f"{status} Daily Risk Check complete ({elapsed:.1f}s)"
    return {
        "text": text,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{status} *Daily Risk Check Complete*\n"
                        f"• Risks scanned: {result.total_open}\n"
                        f"• Stale: {result.total_stale} ({result.compliance_rate_pct} compliant)\n"
                        f"• Notifications sent: {report.total_sent}\n"
                        f"• Skipped (dedup): {report.total_skipped}\n"
                        f"• Failed: {report.total_failed}\n"
                        f"• Duration: {elapsed:.1f}s\n"
                        f"• Mode: `{'dry_run' if DRY_RUN else 'live'}`"
                    ),
                },
            },
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
