#!/bin/bash
# Cron wrapper for daily risk check
export HOME="/Users/alyssa.cheng"
export PATH="/Users/alyssa.cheng/.aisuite/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export NODE_EXTRA_CA_CERTS="/Users/alyssa.cheng/.aisuite/conf/npm-sfdc-certs.pem"
# Load secrets from .env file (not committed to git)
source /Users/alyssa.cheng/risk-register-automation/.env
export DRY_RUN="false"
export TARGET_USER="alyssa.cheng@salesforce.com"

# Disable Keychain — store auth in plain file (cron can't access Keychain)
export SF_USE_GENERIC_UNIX_KEYCHAIN=true

cd /Users/alyssa.cheng/risk-register-automation

# Re-authenticate to GUS using saved auth URL
sf org login sfdx-url --sfdx-url-file .auth_url --alias gus --set-default 2>/dev/null

/usr/bin/python3 jobs/daily_risk_check.py
