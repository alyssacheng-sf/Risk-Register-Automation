"""Risk analysis engine: staleness detection, categorization, and prioritization.

This module is the "intelligence layer" that determines which risks need
attention and how urgently.
"""

import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from .models.risk import Risk

logger = logging.getLogger(__name__)

# Load category config
CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_categories(config_path: Optional[Path] = None) -> Dict[str, List[str]]:
    """Load category keyword definitions from YAML config.

    Returns:
        Dict mapping category name to list of keywords.
    """
    path = config_path or (CONFIG_DIR / "categories.yaml")
    with open(path) as f:
        config = yaml.safe_load(f)

    categories = {}
    for name, data in config.get("categories", {}).items():
        categories[name] = [kw.lower() for kw in data.get("keywords", [])]
    return categories


def load_thresholds(config_path: Optional[Path] = None) -> Dict[str, int]:
    """Load staleness thresholds from YAML config.

    Returns:
        Dict mapping impact level to days threshold.
    """
    path = config_path or (CONFIG_DIR / "thresholds.yaml")
    with open(path) as f:
        config = yaml.safe_load(f)
    return config.get("stale_thresholds", {"High": 7, "Medium": 14, "Low": 30, "default": 14})


class RiskAnalyzer:
    """Analyzes risks to determine staleness, categories, and priority.

    Usage:
        analyzer = RiskAnalyzer()
        results = analyzer.analyze(open_risks)
        for owner_email, owner_risks in results.by_owner.items():
            # Send notification...
    """

    def __init__(self, categories: Optional[Dict[str, List[str]]] = None):
        """Initialize with category keywords.

        Args:
            categories: Optional override for category config. If None, loads from YAML.
        """
        self.categories = categories or load_categories()

    def analyze(self, risks: List[Risk]) -> "AnalysisResult":
        """Run full analysis on a list of risks.

        Returns an AnalysisResult with stale risks grouped by owner, team,
        category, and priority.
        """
        stale_risks = [r for r in risks if r.is_stale]
        current_risks = [r for r in risks if not r.is_stale]

        # Categorize all stale risks
        categorized = {}
        for risk in stale_risks:
            categorized[risk.id] = self.categorize(risk)

        # Group by owner
        by_owner = self._group_by_owner(stale_risks)

        # Group by team
        by_team = self._group_by_team(stale_risks)

        # Group by category
        by_category = self._group_by_category(stale_risks, categorized)

        # Priority sort (highest risk_score first, then by days stale)
        prioritized = sorted(
            stale_risks,
            key=lambda r: (-(r.risk_score), -(r.days_since_review or 0)),
        )

        return AnalysisResult(
            total_open=len(risks),
            total_stale=len(stale_risks),
            total_current=len(current_risks),
            stale_risks=stale_risks,
            current_risks=current_risks,
            prioritized=prioritized,
            by_owner=by_owner,
            by_team=by_team,
            by_category=by_category,
            categorized=categorized,
        )

    def categorize(self, risk: Risk) -> List[str]:
        """Assign categories to a risk based on keyword matching.

        Matches against risk name + details (case-insensitive).
        A risk can have multiple categories.

        Returns:
            List of category names that matched.
        """
        # Build searchable text from name + details
        text_parts = [risk.name or ""]
        if risk.details:
            text_parts.append(risk.details)
        if risk.impact_description:
            text_parts.append(risk.impact_description)
        if risk.mitigation_strategy:
            text_parts.append(risk.mitigation_strategy)

        search_text = " ".join(text_parts).lower()

        matched = []
        for category, keywords in self.categories.items():
            for keyword in keywords:
                # Use word boundary matching for short keywords to avoid false positives
                if len(keyword) <= 3:
                    pattern = r'\b' + re.escape(keyword) + r'\b'
                    if re.search(pattern, search_text):
                        matched.append(category)
                        break
                else:
                    if keyword in search_text:
                        matched.append(category)
                        break

        return matched if matched else ["uncategorized"]

    def get_escalation_candidates(
        self, risks: List[Risk], days_without_response: int = 14
    ) -> List[Risk]:
        """Find risks that should be escalated to manager.

        A risk should be escalated if:
        - It's stale AND
        - It's been stale for longer than `days_without_response` beyond its threshold
        - OR it has High impact and is stale

        Args:
            risks: List of risks to check.
            days_without_response: Extra days past threshold before escalation.
        """
        candidates = []
        for risk in risks:
            if not risk.is_stale:
                continue
            days = risk.days_since_review or 0
            threshold = risk.stale_threshold_days

            # Escalate if way past threshold, or high impact and stale
            if days > (threshold + days_without_response):
                candidates.append(risk)
            elif risk.impact == "High" and days > threshold:
                candidates.append(risk)

        return sorted(candidates, key=lambda r: -(r.risk_score))

    def get_newly_stale(
        self, risks: List[Risk], since_days: int = 1
    ) -> List[Risk]:
        """Find risks that became stale in the last N days.

        Useful for daily notifications — only alert on newly-stale risks,
        not ones that have been stale for weeks.

        Args:
            risks: List of risks to check.
            since_days: How many days back to look.
        """
        newly_stale = []
        for risk in risks:
            if not risk.is_stale:
                continue
            days = risk.days_since_review
            if days is None:
                continue
            threshold = risk.stale_threshold_days
            # Became stale within the window
            if threshold < days <= threshold + since_days:
                newly_stale.append(risk)
        return newly_stale

    def compute_compliance_rate(self, risks: List[Risk]) -> float:
        """Calculate the % of risks that are NOT stale (reviewed within SLA).

        Returns:
            Float between 0.0 and 1.0.
        """
        if not risks:
            return 1.0
        current = sum(1 for r in risks if not r.is_stale)
        return current / len(risks)

    # --- Private Helpers ---

    def _group_by_owner(self, risks: List[Risk]) -> Dict[str, List[Risk]]:
        """Group risks by owner email. Uses risk_owner if available, else record owner."""
        grouped = defaultdict(list)
        for risk in risks:
            # Prefer risk_owner, fall back to record owner
            email = risk.owner_email or "unknown"
            grouped[email].append(risk)
        return dict(grouped)

    def _group_by_team(self, risks: List[Risk]) -> Dict[str, List[Risk]]:
        """Group risks by team name."""
        grouped = defaultdict(list)
        for risk in risks:
            team = risk.team_name or "Unknown"
            grouped[team].append(risk)
        return dict(grouped)

    def _group_by_category(
        self, risks: List[Risk], categorized: Dict[str, List[str]]
    ) -> Dict[str, List[Risk]]:
        """Group risks by category (a risk can appear in multiple categories)."""
        grouped = defaultdict(list)
        for risk in risks:
            cats = categorized.get(risk.id, ["uncategorized"])
            for cat in cats:
                grouped[cat].append(risk)
        return dict(grouped)


class AnalysisResult:
    """Results of risk analysis — structured for notification and reporting."""

    def __init__(
        self,
        total_open: int,
        total_stale: int,
        total_current: int,
        stale_risks: List[Risk],
        current_risks: List[Risk],
        prioritized: List[Risk],
        by_owner: Dict[str, List[Risk]],
        by_team: Dict[str, List[Risk]],
        by_category: Dict[str, List[Risk]],
        categorized: Dict[str, List[str]],
    ):
        self.total_open = total_open
        self.total_stale = total_stale
        self.total_current = total_current
        self.stale_risks = stale_risks
        self.current_risks = current_risks
        self.prioritized = prioritized
        self.by_owner = by_owner
        self.by_team = by_team
        self.by_category = by_category
        self.categorized = categorized  # risk_id -> [category names]

    @property
    def compliance_rate(self) -> float:
        """Percentage of risks that are current (not stale)."""
        total = self.total_open
        if total == 0:
            return 1.0
        return self.total_current / total

    @property
    def compliance_rate_pct(self) -> str:
        """Human-readable compliance rate."""
        return f"{self.compliance_rate * 100:.0f}%"

    def summary(self) -> str:
        """Quick text summary of the analysis."""
        lines = [
            f"Open risks: {self.total_open}",
            f"Stale: {self.total_stale} ({100 - self.compliance_rate * 100:.0f}% non-compliant)",
            f"Current: {self.total_current}",
            f"Teams affected: {len(self.by_team)}",
            f"Owners to notify: {len(self.by_owner)}",
        ]
        return "\n".join(lines)
