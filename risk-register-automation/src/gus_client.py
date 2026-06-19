"""GUS SOQL API client for fetching PPM_Project_Risk__c records.

Uses the Salesforce CLI (`sf data query`) under the hood, since the intern
already has `sf` authenticated with --target-org gus. This avoids needing
separate OAuth token management for MVP.

For production, this could be swapped to use `simple_salesforce` or `requests`
with a connected app — the interface stays the same.
"""

import json
import logging
import subprocess
import time
from typing import List, Optional

from .models.risk import Risk

logger = logging.getLogger(__name__)

# All fields we need from PPM_Project_Risk__c
RISK_FIELDS = [
    "Id",
    "Name",
    "Project_Risk_Number__c",
    "Status__c",
    "Impact__c",
    "Probability__c",
    "Escalation_Level__c",
    "Identified_On__c",
    "Last_Reviewed_Date__c",
    "Target_Close_on__c",
    "Closed_On__c",
    "CreatedDate",
    "LastModifiedDate",
    "OwnerId",
    "Owner.Name",
    "Owner.Email",
    "Risk_Owner__c",
    "Risk_Owner__r.Name",
    "Team__c",
    "Team__r.Name",
    "Details__c",
    "Impact_Description__c",
    "Mitigation_Strategy__c",
    "Closure_Criteria__c",
    "Feature_Flag_Kill_Switch__c",
]

# MCE teams to monitor (from the FY26 Engagement Risk Register dashboard)
MCE_TEAM_FILTER = """(
    Team__r.Name LIKE '%MC %'
    OR Team__r.Name LIKE '%E360%'
    OR Team__r.Name LIKE '%Engagement%'
    OR Team__r.Name LIKE '%SFMC%'
    OR Team__r.Name LIKE '%JB%'
)"""


class GUSClientError(Exception):
    """Raised when a GUS query fails."""

    pass


class GUSClient:
    """Client for querying PPM_Project_Risk__c via Salesforce CLI.

    Usage:
        client = GUSClient()
        open_risks = client.get_open_risks()
        stale = [r for r in open_risks if r.is_stale]
    """

    def __init__(self, target_org: str = "gus", max_retries: int = 3):
        """Initialize the GUS client.

        Args:
            target_org: The sf CLI org alias to query against.
            max_retries: Number of retries for transient failures.
        """
        self.target_org = target_org
        self.max_retries = max_retries

    def get_all_risks(self, limit: Optional[int] = None) -> List[Risk]:
        """Fetch all risks (any status) for MCE teams.

        Args:
            limit: Optional row limit for testing.
        """
        query = self._build_query(
            where=f"WHERE {MCE_TEAM_FILTER}",
            order_by="ORDER BY Status__c, Impact__c, Last_Reviewed_Date__c ASC NULLS FIRST",
            limit=limit,
        )
        return self._execute_query(query)

    def get_open_risks(self, limit: Optional[int] = None) -> List[Risk]:
        """Fetch all open/escalated risks for MCE teams.

        Args:
            limit: Optional row limit for testing.
        """
        query = self._build_query(
            where=f"WHERE Status__c IN ('Open', 'Escalated') AND {MCE_TEAM_FILTER}",
            order_by="ORDER BY Impact__c, Last_Reviewed_Date__c ASC NULLS FIRST",
            limit=limit,
        )
        return self._execute_query(query)

    def get_stale_risks(self) -> List[Risk]:
        """Fetch open risks and return only those that are stale.

        Uses in-memory filtering after fetch, since staleness depends on
        computed thresholds that vary by impact level.
        """
        open_risks = self.get_open_risks()
        stale = [r for r in open_risks if r.is_stale]
        logger.info(
            f"Found {len(stale)} stale risks out of {len(open_risks)} open risks"
        )
        return stale

    def get_risks_by_team(self, team_name: str, limit: Optional[int] = None) -> List[Risk]:
        """Fetch risks for a specific team.

        Args:
            team_name: Exact team name in GUS.
            limit: Optional row limit.
        """
        # Escape single quotes in team name for SOQL
        safe_name = team_name.replace("'", "\\'")
        query = self._build_query(
            where=f"WHERE Team__r.Name = '{safe_name}'",
            order_by="ORDER BY Status__c, Impact__c",
            limit=limit,
        )
        return self._execute_query(query)

    def get_risks_by_status(self, status: str, limit: Optional[int] = None) -> List[Risk]:
        """Fetch risks filtered by status.

        Args:
            status: One of 'Open', 'Closed', 'Escalated', 'Accepted'.
            limit: Optional row limit.
        """
        query = self._build_query(
            where=f"WHERE Status__c = '{status}' AND {MCE_TEAM_FILTER}",
            order_by="ORDER BY Impact__c, Last_Reviewed_Date__c ASC NULLS FIRST",
            limit=limit,
        )
        return self._execute_query(query)

    def get_accepted_risks(self, limit: Optional[int] = None) -> List[Risk]:
        """Fetch accepted risks (the 39 shown on dashboard)."""
        return self.get_risks_by_status("Accepted", limit=limit)

    def get_risk_by_id(self, risk_id: str) -> Optional[Risk]:
        """Fetch a single risk by its Salesforce ID."""
        query = self._build_query(
            where=f"WHERE Id = '{risk_id}'",
            limit=1,
        )
        results = self._execute_query(query)
        return results[0] if results else None

    # --- Private Methods ---

    def _build_query(
        self,
        where: str = "",
        order_by: str = "",
        limit: Optional[int] = None,
    ) -> str:
        """Build a SOQL query string."""
        fields = ", ".join(RISK_FIELDS)
        query = f"SELECT {fields} FROM PPM_Project_Risk__c"
        if where:
            query += f" {where}"
        if order_by:
            query += f" {order_by}"
        if limit:
            query += f" LIMIT {limit}"
        return query

    def _execute_query(self, query: str) -> List[Risk]:
        """Execute a SOQL query via sf CLI and return parsed Risk objects.

        Includes retry logic with exponential backoff.
        """
        logger.debug(f"Executing SOQL: {query[:200]}...")

        for attempt in range(1, self.max_retries + 1):
            try:
                result = subprocess.run(
                    [
                        "sf",
                        "data",
                        "query",
                        "--target-org",
                        self.target_org,
                        "-q",
                        query,
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout
                    logger.error(f"sf query failed (attempt {attempt}): {error_msg[:500]}")
                    if attempt < self.max_retries:
                        wait = 2**attempt
                        logger.info(f"Retrying in {wait}s...")
                        time.sleep(wait)
                        continue
                    raise GUSClientError(f"Query failed after {self.max_retries} attempts: {error_msg[:200]}")

                data = json.loads(result.stdout)
                records = data.get("result", {}).get("records", [])
                logger.info(f"Query returned {len(records)} records")

                # Parse records into Risk objects
                risks = []
                for record in records:
                    try:
                        risk = Risk.from_gus_record(record)
                        risks.append(risk)
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Failed to parse record {record.get('Id', '?')}: {e}")
                        continue

                return risks

            except subprocess.TimeoutExpired:
                logger.error(f"Query timed out (attempt {attempt})")
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise GUSClientError("Query timed out after all retries")

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                raise GUSClientError(f"Invalid JSON from sf CLI: {e}")

        return []  # Should not reach here

    def health_check(self) -> bool:
        """Verify we can connect to GUS and query risks.

        Returns True if connection is healthy.
        """
        try:
            result = subprocess.run(
                [
                    "sf",
                    "data",
                    "query",
                    "--target-org",
                    self.target_org,
                    "-q",
                    "SELECT Id FROM PPM_Project_Risk__c LIMIT 1",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
