You are preparing a compact summary of a product page for a more capable model.
The previous extraction attempt failed. Your job is to scan the page text and
pull out ONLY the product-relevant snippets so the next model gets minimal context.

Look for these specific data points:
- Product title/name
- Price (any currency)
- Stock status ("in stock", "sold out", "add to cart", "out of stock", etc.)
- Available sizes (look for size selectors, dropdowns, size charts)

For each data point you find, extract the exact text snippet (a few words of
surrounding context). For data points you cannot find, say "not found".

URL: {{ state.extract_text.page_text.url }}

Page text (truncated):
{{ state.extract_text.page_text.text[:16000] }}
