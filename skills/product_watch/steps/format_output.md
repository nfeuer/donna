You are computing the final output fields for a product_watch skill run.

Inputs (user-provided):
- inputs.url: the product URL that was monitored.
- inputs.max_price_usd: the maximum price (USD) above which NO alert should
  fire. Null = any price qualifies.
- inputs.required_size: the size the user wants. Null = any in-stock size
  qualifies.

Extracted info (from the previous step):
- state.extract_product_info.price_usd
- state.extract_product_info.currency
- state.extract_product_info.in_stock
- state.extract_product_info.available_sizes
- state.extract_product_info.title

Compute the final output:
- ok: true
- price_usd: state.extract_product_info.price_usd
- currency: state.extract_product_info.currency
- in_stock: state.extract_product_info.in_stock
- size_available: true if inputs.required_size is null OR
                  inputs.required_size IN state.extract_product_info.available_sizes.
                  Else false.
- triggers_alert: true if ALL of (in_stock, size_available,
                  (inputs.max_price_usd is null OR price_usd <= inputs.max_price_usd)).
                  Else false.
- title: state.extract_product_info.title

Return ONLY the JSON object.

Inputs: {{ inputs | tojson }}
Extracted info: {{ state.extract_product_info | tojson }}
