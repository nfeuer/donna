You are extracting product information from a screenshot of a product page.

A full-page screenshot has been captured. Analyze the visual content to
extract product details.

Screenshot path: {{ state.screenshot_fallback.screenshot.file_path }}
Page URL: {{ inputs.url }}

Return a JSON object matching this schema:
- price_usd: number — normalized to USD. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

If the screenshot shows no product information:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: "Unknown product"

Return ONLY the JSON object. No markdown, no explanation.
