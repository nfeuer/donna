You are rendering a digest DM for action-required emails.

**Inputs:**
- `confirmed`: emails confirmed as action-required (after body inspection).
- `total_scanned`: total snippets examined.
- Counters for candidates + bodies fetched.

**Your job:**
Return JSON matching the schema. Keep `message` under 1200 chars. If zero confirmed, `triggers_alert=false` and `message=null`. Format each line: `• "<subject>" from <sender> (<age>) — <reason>`. No emojis.

Schema:
```
{
  "ok": true,
  "triggers_alert": bool,
  "message": string|null,
  "meta": {
    "item_count": int,
    "action_required_count": int,
    "snippet_scanned_count": int,
    "body_fetched_count": int
  }
}
```

`item_count` = messages returned by Gmail search. `action_required_count` = len(confirmed). `snippet_scanned_count` = total_scanned from classify_snippets. `body_fetched_count` = number of body_<n> entries in state.fetch_bodies.

---

Confirmed items:
{{ state.classify_bodies.confirmed | tojson }}

Snippet-classification summary:
{{ state.classify_snippets | tojson }}

Fetched bodies:
{{ state.fetch_bodies | tojson }}
