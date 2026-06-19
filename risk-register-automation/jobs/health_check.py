#!/usr/bin/env python3
"""Health Check Job — Phase 4.

Verifies that all dependencies are reachable:
- GUS API (sf CLI authenticated)
- Slack API (token valid, if configured)
- Data directory writable
- Log directory writable

Designed to run every 6 hours via cron as a canary.

Usage:
    python3 jobs/health_check.py

Exit codes:
    0 - All checks pass
    1 - One or more checks failed (non-fatal, logged)
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gus_client import GUSClient
from src.slack_client import SlackClient
from jobs.job_logging import setup_logging, log_job_metrics


BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", None)


def main() -> int:
    logger = setup_logging("health_check")
    start_time = time.time()
    checks_passed = 0
    checks_failed = 0

    logger.info("Running health checks...")

    # --- Check 1: GUS connectivity ---
    logger.info("Check 1: GUS API...")
    try:
        gus = GUSClient()
        if gus.health_check():
            logger.info("  GUS: OK")
            checks_passed += 1
        else:
            logger.error("  GUS: FAILED (sf CLI query returned non-zero)")
            checks_failed += 1
    except Exception as e:
        logger.error(f"  GUS: FAILED ({e})")
        checks_failed += 1

    # --- Check 2: Slack API (if token configured) ---
    logger.info("Check 2: Slack API...")
    if BOT_TOKEN:
        try:
            slack = SlackClient(bot_token=BOT_TOKEN, dry_run=False)
            # Try to look up a known user as a connectivity test
            user_id = slack.lookup_user_by_email("alyssa.cheng@salesforce.com")
            if user_id:
                logger.info(f"  Slack: OK (resolved user)")
                checks_passed += 1
            else:
                logger.warning("  Slack: DEGRADED (token works but user not found)")
                checks_passed += 1  # Token is valid, just user lookup issue
        except Exception as e:
            logger.error(f"  Slack: FAILED ({e})")
            checks_failed += 1
    else:
        logger.info("  Slack: SKIPPED (no SLACK_BOT_TOKEN set)")
        checks_passed += 1

    # --- Check 3: Data directory writable ---
    logger.info("Check 3: Data directory...")
    data_dir = Path("data")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        logger.info("  Data dir: OK (writable)")
        checks_passed += 1
    except Exception as e:
        logger.error(f"  Data dir: FAILED ({e})")
        checks_failed += 1

    # --- Check 4: Logs directory writable ---
    logger.info("Check 4: Logs directory...")
    log_dir = Path("logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        logger.info("  Logs dir: OK (writable)")
        checks_passed += 1
    except Exception as e:
        logger.error(f"  Logs dir: FAILED ({e})")
        checks_failed += 1

    # --- Summary ---
    total = checks_passed + checks_failed
    logger.info(f"Health check: {checks_passed}/{total} passed")

    metrics = {
        "job": "health_check",
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "result": "healthy" if checks_failed == 0 else "degraded",
    }
    log_job_metrics(logger, metrics, start_time)

    return 0 if checks_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
