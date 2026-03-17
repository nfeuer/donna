# Slice 8: Email Integration & Correction Logging

> **Goal:** Connect Gmail for email monitoring (read + draft), add email as a notification channel (Tier 3 escalation), and implement correction logging so user overrides start accumulating data for preference learning.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/integrations.md` — Gmail API access levels, direct API pattern
- `docs/notifications.md` — Email notification types, end-of-day digest
- `docs/preferences.md` — Correction logging schema

## What to Build

1. **Implement Gmail client** (`src/donna/integrations/gmail.py`):
   - Read-only access: search and read emails
   - Draft creation: compose drafts (never send without explicit user approval)
   - Send scope behind feature flag (disabled by default)
   - OAuth2 with restricted scopes

2. **Implement email forwarding parser:**
   - Monitor a configured email alias for forwarded messages
   - Parse forwarded emails for task content
   - Create tasks from email content (same pipeline as Discord/SMS)

3. **Wire email into notification service:**
   - Morning digest via email (6:30 AM, same content as Discord but formatted for email)
   - End-of-day digest via email (5:30 PM weekdays)
   - Escalation Tier 3: email with "ACTION REQUIRED" subject

4. **Implement correction logging** (`src/donna/preferences/correction_logger.py`):
   - When user changes a task field (domain, priority, scheduled time, etc.) → log to `correction_log` table
   - Capture: original value, corrected value, input text, field corrected
   - Calendar sync time changes are logged as implicit corrections (from Slice 4)
   - Discord commands: "change priority to 4", "move to work domain" → detect field change and log

5. **Write tests:**
   - Unit test: email parser extracts task content from forwarded email
   - Unit test: correction logger records field changes with correct before/after values
   - Unit test: email notification respects blackout and quiet hours
   - Integration test: mock Gmail API, verify draft creation

## Acceptance Criteria

- [ ] Gmail client authenticates with restricted OAuth2 scopes
- [ ] `search_emails()` returns matching emails
- [ ] `create_draft()` creates a Gmail draft (never sends)
- [ ] Forwarded emails parsed and created as tasks
- [ ] Morning digest sent via email at 6:30 AM
- [ ] End-of-day digest sent via email at 5:30 PM weekdays
- [ ] Email is Tier 3 in escalation chain
- [ ] User field changes logged to `correction_log` with original and corrected values
- [ ] Calendar reschedules logged as implicit corrections
- [ ] Send scope is disabled by default, requires config flag to enable

## Not in Scope

- No rule extraction from corrections (that's Phase 3)
- No preference engine post-processing
- No Communication/Drafting Agent

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/integrations.md`, `docs/notifications.md`, `docs/preferences.md`
