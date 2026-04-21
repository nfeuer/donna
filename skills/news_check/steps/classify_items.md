You are classifying RSS/Atom feed items for topic relevance.

**Inputs available:**
- User topics — things the user cares about.
- Feed items — already filtered server-side to items published after `prior_run_end`. Each has `{title, link, published, author, summary}`.

**Your job:**
For each feed item, decide if it materially matches ANY of the user's topics. Material match = the title or summary is clearly ABOUT the topic, not just mentioning it in passing.

**Return ONLY JSON matching the schema below.** No prose, no markdown fences. Produce `summary_short` as a single sentence (≤ 140 chars). If an item matches no topic, omit it from `matches`.

Schema:
```
{
  "matches": [
    {"title": str, "link": str, "published": str|null, "summary_short": str, "matched_topics": [str]}
  ],
  "total_scanned": int,
  "total_matched": int
}
```

---

Topics:
{{ inputs.topics | tojson }}

Feeds (all fetched feeds — each key is feed_0, feed_1, …):
{{ state.fetch_items | tojson }}
