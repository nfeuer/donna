You are computing the final output fields for a product_watch skill run.

Inputs (user-provided):
- inputs.url: the product URL that was monitored.
- inputs.max_price_usd: the maximum price (USD) above which NO alert should
  fire. Null = any price qualifies.
- inputs.required_size: the size the user wants. Null = any in-stock size
  qualifies.

Extracted info (from whichever tier succeeded):
{% if state.try_local_extract is defined and state.try_local_extract.success %}
- Tier: 1 (local text extraction)
- Extraction: state.try_local_extract
{% elif state.try_vision_extract is defined and state.try_vision_extract.success %}
- Tier: 2 (local vision extraction)
- Extraction: state.try_vision_extract
{% elif state.claude_with_triage is defined and state.claude_with_triage.success %}
- Tier: 3 (Claude with local triage)
- Extraction: state.claude_with_triage
{% elif state.claude_fallback is defined and state.claude_fallback.success %}
- Tier: 4 (Claude direct fallback)
- Extraction: state.claude_fallback
{% else %}
- No tier succeeded — all extraction attempts failed.
{% endif %}

{% set extraction = state.try_local_extract if (state.try_local_extract is defined and state.try_local_extract.success) else (state.try_vision_extract if (state.try_vision_extract is defined and state.try_vision_extract.success) else (state.claude_with_triage if (state.claude_with_triage is defined and state.claude_with_triage.success) else (state.claude_fallback if (state.claude_fallback is defined and state.claude_fallback.success) else {}))) %}

Compute the final output:
- ok: true
- price_usd: {{ extraction.price_usd | default(none) }}
- currency: {{ extraction.currency | default("USD") }}
- in_stock: {{ extraction.in_stock | default(false) }}
- size_available: true if inputs.required_size is null OR
                  inputs.required_size IN extraction.available_sizes.
                  Else false.
- triggers_alert: true if ALL of (in_stock, size_available,
                  (inputs.max_price_usd is null OR price_usd <= inputs.max_price_usd)).
                  Else false.
- title: {{ extraction.title | default("Unknown") }}

Return ONLY the JSON object.

Inputs: {{ inputs | tojson }}
Extraction data: {{ extraction | tojson }}
