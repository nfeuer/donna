#!/usr/bin/env bash
# Donna External Watchdog
# Runs OUTSIDE Docker (cron or systemd timer) every 5 minutes.
# Checks donna-orchestrator health. Alerts via Discord webhook or Twilio SMS.
# See docs/resilience.md Layer 2.
#
# Install:
#   crontab -e
#   */5 * * * * /path/to/donna/scripts/watchdog.sh >> /var/log/donna-watchdog.log 2>&1

set -euo pipefail

# Configuration — override via environment or edit here
CONTAINER_NAME="${DONNA_CONTAINER_NAME:-donna-orchestrator}"
DISCORD_WEBHOOK_URL="${DONNA_WATCHDOG_DISCORD_WEBHOOK:-}"
TWILIO_ACCOUNT_SID="${TWILIO_ACCOUNT_SID:-}"
TWILIO_AUTH_TOKEN="${TWILIO_AUTH_TOKEN:-}"
TWILIO_FROM="${TWILIO_PHONE_NUMBER:-}"
TWILIO_TO="${DONNA_ALERT_PHONE:-}"
HEALTH_URL="${DONNA_HEALTH_URL:-http://localhost:8100/health}"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Check if container is running and healthy
HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "not_found")

if [ "$HEALTH_STATUS" = "healthy" ]; then
    # All good, exit silently
    exit 0
fi

# Container is unhealthy, stopped, or not found
MESSAGE="⚠️ Donna Watchdog Alert [$TIMESTAMP]: Container '$CONTAINER_NAME' status is '$HEALTH_STATUS'. Manual investigation may be required."

echo "$MESSAGE"

# Alert via Discord webhook
if [ -n "$DISCORD_WEBHOOK_URL" ]; then
    curl -s -X POST "$DISCORD_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{\"content\": \"$MESSAGE\"}" \
        || echo "Failed to send Discord alert"
fi

# Alert via Twilio SMS
if [ -n "$TWILIO_ACCOUNT_SID" ] && [ -n "$TWILIO_TO" ]; then
    curl -s -X POST "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/Messages.json" \
        -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
        --data-urlencode "From=$TWILIO_FROM" \
        --data-urlencode "To=$TWILIO_TO" \
        --data-urlencode "Body=$MESSAGE" \
        || echo "Failed to send SMS alert"
fi
