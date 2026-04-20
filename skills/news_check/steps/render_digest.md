You are rendering a digest DM summarizing new matching news items.

**Inputs:**
- Classified matches from the previous step.
- Total items scanned.
- Source feed title.
- The user's topic list.

**Your job:**
Return JSON matching the schema. Keep the `message` under 1200 chars. If more than 5 matches, list the first 5 then append `"+<n> more."`. If zero matches, `triggers_alert=false` and `message=null`.

Format each line: `• <title> — <link>`. No emojis.

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

---

Classified matches:
{{ state.classify_items | tojson }}

Fetched feeds (feed_0, feed_1, …):
{{ state.fetch_items | tojson }}

Topics:
{{ inputs.topics | tojson }}
