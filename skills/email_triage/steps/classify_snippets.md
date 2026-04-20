You are classifying Gmail messages by action-required likelihood from snippets only.

**Inputs:**
- `senders`: allow-list of sender addresses the user explicitly asked to be watched.
- `messages`: Gmail message summaries returned by search, each with `{id, sender, subject, snippet, internal_date}`.

**Your job:**
For each message, decide from the SNIPPET alone if the user should probably reply. Signals for action-required:
- Direct question to the user ("can you...", "what do you think about...")
- Explicit ask or request ("please review", "need your approval")
- Stated deadline ("by Friday", "before EOW")
- Imperative verb in subject ("Review:", "Action needed:")

Non-action-required: automated receipts, newsletters, calendar RSVPs, pure FYI notifications.

Return ONLY JSON matching the schema. `candidates` is the subset needing body inspection (snippet_confidence >= 0.6).

Schema:
```
{
  "candidates": [
    {"id": str, "sender": str, "subject": str, "snippet": str, "internal_date": str|null, "snippet_confidence": float}
  ],
  "total_scanned": int
}
```

---

Senders:
{{ inputs.senders | tojson }}

Messages:
{{ state.search_messages.search | tojson }}
