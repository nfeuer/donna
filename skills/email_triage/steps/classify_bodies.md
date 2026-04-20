You are confirming or rejecting candidate action-required emails using their full bodies.

**Inputs:**
- `candidates`: the shortlist from the snippet pass.
- Each body (if fetched) is at `state.fetch_bodies.body_<index>` — match candidates by list-index.
- If no candidates were shortlisted, no bodies were fetched; just emit an empty confirmed list with `body_fetched=false`.

**Your job:**
For each candidate, look at the body (if available) and decide whether it genuinely requires a reply. Produce a 1-line `reason` for each confirmed item (e.g. "asks for budget approval by Fri"). Produce a human-readable `age_human` string from `internal_date`.

Return ONLY JSON matching the schema.

Schema:
```
{
  "confirmed": [
    {"id": str, "sender": str, "subject": str, "reason": str, "age_human": str}
  ],
  "rejected_ids": [str],
  "body_fetched": bool
}
```

`age_human` examples: `"2h ago"`, `"yesterday"`, `"3 days ago"`.

---

Candidates:
{{ state.classify_snippets.candidates | tojson }}

Fetched bodies:
{{ state.fetch_bodies | tojson }}
