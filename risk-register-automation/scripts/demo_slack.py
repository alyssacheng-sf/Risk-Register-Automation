#!/usr/bin/env python3
"""Demo script: Phase 3 - Slack Integration (Dry Run).

End-to-end flow:
  1. Fetch stale risks from GUS (real data)
  2. Analyze and prioritize
  3. Build notification messages
  4. Deliver via SlackClient (dry-run prints what would be sent)

Safety:
  - All notifications are targeted ONLY to alyssa.cheng@salesforce.com
  - Dry-run mode: no actual Slack API calls (no token needed)
  - To go live: set DRY_RUN=False and provide SLACK_BOT_TOKEN env var

Run with:
    python3 scripts/demo_slack.py

    # Or with a real token (when approved):
    SLACK_BOT_TOKEN=xoxb-... DRY_RUN=false python3 scripts/demo_slack.py
"""

import json
import os
import sys
sys.path.insert(0, ".")

from src.gus_client import GUSClient
from src.risk_analyzer import RiskAnalyzer
from src.notification_builder import NotificationBuilder
from src.slack_client import SlackClient


# === CONFIGURATION ===
TARGET_USER = "alyssa.cheng@salesforce.com"  # HARDCODED: only send to me
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", None)


def main():
    print("=" * 70)
    print("MCE Risk Register - Phase 3 Slack Integration Demo")
    print(f"Mode: {'🧪 DRY RUN (no messages sent)' if DRY_RUN else '🔴 LIVE'}")
    print(f"Target: {TARGET_USER}")
    print("=" * 70)

    # --- Step 1: Fetch data ---
    print("\n📡 Step 1: Fetching stale risks from GUS...")
    client = GUSClient()
    open_risks = client.get_open_risks()
    print(f"   Fetched {len(open_risks)} open risks")

    # --- Step 2: Analyze ---
    print("\n🔍 Step 2: Analyzing risks...")
    analyzer = RiskAnalyzer()
    result = analyzer.analyze(open_risks)
    print(f"   Total stale: {result.total_stale}/{result.total_open}")
    print(f"   Compliance: {result.compliance_rate_pct}")
    print(f"   Owners to notify: {len(result.by_owner)}")

    # --- Step 3: Build notifications ---
    print("\n📨 Step 3: Building notification messages...")
    builder = NotificationBuilder()

    # Initialize Slack client
    slack = SlackClient(
        bot_token=BOT_TOKEN,
        dry_run=DRY_RUN,
        target_user=TARGET_USER,  # SAFETY: all messages redirect here
    )

    # --- Notification Type 1: Owner Alert (DM) ---
    print("\n" + "=" * 70)
    print("📬 NOTIFICATION 1: Daily Owner Alert")
    print("=" * 70)
    # Use the top owner's risks as a demo
    sorted_owners = sorted(result.by_owner.items(), key=lambda x: -len(x[1]))
    if sorted_owners:
        demo_email, demo_risks = sorted_owners[0]
        owner_msg = builder.build_owner_alert(
            demo_risks[:8],  # Cap for readability
            TARGET_USER,
            categories=result.categorized,
        )
        slack.send_dm(TARGET_USER, owner_msg)

    # --- Notification Type 2: Escalation Alert ---
    print("\n" + "=" * 70)
    print("📈 NOTIFICATION 2: Escalation Alert (to manager)")
    print("=" * 70)
    escalations = analyzer.get_escalation_candidates(open_risks)
    if escalations:
        escalation_msg = builder.build_escalation_alert(
            escalations[:5],
            owner_email="ncolgin@salesforce.com",  # The default owner with 81 risks
            manager_name="Alyssa",
        )
        slack.send_dm(TARGET_USER, escalation_msg)

    # --- Notification Type 3: Weekly Summary ---
    print("\n" + "=" * 70)
    print("📊 NOTIFICATION 3: Weekly Summary Report")
    print("=" * 70)
    summary_msg = builder.build_weekly_summary(
        total_open=result.total_open,
        total_stale=result.total_stale,
        compliance_rate=result.compliance_rate,
        by_team=result.by_team,
        top_stale=result.prioritized[:5],
    )
    slack.send_dm(TARGET_USER, summary_msg)

    # --- Notification Type 4: Test Message ---
    print("\n" + "=" * 70)
    print("🧪 NOTIFICATION 4: Test Ping")
    print("=" * 70)
    slack.send_test_message(TARGET_USER)

    # --- Stats ---
    print("\n" + "=" * 70)
    print("📊 DELIVERY STATS")
    print("=" * 70)
    stats = slack.get_stats()
    print(f"   Total attempted: {stats['total_attempted']}")
    print(f"   Successful:      {stats['successful']}")
    print(f"   Failed:          {stats['failed']}")
    print(f"   Mode:            {'DRY RUN' if stats['dry_run'] else 'LIVE'}")
    print(f"   Target user:     {stats['target_user']}")

    # --- JSON export of delivery log ---
    log_file = "scripts/delivery_log.json"
    with open(log_file, "w") as f:
        json.dump(slack.get_delivery_log(), f, indent=2)
    print(f"\n   Delivery log saved to: {log_file}")

    # --- Next steps ---
    print(f"\n{'=' * 70}")
    print("✅ Phase 3 dry-run complete!")
    print(f"{'=' * 70}")
    print("\nTo go live (once app is approved):")
    print("  1. Get bot token from: https://api.slack.com/apps → OAuth & Permissions")
    print("  2. Run: SLACK_BOT_TOKEN=xoxb-... DRY_RUN=false python3 scripts/demo_slack.py")
    print("  3. Check your Slack DMs!")
    print()


if __name__ == "__main__":
    main()
