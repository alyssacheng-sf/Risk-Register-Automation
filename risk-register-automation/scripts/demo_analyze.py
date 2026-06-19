#!/usr/bin/env python3
"""Demo script: Full Phase 2 analysis pipeline.

Fetches open risks → analyzes → categorizes → shows who to notify.

Run with:
    python3 scripts/demo_analyze.py
"""

import json
import sys
sys.path.insert(0, ".")

from src.gus_client import GUSClient
from src.risk_analyzer import RiskAnalyzer
from src.notification_builder import NotificationBuilder
from src.owner_mapper import OwnerMapper


def main():
    print("=" * 70)
    print("MCE Risk Register - Phase 2 Analysis Demo")
    print("=" * 70)

    # 1. Fetch data (Phase 1)
    print("\n📡 Fetching open risks from GUS...")
    client = GUSClient()
    open_risks = client.get_open_risks()
    print(f"   Fetched {len(open_risks)} open risks")

    # 2. Analyze (Phase 2)
    print("\n🔍 Running analysis...")
    analyzer = RiskAnalyzer()
    result = analyzer.analyze(open_risks)

    print(f"\n{'─' * 70}")
    print(f"📊 ANALYSIS RESULTS")
    print(f"{'─' * 70}")
    print(f"   Total open:        {result.total_open}")
    print(f"   Stale:             {result.total_stale}")
    print(f"   Current:           {result.total_current}")
    print(f"   Compliance rate:   {result.compliance_rate_pct}")
    print(f"   Teams affected:    {len(result.by_team)}")
    print(f"   Owners to notify:  {len(result.by_owner)}")

    # 3. Categorization
    print(f"\n{'─' * 70}")
    print(f"🏷️  CATEGORIES")
    print(f"{'─' * 70}")
    for cat, risks in sorted(result.by_category.items(), key=lambda x: -len(x[1])):
        print(f"   {cat:<20} {len(risks):>3} risks")

    # 4. Escalation candidates
    print(f"\n{'─' * 70}")
    print(f"📈 ESCALATION CANDIDATES (stale > 14 days past threshold)")
    print(f"{'─' * 70}")
    escalations = analyzer.get_escalation_candidates(open_risks)
    for risk in escalations[:10]:
        days = risk.days_since_review or "?"
        print(f"   🔴 [{risk.impact}] {risk.name[:45]:<45} ({days} days) - {risk.owner_email or '?'}")
    if len(escalations) > 10:
        print(f"   ... and {len(escalations) - 10} more")

    # 5. Owner notification preview
    print(f"\n{'─' * 70}")
    print(f"📬 NOTIFICATION TARGETS (top 5 owners by stale risk count)")
    print(f"{'─' * 70}")
    sorted_owners = sorted(result.by_owner.items(), key=lambda x: -len(x[1]))
    for email, risks in sorted_owners[:5]:
        high = sum(1 for r in risks if r.impact == "High")
        print(f"   {email:<40} {len(risks)} stale ({high} high impact)")

    # 6. Sample notification message
    print(f"\n{'─' * 70}")
    print(f"💬 SAMPLE NOTIFICATION (for top owner)")
    print(f"{'─' * 70}")
    if sorted_owners:
        top_email, top_risks = sorted_owners[0]
        builder = NotificationBuilder()
        msg = builder.build_owner_alert(top_risks, top_email, categories=result.categorized)
        print(f"   To: {top_email}")
        print(f"   Subject: {msg['text']}")
        print(f"   Blocks: {len(msg['blocks'])} Slack blocks")
        # Show plain text version
        plain = builder.build_plain_text_summary(top_risks)
        print(f"\n   Plain text preview:")
        for line in plain.split("\n")[:10]:
            print(f"   {line}")

    # 7. Weekly summary preview
    print(f"\n{'─' * 70}")
    print(f"📊 WEEKLY SUMMARY PREVIEW")
    print(f"{'─' * 70}")
    builder = NotificationBuilder()
    summary = builder.build_weekly_summary(
        total_open=result.total_open,
        total_stale=result.total_stale,
        compliance_rate=result.compliance_rate,
        by_team=result.by_team,
        top_stale=result.prioritized[:5],
    )
    print(f"   Title: {summary['text']}")
    print(f"   Blocks: {len(summary['blocks'])} Slack blocks")

    print(f"\n{'=' * 70}")
    print("Phase 2 analysis pipeline complete. ✅")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
