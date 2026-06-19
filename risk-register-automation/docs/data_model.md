# Data Model Documentation

## Source: GUS `PPM_Project_Risk__c`

The risk data lives in the GUS Salesforce org as the `PPM_Project_Risk__c` custom object.
The dashboard at `01ZEE000001Bgkv2AC` (FY26 Engagement Risk Register) visualizes this data.

## Field Mapping

| GUS API Field | Python Attribute | Type | Notes |
|---|---|---|---|
| `Id` | `id` | string | Salesforce 18-char ID |
| `Name` | `name` | string | "Project Risk Name" - the risk title |
| `Project_Risk_Number__c` | `project_risk_number` | string | Human-readable number |
| `Status__c` | `status` | picklist | Open, Closed, Escalated, Accepted |
| `Impact__c` | `impact` | picklist | High, Medium, Low |
| `Probability__c` | `probability` | picklist | High, Medium, Low |
| `Escalation_Level__c` | `escalation_level` | picklist | Program, Project, S2 |
| `Identified_On__c` | `identified_on` | date | When risk was first identified |
| `Last_Reviewed_Date__c` | `last_reviewed_date` | date | Last manual review - KEY for staleness |
| `Target_Close_on__c` | `target_close_on` | date | Expected closure date |
| `Closed_On__c` | `closed_on` | date | Actual closure date |
| `CreatedDate` | `created_date` | datetime | Record creation timestamp |
| `LastModifiedDate` | `last_modified_date` | datetime | Any field change |
| `OwnerId` / `Owner.Name` / `Owner.Email` | `owner_*` | reference | GUS record owner |
| `Risk_Owner__c` / `Risk_Owner__r.Name` | `risk_owner_*` | reference | Actual risk owner (may differ from record owner) |
| `Team__c` / `Team__r.Name` | `team_*` | reference | Owning team |
| `Details__c` | `details` | textarea | Risk description |
| `Impact_Description__c` | `impact_description` | textarea | What happens if risk materializes |
| `Mitigation_Strategy__c` | `mitigation_strategy` | textarea | How to reduce risk |
| `Closure_Criteria__c` | `closure_criteria` | textarea | What "done" looks like |
| `Feature_Flag_Kill_Switch__c` | `feature_flag_kill_switch` | boolean | Has a kill switch |

## Key Observations

1. **Two owner fields**: `Owner` is the Salesforce record owner; `Risk_Owner__c` is the
   person actually responsible. Notifications should go to Risk_Owner first, fall back to Owner.

2. **Last_Reviewed_Date__c is often NULL**: Many risks have never been reviewed.
   The staleness calculation falls back to `Identified_On__c`, then `CreatedDate`.

3. **Status values**: The dashboard shows:
   - Open Risks - High Impact: status=Open, impact=High
   - Open Risks - Need Review: status=Open, is_stale=True (computed)
   - Accepted Risks: status=Accepted (39 of them — these are acknowledged, not actioned)
   - Closed Risks: status=Closed

4. **92 open risks** across 57 MCE teams as of June 2026. ALL are currently stale
   (0 have been reviewed within threshold).

## Computed Properties

| Property | Logic | Use Case |
|---|---|---|
| `days_since_review` | days since `last_reviewed_date` (or `identified_on`, or `created_date`) | Staleness detection |
| `is_stale` | `days_since_review > threshold` | Dashboard "Need Review" |
| `stale_threshold_days` | High=7, Medium=14, Low=30, default=14 | Configurable in `config/thresholds.yaml` |
| `risk_score` | impact(H=3,M=2,L=1) × probability(H=3,M=2,L=1) → 1-9 | Notification priority |
| `is_open` | status in (Open, Escalated, None) | Filter active risks |
| `gus_url` | Constructed from Id | Link in notifications |

## MCE Team Filter

Risks are scoped to MCE teams using LIKE patterns:
- `%MC %` (space after MC to avoid false positives)
- `%E360%`
- `%Engagement%`
- `%SFMC%`
- `%JB%`

This captures 57 teams and 397 total risk records (92 open, 45 accepted, 259 closed, 2 escalated).
