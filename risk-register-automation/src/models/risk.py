"""Data model for PPM_Project_Risk__c records from GUS."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class Risk:
    """Represents a single risk from the FY26 Engagement Risk Register.

    Maps directly to PPM_Project_Risk__c in GUS.
    """

    # Core identifiers
    id: str
    name: str  # Project Risk Name
    project_risk_number: Optional[str] = None

    # Status & classification
    status: Optional[str] = None  # Open, Closed, Escalated, Accepted
    impact: Optional[str] = None  # High, Medium, Low
    probability: Optional[str] = None  # High, Medium, Low
    escalation_level: Optional[str] = None  # Program, Project, S2

    # Dates
    identified_on: Optional[date] = None
    last_reviewed_date: Optional[date] = None
    target_close_on: Optional[date] = None
    closed_on: Optional[date] = None
    created_date: Optional[datetime] = None
    last_modified_date: Optional[datetime] = None

    # Ownership
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    risk_owner_id: Optional[str] = None
    risk_owner_name: Optional[str] = None

    # Team
    team_id: Optional[str] = None
    team_name: Optional[str] = None

    # Content
    details: Optional[str] = None
    impact_description: Optional[str] = None
    mitigation_strategy: Optional[str] = None
    closure_criteria: Optional[str] = None

    # Flags
    feature_flag_kill_switch: bool = False

    # --- Computed Properties ---

    @property
    def days_since_review(self) -> Optional[int]:
        """Days since last review, or days since identification if never reviewed."""
        reference_date = self.last_reviewed_date or self.identified_on
        if reference_date is None:
            # Fall back to created_date
            if self.created_date:
                return (date.today() - self.created_date.date()).days
            return None
        return (date.today() - reference_date).days

    @property
    def is_stale(self) -> bool:
        """Check if risk is stale based on impact-based thresholds.

        Thresholds (from dashboard "Need Review" logic):
          High impact:   > 7 days
          Medium impact: > 14 days
          Low impact:    > 30 days
          Unknown:       > 14 days (default to medium)
        """
        days = self.days_since_review
        if days is None:
            return True  # Never reviewed = stale
        threshold = self.stale_threshold_days
        return days > threshold

    @property
    def stale_threshold_days(self) -> int:
        """Get staleness threshold based on impact level."""
        thresholds = {
            "High": 7,
            "Medium": 14,
            "Low": 30,
        }
        return thresholds.get(self.impact, 14)

    @property
    def risk_score(self) -> int:
        """Compute a simple risk score (1-9) from impact x probability.

        Higher = more urgent. Used for prioritizing notifications.
        """
        score_map = {"High": 3, "Medium": 2, "Low": 1}
        impact_score = score_map.get(self.impact, 2)
        probability_score = score_map.get(self.probability, 2)
        return impact_score * probability_score

    @property
    def is_open(self) -> bool:
        """Whether this risk is in an active/open state."""
        return self.status in ("Open", "Escalated", None)

    @property
    def gus_url(self) -> str:
        """Direct URL to this risk in GUS."""
        return f"https://gus.lightning.force.com/lightning/r/PPM_Project_Risk__c/{self.id}/view"

    @classmethod
    def from_gus_record(cls, record: dict) -> "Risk":
        """Create a Risk from a raw GUS SOQL API response record.

        Args:
            record: A single record dict from sf data query --json output.
        """
        owner = record.get("Owner") or {}
        risk_owner = record.get("Risk_Owner__r") or {}
        team = record.get("Team__r") or {}

        return cls(
            id=record["Id"],
            name=record.get("Name", ""),
            project_risk_number=record.get("Project_Risk_Number__c"),
            status=record.get("Status__c"),
            impact=record.get("Impact__c"),
            probability=record.get("Probability__c"),
            escalation_level=record.get("Escalation_Level__c"),
            identified_on=_parse_date(record.get("Identified_On__c")),
            last_reviewed_date=_parse_date(record.get("Last_Reviewed_Date__c")),
            target_close_on=_parse_date(record.get("Target_Close_on__c")),
            closed_on=_parse_date(record.get("Closed_On__c")),
            created_date=_parse_datetime(record.get("CreatedDate")),
            last_modified_date=_parse_datetime(record.get("LastModifiedDate")),
            owner_id=record.get("OwnerId"),
            owner_name=owner.get("Name"),
            owner_email=owner.get("Email"),
            risk_owner_id=record.get("Risk_Owner__c"),
            risk_owner_name=risk_owner.get("Name"),
            team_id=record.get("Team__c"),
            team_name=team.get("Name"),
            details=record.get("Details__c"),
            impact_description=record.get("Impact_Description__c"),
            mitigation_strategy=record.get("Mitigation_Strategy__c"),
            closure_criteria=record.get("Closure_Criteria__c"),
            feature_flag_kill_switch=record.get("Feature_Flag_Kill_Switch__c", False),
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict (for JSON storage/snapshots)."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "impact": self.impact,
            "probability": self.probability,
            "escalation_level": self.escalation_level,
            "last_reviewed_date": str(self.last_reviewed_date) if self.last_reviewed_date else None,
            "identified_on": str(self.identified_on) if self.identified_on else None,
            "days_since_review": self.days_since_review,
            "is_stale": self.is_stale,
            "risk_score": self.risk_score,
            "owner_name": self.owner_name,
            "owner_email": self.owner_email,
            "risk_owner_name": self.risk_owner_name,
            "team_name": self.team_name,
            "gus_url": self.gus_url,
        }


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a SOQL date string (YYYY-MM-DD) to a date object."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a SOQL datetime string to a datetime object."""
    if not value:
        return None
    try:
        # GUS returns ISO format like 2024-10-16T11:37:27.000+0000
        return datetime.fromisoformat(value.replace("+0000", "+00:00"))
    except (ValueError, TypeError):
        return None
