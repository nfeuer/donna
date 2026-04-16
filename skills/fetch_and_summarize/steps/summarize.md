You have fetched a web page. Summarize its content.

URL: {{ inputs.url }}
Expected content type: {{ state.plan.expected_content_type }}
Key questions to answer: {{ state.plan.key_questions }}
HTTP status: {{ state.fetch.page.status_code }}

Page content (truncated to fit context):
{{ state.fetch.page.body[:4000] }}

Return a JSON object with:
- summary: 2-3 sentence summary
- answers: map of question -> one-sentence answer, one per key question
- confidence: 0.0-1.0
