You are computing the final output fields for a product_watch skill run.

Inputs (user-provided):
- inputs.url: the product URL that was monitored.
- inputs.max_price_usd: the maximum price (USD) above which NO alert should
  fire. Null = any price qualifies.
- inputs.required_size: the size the user wants. Null = any in-stock size
  qualifies.

Extracted info (from whichever tier succeeded):
{% if state.try_local_extract.success is defined and state.try_local_extract.success %}
- Tier: 1 (local text extraction)
- Extraction: state.try_local_extract
{% elif state.try_vision_extract is defined and state.try_vision_extract.success %}
- Tier: 2 (local vision extraction)
- Extraction: state.try_vision_extract
{% else %}
- Tier: 3 (Claude fallback)
- Extraction: state.claude_fallback
{% endif %}

{% set extraction = state.try_local_extract if (state.try_local_extract.success is defined and state.try_local_extract.success) else (state.try_vision_extract if (state.try_vision_extract is defined and state.try_vision_extract.success) else state.claude_fallback) %}

Compute the final output:
- ok: true
- price_usd: {{ extraction.price_usd }}
- currency: {{ extraction.currency }}
- in_stock: {{ extraction.in_stock }}
- size_available: true if inputs.required_size is null OR
                  inputs.required_size IN extraction.available_sizes.
                  Else false.
- triggers_alert: true if ALL of (in_stock, size_available,
                  (inputs.max_price_usd is null OR price_usd <= inputs.max_price_usd)).
                  Else false.
- title: {{ extraction.title }}
- tier: "tier_1_text" or "tier_2_vision" or "tier_3_claude" (whichever succeeded)

Return ONLY the JSON object.

Inputs: {{ inputs | tojson }}
Extraction data: {{ extraction | tojson }}
