Extract product information from a product page. A local model scanned the page
and found these snippets:

- Title: {{ state.triage_for_claude.title_snippet or "not found" }}
- Price: {{ state.triage_for_claude.price_snippet or "not found" }}
- Stock status: {{ state.triage_for_claude.stock_snippet or "not found" }}
- Sizes: {{ state.triage_for_claude.sizes_snippet or "not found" }}

URL: {{ inputs.url }}

Using the snippets above, return a JSON object:
- price_usd: number — normalized to USD. Convert if non-USD currency. Null if unknown.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name.

Return ONLY the JSON object. No markdown, no explanation.
