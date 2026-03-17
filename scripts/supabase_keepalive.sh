#!/usr/bin/env bash
# Supabase Keep-Alive
# Ping Supabase every 3 days to prevent free tier inactivity pause.
# See docs/architecture.md Section 16.2.
#
# Install:
#   crontab -e
#   0 8 */3 * * /path/to/donna/scripts/supabase_keepalive.sh >> /var/log/donna-keepalive.log 2>&1

set -euo pipefail

SUPABASE_URL="${SUPABASE_URL:-}"
SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY:-}"

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_ANON_KEY" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be set"
    exit 1
fi

RESPONSE=$(curl -s -w "%{http_code}" -o /dev/null \
    "$SUPABASE_URL/rest/v1/" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $SUPABASE_ANON_KEY")

if [ "$RESPONSE" = "200" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) INFO: Supabase keep-alive ping successful"
else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARNING: Supabase keep-alive ping returned HTTP $RESPONSE"
fi
