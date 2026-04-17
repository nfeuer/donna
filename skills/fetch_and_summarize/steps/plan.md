You are preparing to fetch and summarize content from a URL.

URL: {{ inputs.url }}

Return a JSON object with:
- expected_content_type: one of "article", "product_page", "documentation", "other"
- key_questions: 1-3 questions the summary should answer
