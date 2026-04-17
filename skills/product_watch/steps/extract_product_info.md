You are extracting product information from the HTML of a product page.

Inputs you can use:
- state.fetch_page.body: the HTML of the page
- state.fetch_page.status: HTTP status code

Return a JSON object matching this schema:
- price_usd: number — normalized to USD. If the page shows a non-USD price,
  convert approximately. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase in any size.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

If the page is a 404 or otherwise shows no product:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: best-effort name or "Unknown product"

Return ONLY the JSON object. No markdown, no explanation.

State so far:
{{ state | tojson }}
