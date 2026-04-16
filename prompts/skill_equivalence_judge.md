You are judging whether two skill outputs convey the same information.

Context (what this task represents):
{context}

Output A:
{output_a}

Output B:
{output_b}

Respond with strict JSON matching this shape:
{"agreement": <float 0.0-1.0>, "rationale": <short string>}

Where 1.0 means "the two outputs convey the same information completely" and 0.0 means "they disagree materially." 0.5 means "partial overlap." Be strict about factual equivalence but forgiving about wording and formatting.
