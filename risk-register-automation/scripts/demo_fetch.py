#!/usr/bin/env python3
"""Demo script: Fetch open risks from GUS and show which are stale.

Run with:
    python3 scripts/demo_fetch.py
"""

import sys
sys.path.insert(0, ".")

from src.gus_client import GUSClient


def main():
    print("=" * 70)
    print("MCE Risk Register - Data Retrieval Demo")
    print("=" * 70)

    client = GUSClient()

    # Health check
    print("\n1. Health check...")
    if not client.health_check():
        print("   ❌ Cannot connect to GUS. Is 'sf' authenticated?")
        sys.exit(1)
    print("   ✅ GUS connection healthy")

    # Fetch open risks
    print("\n2. Fetching open risks for MCE teams...")
    open_risks = client.get_open_risks()
    print(f"   Found {len(open_risks)} open risks")

    # Identify stale risks
    stale = [r for r in open_risks if r.is_stale]
    not_stale = [r for r in open_risks if not r.is_stale]
    print(f"\n3. Staleness analysis:")
    print(f"   🔴 Stale:     {len(stale)}")
    print(f"   🟢 Current:   {len(not_stale)}")

    # Show top stale risks
    if stale:
        print(f"\n4. Top 10 stale risks (by days since review):")
        print(f"   {'Risk Name':<45} {'Impact':<8} {'Days':<6} {'Team'}")
        print(f"   {'-'*45} {'-'*8} {'-'*6} {'-'*30}")
        sorted_stale = sorted(stale, key=lambda r: r.days_since_review or 9999, reverse=True)
        for risk in sorted_stale[:10]:
            name = (risk.name[:42] + "...") if len(risk.name) > 45 else risk.name
            days = risk.days_since_review or "N/A"
            team = (risk.team_name[:30]) if risk.team_name else "Unknown"
            print(f"   {name:<45} {risk.impact or '-':<8} {str(days):<6} {team}")

    # Breakdown by team
    print(f"\n5. Open risks by team:")
    teams = {}
    for r in open_risks:
        t = r.team_name or "Unknown"
        teams[t] = teams.get(t, 0) + 1
    for team, count in sorted(teams.items(), key=lambda x: -x[1])[:15]:
        print(f"   {team:<50} {count}")

    # Score distribution
    print(f"\n6. Risk score distribution (impact × probability):")
    scores = {}
    for r in open_risks:
        s = r.risk_score
        scores[s] = scores.get(s, 0) + 1
    for score in sorted(scores.keys(), reverse=True):
        bar = "█" * scores[score]
        print(f"   Score {score}: {bar} ({scores[score]})")

    print(f"\n{'=' * 70}")
    print("Done! Phase 1 data retrieval working. ✅")


if __name__ == "__main__":
    main()
