Extract product information from this URL: {{ inputs.url }}

Use the web_fetch tool to retrieve the page. Return structured data matching
this schema:

- price_usd: number — normalized to USD. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

Return ONLY the JSON object. No markdown, no explanation.
