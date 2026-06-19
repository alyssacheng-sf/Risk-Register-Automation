#!/bin/bash
# Sets up a cron job that runs the daily risk check every 2 minutes (for demo purposes).
# Remove after recording with: crontab -r

PROJECT_DIR="/Users/alyssa.cheng/risk-register-automation"
PYTHON="/usr/bin/python3"
LOG_FILE="$PROJECT_DIR/logs/cron_demo.log"

mkdir -p "$PROJECT_DIR/logs"

# Write the cron entry: every 2 minutes
(crontab -l 2>/dev/null; echo "*/2 * * * * cd $PROJECT_DIR && $PYTHON jobs/daily_risk_check.py >> $LOG_FILE 2>&1") | crontab -

echo "Demo cron job installed! It will run every 2 minutes."
echo "Monitor output:  tail -f $LOG_FILE"
echo ""
echo "To remove after demo:  crontab -r"
echo "Or to view:  crontab -l"
