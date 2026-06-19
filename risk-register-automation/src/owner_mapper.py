"""Map GUS owner emails to Slack user IDs for notification delivery.

This module resolves the mapping between:
  - GUS Owner.Email (e.g., jsmith@salesforce.com)
  - Slack user ID (e.g., U0123456789)

For Phase 2 (MVP), we use a static mapping + Slack API lookup.
For production, this could integrate with an LDAP/directory service.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Cache file for email -> Slack ID mappings
CACHE_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = CACHE_DIR / "slack_user_cache.json"


class OwnerMapper:
    """Maps GUS owner emails to Slack user IDs and notification targets.

    Usage:
        mapper = OwnerMapper()
        slack_id = mapper.get_slack_id("jsmith@salesforce.com")
        # Returns Slack user ID or None if not found
    """

    def __init__(
        self,
        slack_client=None,
        cache_file: Optional[Path] = None,
        team_channels: Optional[Dict[str, str]] = None,
    ):
        """Initialize the owner mapper.

        Args:
            slack_client: Optional Slack SDK client for user lookups.
                         (Will be integrated in Phase 3)
            cache_file: Path to the email->Slack ID cache file.
            team_channels: Mapping of team name to Slack channel ID for fallback.
        """
        self.slack_client = slack_client
        self.cache_file = cache_file or CACHE_FILE
        self._cache: Dict[str, str] = self._load_cache()
        self.team_channels = team_channels or {}

    def get_slack_id(self, email: str) -> Optional[str]:
        """Look up a Slack user ID by email.

        Checks local cache first, then falls back to Slack API.

        Args:
            email: Salesforce email (e.g., jsmith@salesforce.com)

        Returns:
            Slack user ID (e.g., U0123456789) or None if not found.
        """
        if not email:
            return None

        email = email.lower()

        # Check cache first
        if email in self._cache:
            return self._cache[email]

        # Try Slack API lookup (Phase 3)
        if self.slack_client:
            slack_id = self._lookup_via_slack(email)
            if slack_id:
                self._cache[email] = slack_id
                self._save_cache()
                return slack_id

        logger.debug(f"No Slack ID found for {email}")
        return None

    def get_notification_target(self, email: str, team_name: Optional[str] = None) -> "NotificationTarget":
        """Determine where to send a notification for this owner.

        Priority:
        1. Direct message to owner's Slack ID (if found)
        2. Team channel (if team_name is mapped)
        3. Fallback channel (default alerts channel)

        Args:
            email: Owner's email address.
            team_name: Optional team name for channel fallback.

        Returns:
            NotificationTarget with type and destination.
        """
        # Try direct Slack ID
        slack_id = self.get_slack_id(email)
        if slack_id:
            return NotificationTarget(
                type="direct_message",
                destination=slack_id,
                owner_email=email,
            )

        # Try team channel
        if team_name and team_name in self.team_channels:
            return NotificationTarget(
                type="channel",
                destination=self.team_channels[team_name],
                owner_email=email,
            )

        # Fallback — will be handled by notification delivery
        return NotificationTarget(
            type="fallback",
            destination=None,
            owner_email=email,
        )

    def bulk_resolve(self, emails: List[str]) -> Dict[str, Optional[str]]:
        """Resolve multiple emails to Slack IDs at once.

        Args:
            emails: List of email addresses.

        Returns:
            Dict mapping email to Slack ID (or None).
        """
        results = {}
        for email in set(emails):
            results[email] = self.get_slack_id(email)

        resolved = sum(1 for v in results.values() if v)
        logger.info(f"Resolved {resolved}/{len(results)} emails to Slack IDs")
        return results

    def add_to_cache(self, email: str, slack_id: str) -> None:
        """Manually add a mapping to the cache.

        Useful for bootstrapping or manual overrides.
        """
        self._cache[email.lower()] = slack_id
        self._save_cache()

    def get_cache_stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        return {
            "total_cached": len(self._cache),
            "team_channels": len(self.team_channels),
        }

    # --- Private Methods ---

    def _lookup_via_slack(self, email: str) -> Optional[str]:
        """Look up a Slack user ID via the Slack API (users.lookupByEmail).

        Will be implemented in Phase 3 when Slack SDK is integrated.
        """
        try:
            if self.slack_client:
                response = self.slack_client.users_lookupByEmail(email=email)
                if response.get("ok"):
                    return response["user"]["id"]
        except Exception as e:
            logger.warning(f"Slack lookup failed for {email}: {e}")
        return None

    def _load_cache(self) -> Dict[str, str]:
        """Load the email->Slack ID cache from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load Slack cache: {e}")
        return {}

    def _save_cache(self) -> None:
        """Persist the cache to disk."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(self._cache, f, indent=2)
        except IOError as e:
            logger.warning(f"Failed to save Slack cache: {e}")


class NotificationTarget:
    """Where to send a notification for a risk owner."""

    def __init__(self, type: str, destination: Optional[str], owner_email: str):
        """
        Args:
            type: One of 'direct_message', 'channel', 'fallback'.
            destination: Slack user ID or channel ID (None for fallback).
            owner_email: Original email for reference.
        """
        self.type = type
        self.destination = destination
        self.owner_email = owner_email

    @property
    def is_resolved(self) -> bool:
        """Whether we have a concrete destination."""
        return self.destination is not None

    def __repr__(self):
        return f"NotificationTarget(type={self.type}, dest={self.destination}, email={self.owner_email})"
