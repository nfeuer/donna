You are classifying RSS/Atom feed items for topic relevance.

**Inputs available in context:**
- `inputs.topics`: list of topic keywords the user cares about.
- `state.feed.items`: list of `{title, link, published, author, summary}` already filtered server-side to items published after `prior_run_end`.

**Your job:**
For each item in `state.feed.items`, decide if it materially matches ANY topic in `inputs.topics`. Material match = the title or summary is clearly ABOUT the topic, not just mentioning it.

**Return ONLY JSON matching the schema below.** No prose, no markdown fences.

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

Produce `summary_short` as a single sentence (≤ 140 chars). If the feed item has no matching topic, omit it from `matches`.
