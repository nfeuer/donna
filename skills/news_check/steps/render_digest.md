You are rendering a digest DM summarizing new matching news items.

**Inputs available:**
- `state.classify_items.matches`: list of `{title, link, summary_short, matched_topics}`.
- `state.classify_items.total_scanned`: total items inspected.
- `state.feed.feed_title`: source feed title.
- `inputs.topics`: topic list.

**Your job:**
Return JSON matching the schema below. Keep the `message` under 1200 chars — if more than 5 matches, list the first 5 then append `"+<n> more."`. If zero matches, `triggers_alert=false` and `message=null`.

Be concise. Each line format: `• <title> — <link>`. No emojis.

Schema:
```
{
  "ok": true,
  "triggers_alert": bool,
  "message": string|null,
  "meta": {
    "item_count": int,
    "action_required_count": int,
    "source_feed": string
  }
}
```

Where `action_required_count` is the number of matched items (synonym for match count; keeps shape parity with email_triage).
