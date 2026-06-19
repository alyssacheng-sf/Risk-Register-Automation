"""Notification message builder using Slack Block Kit format.

Builds structured Slack messages for:
- Individual risk owner alerts (daily)
- Manager escalation alerts
- Weekly summary reports
- "All clear" messages (when no stale risks)

Messages follow Slack Block Kit spec:
https://api.slack.com/reference/block-kit
"""

import logging
from datetime import date
from typing import Dict, List, Optional

from .models.risk import Risk

logger = logging.getLogger(__name__)


# Severity emoji mapping
IMPACT_EMOJI = {
    "High": "🔴",
    "Medium": "🟡",
    "Low": "🟢",
    None: "⚪",
}

SCORE_EMOJI = {
    9: "🚨",  # High x High
    6: "⚠️",   # High x Med or Med x High
    4: "📋",  # Med x Med
    3: "📝",  # High x Low or Low x High
    2: "📌",  # Med x Low or Low x Med
    1: "✅",  # Low x Low
}


class NotificationBuilder:
    """Builds Slack Block Kit messages for risk notifications.

    Usage:
        builder = NotificationBuilder()
        blocks = builder.build_owner_alert(risks, "jsmith@salesforce.com")
        # Send blocks via Slack client
    """

    def build_owner_alert(
        self, risks: List[Risk], owner_email: str, categories: Optional[Dict[str, List[str]]] = None
    ) -> Dict:
        """Build a daily alert message for a risk owner.

        Groups their stale risks by priority and shows clear next actions.

        Args:
            risks: List of stale risks owned by this person.
            owner_email: The owner's email (for personalization).
            categories: Optional dict of risk_id -> category list.

        Returns:
            Slack Block Kit message dict with 'text' and 'blocks'.
        """
        if not risks:
            return self._build_all_clear(owner_email)

        # Sort by risk score (highest first)
        sorted_risks = sorted(risks, key=lambda r: -r.risk_score)

        count = len(risks)
        text = f"You have {count} stale risk{'s' if count != 1 else ''} that need review"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{'🚨' if count > 3 else '⚠️'} {text}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"📅 {date.today().strftime('%B %d, %Y')} • Risks not reviewed within SLA",
                    }
                ],
            },
            {"type": "divider"},
        ]

        # Add each risk as a section
        for risk in sorted_risks[:10]:  # Cap at 10 to avoid message size limits
            emoji = IMPACT_EMOJI.get(risk.impact, "⚪")
            score_emoji = SCORE_EMOJI.get(risk.risk_score, "📋")
            days = risk.days_since_review or "N/A"

            # Category tags
            cats = categories.get(risk.id, []) if categories else []
            cat_tags = " ".join(f"`{c}`" for c in cats[:3]) if cats else ""

            risk_block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{score_emoji} *{risk.name}*\n"
                        f"{emoji} Impact: {risk.impact or 'Unknown'} • "
                        f"Probability: {risk.probability or 'Unknown'} • "
                        f"Stale: *{days} days*\n"
                        f"Team: {risk.team_name or 'Unknown'}"
                        f"{' • ' + cat_tags if cat_tags else ''}"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in GUS"},
                    "url": risk.gus_url,
                    "action_id": f"view_risk_{risk.id}",
                },
            }
            blocks.append(risk_block)

        # Add overflow notice if more than 10
        if len(sorted_risks) > 10:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"_...and {len(sorted_risks) - 10} more stale risks. View all in the <https://gus.lightning.force.com/lightning/r/Dashboard/01ZEE000001Bgkv2AC/view|Risk Register Dashboard>._",
                }],
            })

        # Call to action
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "👉 *Action needed:* Review each risk and update `Last Reviewed Date` in GUS to clear this alert.",
            },
        })

        return {"text": text, "blocks": blocks}

    def build_escalation_alert(
        self, risks: List[Risk], owner_email: str, manager_name: Optional[str] = None
    ) -> Dict:
        """Build an escalation message for a manager.

        Sent when a risk owner hasn't responded to notifications.

        Args:
            risks: Stale risks that need escalation.
            owner_email: The original owner who hasn't responded.
            manager_name: Optional manager name for addressing.
        """
        count = len(risks)
        greeting = f"Hi {manager_name}," if manager_name else "Hi,"

        text = f"Escalation: {count} risk{'s' if count != 1 else ''} from {owner_email} need attention"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📈 Risk Escalation"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{greeting}\n\n"
                        f"*{count}* risk{'s' if count != 1 else ''} owned by `{owner_email}` "
                        f"{'have' if count != 1 else 'has'} not been reviewed despite notifications. "
                        f"These risks are significantly past their review SLA."
                    ),
                },
            },
            {"type": "divider"},
        ]

        for risk in sorted(risks, key=lambda r: -r.risk_score)[:5]:
            days = risk.days_since_review or "N/A"
            emoji = IMPACT_EMOJI.get(risk.impact, "⚪")
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{risk.name}*\n"
                        f"Impact: {risk.impact} • Stale: *{days} days* • "
                        f"<{risk.gus_url}|View in GUS>"
                    ),
                },
            })

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Please ensure these risks are reviewed or reassigned.",
            },
        })

        return {"text": text, "blocks": blocks}

    def build_weekly_summary(
        self,
        total_open: int,
        total_stale: int,
        compliance_rate: float,
        by_team: Dict[str, List[Risk]],
        top_stale: List[Risk],
        newly_closed: int = 0,
        newly_added: int = 0,
    ) -> Dict:
        """Build the weekly summary report for leadership.

        Args:
            total_open: Total open risks.
            total_stale: Total stale risks.
            compliance_rate: Float 0-1 representing % compliant.
            by_team: Dict of team_name -> risks.
            top_stale: Top N oldest stale risks.
            newly_closed: Risks closed this week.
            newly_added: New risks this week.
        """
        pct = f"{compliance_rate * 100:.0f}%"
        text = f"MCE Risk Register Weekly Summary - {date.today().strftime('%B %d, %Y')}"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📊 {text}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Key Metrics*\n"
                        f"• Total Open Risks: *{total_open}*\n"
                        f"• Stale (Need Review): *{total_stale}*\n"
                        f"• Review Compliance: *{pct}*\n"
                        f"• Closed This Week: *{newly_closed}*\n"
                        f"• New This Week: *{newly_added}*"
                    ),
                },
            },
            {"type": "divider"},
        ]

        # Team breakdown (top 10)
        team_lines = []
        sorted_teams = sorted(by_team.items(), key=lambda x: -len(x[1]))
        for team, risks in sorted_teams[:10]:
            high_count = sum(1 for r in risks if r.impact == "High")
            team_lines.append(f"• {team}: *{len(risks)}* stale ({high_count} high impact)")

        if team_lines:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*By Team (Top 10):*\n" + "\n".join(team_lines),
                },
            })
            blocks.append({"type": "divider"})

        # Oldest risks
        if top_stale:
            stale_lines = []
            for i, risk in enumerate(top_stale[:5], 1):
                days = risk.days_since_review or "?"
                stale_lines.append(
                    f"{i}. <{risk.gus_url}|{risk.name[:50]}> — "
                    f"*{days} days* ({risk.owner_name or risk.owner_email or 'Unknown'})"
                )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*⏰ Oldest Stale Risks:*\n" + "\n".join(stale_lines),
                },
            })

        # Footer
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    "📋 <https://gus.lightning.force.com/lightning/r/Dashboard/01ZEE000001Bgkv2AC/view|"
                    "View Full Dashboard> • Generated by Risk Register Automation"
                ),
            }],
        })

        return {"text": text, "blocks": blocks}

    def _build_all_clear(self, owner_email: str) -> Dict:
        """Build an 'all clear' message when owner has no stale risks."""
        text = "All your risks are reviewed and current!"
        return {
            "text": text,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"✅ *All clear!* All your risks are reviewed within SLA. Nice work! 🎉",
                    },
                },
            ],
        }

    def build_plain_text_summary(self, risks: List[Risk]) -> str:
        """Build a plain-text summary (for logging/email fallback).

        Args:
            risks: List of stale risks.

        Returns:
            Formatted plain text string.
        """
        lines = [
            f"MCE Risk Register - Stale Risk Alert ({date.today()})",
            f"{'=' * 50}",
            f"Total stale risks: {len(risks)}",
            "",
        ]

        for i, risk in enumerate(sorted(risks, key=lambda r: -r.risk_score), 1):
            days = risk.days_since_review or "N/A"
            lines.append(
                f"{i:3}. [{risk.impact or '?':6}] {risk.name[:50]:<50} "
                f"({days} days) - {risk.owner_name or risk.owner_email or 'Unknown'}"
            )

        lines.append(f"\n{'=' * 50}")
        lines.append(f"Dashboard: https://gus.lightning.force.com/lightning/r/Dashboard/01ZEE000001Bgkv2AC/view")
        return "\n".join(lines)
