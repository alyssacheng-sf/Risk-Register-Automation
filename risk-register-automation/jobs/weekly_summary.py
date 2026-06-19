#!/usr/bin/env python3
"""Weekly Summary Job — Phase 4.

Automated weekly job that:
1. Fetches current risk state from GUS
2. Calculates metrics (compliance, burndown, velocity)
3. Generates a formatted summary report
4. Sends to leadership Slack channel

Designed to run via cron on Monday at 9 AM PST (16:00 UTC).

Usage:
    # Normal execution (dry-run by default)
    python3 jobs/weekly_summary.py

    # Live mode with channel
    SLACK_BOT_TOKEN=xoxb-... DRY_RUN=false SUMMARY_CHANNEL=C0123456789 python3 jobs/weekly_summary.py

Exit codes:
    0 - Success
    1 - Partial failure
    2 - Fatal failure
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gus_client import GUSClient, GUSClientError
from src.data_store import DataStore
from src.metrics_calculator import MetricsCalculator
from src.report_generator import ReportGenerator
from src.slack_client import SlackClient
from jobs.job_logging import setup_logging, log_job_metrics

# === CONFIGURATION ===
TARGET_USER = os.environ.get("TARGET_USER", "alyssa.cheng@salesforce.com")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", None)
SUMMARY_CHANNEL = os.environ.get("SUMMARY_CHANNEL", None)
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "data/snapshots"))


def main() -> int:
    """Execute the weekly summary pipeline.

    Returns:
        Exit code: 0=success, 2=fatal.
    """
    logger = setup_logging("weekly_summary")
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("Weekly Summary Report - Starting")
    logger.info(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    logger.info("=" * 60)

    metrics = {
        "job": "weekly_summary",
        "mode": "dry_run" if DRY_RUN else "live",
    }

    try:
        # --- Step 1: Fetch current data ---
        logger.info("Step 1: Fetching risks from GUS...")
        gus = GUSClient()
        open_risks = gus.get_open_risks()
        metrics["total_open_risks"] = len(open_risks)
        logger.info(f"  Fetched {len(open_risks)} open risks")

        # --- Step 2: Save snapshot (before computing metrics) ---
        logger.info("Step 2: Saving snapshot...")
        store = DataStore(snapshot_dir=SNAPSHOT_DIR)
        store.save_snapshot(open_risks)

        # --- Step 3: Compute metrics (uses historical snapshots) ---
        logger.info("Step 3: Computing metrics...")
        calculator = MetricsCalculator(store)
        weekly_metrics = calculator.compute_all(open_risks)

        metrics["total_stale"] = weekly_metrics.total_stale
        metrics["compliance_rate"] = f"{weekly_metrics.compliance_rate * 100:.0f}%"
        metrics["newly_closed"] = weekly_metrics.newly_closed
        metrics["newly_added"] = weekly_metrics.newly_added
        metrics["avg_velocity"] = f"{weekly_metrics.avg_closed_per_week:.1f}/week"

        logger.info(f"  Stale: {weekly_metrics.total_stale}/{weekly_metrics.total_open}")
        logger.info(f"  Compliance: {metrics['compliance_rate']}")
        logger.info(f"  Closed this week: {weekly_metrics.newly_closed}")
        logger.info(f"  New this week: {weekly_metrics.newly_added}")
        logger.info(f"  Burndown points: {len(weekly_metrics.burndown)}")

        # --- Step 4: Generate report ---
        logger.info("Step 4: Generating report...")
        generator = ReportGenerator()
        report_msg = generator.build_weekly_report(weekly_metrics)

        # --- Step 5: Deliver ---
        logger.info("Step 5: Delivering weekly summary...")
        slack = SlackClient(
            bot_token=BOT_TOKEN,
            dry_run=DRY_RUN,
            target_user=TARGET_USER,
        )

        if SUMMARY_CHANNEL:
            success = slack.send_to_channel(SUMMARY_CHANNEL, report_msg)
        else:
            success = slack.send_dm(TARGET_USER, report_msg)

        logger.info(f"  Delivery: {'success' if success else 'failed'}")
        metrics["result"] = "success" if success else "delivery_failed"
        log_job_metrics(logger, metrics, start_time)
        return 0

    except GUSClientError as e:
        logger.error(f"GUS error: {e}")
        metrics["error"] = str(e)[:100]
        log_job_metrics(logger, metrics, start_time)
        return 2

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        metrics["error"] = str(e)[:100]
        log_job_metrics(logger, metrics, start_time)
        return 2




if __name__ == "__main__":
    sys.exit(main())
