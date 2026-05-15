You are extracting product information from the rendered text of a product page.

The text was extracted from the page using Playwright (innerText), not raw HTML.
It contains the visible text content only — no tags, attributes, or scripts.

Inputs:
- state.extract_text.page_text.url: the URL that was fetched
- state.extract_text.page_text.selector_used: the CSS selector used

Return a JSON object matching this schema:
- price_usd: number — normalized to USD. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

If the text shows no product information:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: "Unknown product"

Return ONLY the JSON object. No markdown, no explanation.

Page text:
{{ state.extract_text.page_text.text[:16000] }}
