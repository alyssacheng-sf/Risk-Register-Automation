# MCE Risk Register Automation

Automated detection and notification system for stale project risks in the MCE (Marketing Cloud Engagement) org. Pulls risk data from GUS, identifies overdue reviews, and sends actionable Slack notifications to risk owners on a scheduled basis.

📄 **[Full System Overview (Google Doc)](https://docs.google.com/document/d/1BxdRjJU8q77sS5tWMhFYX8sA5_AUNixIysvmzNGHLFw/edit?tab=t.0)**

## What It Does

- Queries GUS for all open MCE project risks (`PPM_Project_Risk__c`)
- Detects stale risks based on configurable SLA thresholds (High=7 days, Medium=14 days, Low=30 days)
- Scores and prioritizes risks (impact × probability → 1-9 scale)
- Categorizes risks by keyword (security, performance, reliability, etc.)
- Groups stale risks by owner and sends consolidated Slack notifications
- Deduplicates notifications — owners won't be pinged twice in the same day
- Escalates to managers after 3+ days of unresolved stale risks
- Generates weekly summary reports with compliance metrics and burndown trends
- Respects quiet hours (no notifications outside 8 AM – 6 PM)

## Prerequisites

- **Python 3.9+**
- **Salesforce CLI** (`sf`) authenticated to GUS:
  ```bash
  sf org login web --alias gus --instance-url https://gus.lightning.force.com
  ```
- **Slack Bot Token** with `chat:write` and `users:read.email` scopes

## Setup

```bash
git clone https://github.com/sfdc-mc-mj/Risk-Register-Automation.git
cd Risk-Register-Automation

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your SLACK_BOT_TOKEN
```

## Running

### Daily Risk Check

Fetches all open risks, identifies stale ones, and sends owner notifications:

```bash
python3 jobs/daily_risk_check.py
```

### Weekly Summary

Generates a full report with metrics, burndown, and team breakdown:

```bash
python3 jobs/weekly_summary.py
```

### Dry Run Mode (default)

By default, jobs run in dry-run mode (logs messages but doesn't send to Slack):

```bash
# Live mode — actually sends Slack messages
DRY_RUN=false SLACK_BOT_TOKEN=xoxb-... python3 jobs/daily_risk_check.py
```

### Run Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=src --cov-report=term-missing
```

## Scheduling

### Current: Local Cron Job

The system runs via a cron job with automated GUS authentication:

```bash
# Start (every weekday at 8 AM)
echo "0 8 * * 1-5 /path/to/scripts/cron_daily.sh >> logs/cron.log 2>&1" | crontab -

# Stop
crontab -r

# Check status
crontab -l
```

The cron wrapper (`scripts/cron_daily.sh`) handles:
- Setting up PATH and environment variables
- Bypassing macOS Keychain restrictions (`SF_USE_GENERIC_UNIX_KEYCHAIN=true`)
- Re-authenticating to GUS via saved sfdx auth URL (refresh token auto-renews)

### Upcoming: GitHub Actions

GitHub Actions workflow files are committed and ready:
- `.github/workflows/daily_risk_check.yml` — 8 AM PST Mon-Fri
- `.github/workflows/weekly_summary.yml` — 9 AM PST Mondays

**Status:** Pending org admin action. The `sfdc-mc-mj` GitHub organization has an IP allow list that blocks GitHub's hosted runners. Once an admin enables "Allow GitHub Actions to bypass the IP allow list," the automation will run fully in the cloud with no local dependency.

## Project Structure

```
src/
  models/risk.py            # Risk dataclass with computed properties
  gus_client.py             # GUS SOQL API wrapper (sf CLI subprocess)
  risk_analyzer.py          # Staleness detection, scoring, categorization
  notification_builder.py   # Slack Block Kit message templates
  notification_delivery.py  # Orchestrates alerts with dedup & quiet hours
  notification_history.py   # JSON-based deduplication tracking
  slack_client.py           # Slack API client with rate limiting & safety redirect
  data_store.py             # Daily JSON snapshots for week-over-week metrics
  metrics_calculator.py     # Weekly metrics, burndown, velocity
  report_generator.py       # Rich weekly summary with ASCII burndown chart
  owner_mapper.py           # Email → Slack ID resolution with caching

jobs/
  daily_risk_check.py       # Main daily job: fetch → analyze → notify
  weekly_summary.py         # Weekly job: fetch → snapshot → metrics → report

config/
  thresholds.yaml           # Staleness SLA thresholds (configurable)
  categories.yaml           # Risk keyword categories

scripts/
  cron_daily.sh             # Cron wrapper with env setup & GUS auth

.github/workflows/
  daily_risk_check.yml      # GitHub Actions scheduled workflow (daily)
  weekly_summary.yml        # GitHub Actions scheduled workflow (weekly)

tests/                      # 150 tests, 83% coverage
```

## Safety Features

| Feature | Description |
|---------|-------------|
| Target User Redirect | All notifications route to one person during testing |
| Dry Run Mode | Simulates full pipeline without sending Slack messages |
| Deduplication | Same risk + same owner won't be notified twice per day |
| Quiet Hours | No notifications outside 8 AM – 6 PM |
| Rate Limiting | 3-second delay between Slack messages |
| Escalation Gate | Won't escalate until owner warned on 3+ separate days |

## Key Numbers (as of June 2026)

| Metric | Value |
|--------|-------|
| Open MCE risks | 92 |
| Stale (past SLA) | 92 (100%) |
| Review compliance | 0% |
| Teams affected | 28 |
| Unique owners | 11 |
| Oldest unreviewed | ~3,025 days |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes (live mode) | Slack bot token |
| `DRY_RUN` | No | `"true"` (default) or `"false"` |
| `TARGET_USER` | No | Safety redirect email (e.g., `you@salesforce.com`) |
| `SUMMARY_CHANNEL` | No | Slack channel ID for weekly summaries |
| `OPS_CHANNEL` | No | Slack channel ID for ops alerts |
