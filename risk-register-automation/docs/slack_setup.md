# Slack App Configuration Guide

## Overview

The Risk Register Automation bot sends notifications via the Slack API. This guide covers setup, permissions, and deployment.

## Prerequisites

- Slack workspace admin access (or ability to request app approval)
- Python 3.9+ with `requests` library installed
- (Optional) Flask for interactive button handlers

## Step 1: Create a Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"** → **"From scratch"**
3. Name: `Risk Register Automation`
4. Workspace: Select your Salesforce workspace
5. Click **"Create App"**

## Step 2: Configure Bot Permissions (OAuth Scopes)

Navigate to **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** and add:

| Scope | Purpose |
|-------|---------|
| `chat:write` | Send messages to channels and DMs |
| `users:read.email` | Look up users by email for DMs |
| `users:read` | Read user profile info |
| `im:write` | Open DM conversations |

## Step 3: Install App to Workspace

1. Go to **OAuth & Permissions**
2. Click **"Install to Workspace"**
3. Authorize the requested permissions
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

## Step 4: Set Environment Variables

```bash
# Required for live mode
export SLACK_BOT_TOKEN="xoxb-your-token-here"

# Optional: Incoming webhook for channel posts
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T.../B.../..."

# Control dry-run mode
export DRY_RUN="false"  # Set to "true" for testing
```

## Step 5: Test Connectivity

```bash
# Dry-run test (no token needed)
python3 scripts/demo_slack.py

# Live test (sends real DM to you)
SLACK_BOT_TOKEN=xoxb-... DRY_RUN=false python3 scripts/demo_slack.py
```

## Step 6: Configure Interactive Components (Optional)

If using the "Mark as Reviewed" / "Escalate" / "Dismiss" buttons:

1. Go to **Interactivity & Shortcuts** in app settings
2. Toggle **Interactivity** to **On**
3. Set **Request URL** to your server endpoint:
   ```
   https://your-server.salesforce.com/slack/interactions
   ```
4. Click **Save Changes**

### Running the Interaction Server

```bash
# Install Flask
pip install flask

# Start the handler server
python3 -c "
from src.interactive_handlers import InteractiveHandler, create_interaction_server
from src.notification_history import NotificationHistory
from src.gus_client import GUSClient

handler = InteractiveHandler(
    gus_client=GUSClient(),
    history=NotificationHistory(),
)
app = create_interaction_server(handler, port=3000)
app.run(host='0.0.0.0', port=3000)
"
```

## Safety Features

### Target User Safety Lock

The `SlackClient` has a `target_user` parameter that redirects ALL messages to a single recipient during development:

```python
slack = SlackClient(
    bot_token="xoxb-...",
    dry_run=False,
    target_user="alyssa.cheng@salesforce.com",  # All DMs go here
)
```

**Remove `target_user` only when you're ready to send to real recipients.**

### Dry-Run Mode

When `dry_run=True`:
- No Slack API calls are made
- Messages are printed to stdout
- User lookups return fake IDs
- Full delivery log is still tracked

### Rate Limiting

The client automatically:
- Waits 3 seconds between API calls (Tier 2 compliance)
- Retries on 429 responses with the `Retry-After` header
- Caps at 3 retries before failing gracefully

## Channel Setup

### Recommended Channels

| Channel | Purpose | Frequency |
|---------|---------|-----------|
| `#mce-risk-alerts` | Weekly summary reports | Weekly (Monday 9 AM) |
| `#mce-risk-ops` | Bot operational logs | As needed |

### Invite the Bot

After installing, invite the bot to channels:
```
/invite @Risk Register Automation
```

## Troubleshooting

### "not_in_channel" Error
The bot needs to be invited to the channel before posting. Use `/invite`.

### "users_not_found" Error
The email address doesn't match any Slack user. Check:
- Is the user deactivated?
- Is the email their primary Slack email?

### Rate Limited (429)
The bot will auto-retry. If persistent:
- Reduce `rate_limit_delay` in config
- Check if other integrations share the same token

### "invalid_auth" Error
The bot token is expired or revoked. Reinstall the app and get a new token.

## Architecture Diagram

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│  GUS SOQL API   │────▶│  Risk Analyzer   │────▶│  Notif.     │
│  (sf data query)│     │  (staleness +    │     │  Builder    │
└─────────────────┘     │   categories)    │     │  (Block Kit)│
                        └──────────────────┘     └──────┬──────┘
                                                        │
                        ┌──────────────────┐            ▼
                        │  Notification    │     ┌─────────────┐
                        │  History (dedup) │◀───▶│  Delivery   │
                        └──────────────────┘     │  Engine     │
                                                 └──────┬──────┘
                                                        │
                                                        ▼
                                                 ┌─────────────┐
                                                 │  Slack API  │
                                                 │  (DMs +     │
                                                 │   Channels) │
                                                 └──────┬──────┘
                                                        │
                                                        ▼
                                                 ┌─────────────┐
                                                 │  Interactive│
                                                 │  Handler    │
                                                 │  (buttons)  │
                                                 └─────────────┘
```

## Security Notes

- **Never commit** the bot token to version control
- Use environment variables or a secrets manager
- The `target_user` safety lock prevents accidental spam to the org
- All actions are logged in the delivery log for auditability
