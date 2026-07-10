# Google Calendar OAuth

How Donna authenticates to Google Calendar, why the integration has died
recurrently with `invalid_grant`, and how to fix it permanently.
Spec: `spec_v3.md` §3.2.2 (Calendar direct API), §12.1 (integration status).

## How auth works

- `config/google_credentials.json` — the OAuth **client** ("installed" app type).
- `config/token.json` — the authorized-user token: short-lived access token
  plus a long-lived **refresh token**.
- On boot, `GoogleCalendarClient.authenticate()` loads `token.json`, refreshes
  the access token if expired, and **writes the refreshed token back** to the
  same path. In Docker the config dir is mounted read-write, so persistence
  works — as long as the refresh token itself is still valid.

## Why it keeps breaking: `invalid_grant`

A refresh token is rejected with `invalid_grant` when Google has expired or
revoked it. The overwhelmingly common cause for this project's setup:

**The OAuth consent screen is in "Testing" publishing status.**
Google expires refresh tokens for Testing-status apps **7 days after
issuance**, unconditionally. Every re-link buys one week of calendar access.

Other causes to rule out if it recurs after the fix: a Google account
password change or security event (revokes grants), scope changes between
link and use, and exceeding 50 outstanding refresh tokens per client.

## The permanent fix (one-time, Google Cloud Console)

1. Open [Google Cloud Console](https://console.cloud.google.com/) → the
   project that owns the client ID in `config/google_credentials.json`.
2. **APIs & Services → OAuth consent screen** (Audience page in the new UI).
3. Publishing status will read **Testing**. Click **Publish app**.
   - For scopes like Calendar, Google shows an "unverified app" warning
     during consent. That is fine for a personal single-user app — click
     *Advanced → Go to app (unsafe)* during the relink. No verification
     review is needed; tokens from a Production-status app do not expire on
     the 7-day timer.
4. Re-link (below). Tokens issued **before** publishing keep their old
   expiry, so one final relink is required.

The same consent screen also backs the Gmail integration — publishing fixes
both.

## Re-linking

On a machine with a browser (not inside Docker):

```bash
cd /mnt/donna/donna
.venv/bin/python -m donna.integrations.calendar config/
```

This opens the consent flow and writes `config/token.json`. Then sync it to
the deployment and restart the orchestrator:

```bash
cp config/token.json /mnt/donna/deploy-main/config/token.json
docker restart donna-orchestrator
```

Confirm with `docker logs donna-orchestrator | grep calendar_client` — you
want `calendar_client_authenticated`, not `calendar_client_unavailable`.

## How failures surface now

- `GoogleCalendarClient.authenticate()` raises a typed `CalendarAuthError`
  with the remediation in the message (`reason="refresh_rejected"` or
  `"token_missing_headless"`).
- The wiring factory logs `calendar_client_unavailable` with
  `event_type="fallback_activated"` and dispatches a Discord fallback alert
  via `NotificationService.dispatch_fallback_alert` — a dead calendar is no
  longer silent.
- Boot diagnostics (`SelfDiagnostic`) live-probe the refresh token when
  `calendar.yaml` is configured and post any failure to the debug channel;
  `donna health` runs the same probe on demand.
