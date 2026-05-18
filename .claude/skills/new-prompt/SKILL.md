---
name: new-prompt
description: Scaffold a new prompt template + output schema + model routing entry
disable-model-invocation: true
---

# New Prompt

Create a new LLM prompt template with its paired output schema and model routing entry. Every prompt in Donna follows a three-file pattern.

## Workflow

1. **Ask the user for:**
   - Task type name (snake_case, e.g. `summarize_thread`)
   - One-line description
   - Which model alias to use: `parser` (Sonnet, cheap), `reasoner` (Sonnet, complex), `local_parser` (Ollama, free), `local_vision` (Ollama vision)
   - What tools the model may call (or none)
   - Whether it uses Jinja2 variables (`.md.j2`) or plain markdown (`.md`)
   - Expected output fields

2. **Create three files:**

### A. Prompt template: `prompts/<task_type>.md` (or `.md.j2`)

```markdown
# <Title> Prompt

<Role and instructions>

## Output Schema

Respond with a JSON object containing exactly these fields:

\```json
{
  "field_name": "type and description",
  "confidence": 0.0
}
\```

## Context

{{ context_variable }}

## Input

{{ user_input }}
```

### B. Output schema: `schemas/<task_type>_output.json` (or matching name)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "field_name": { "type": "string", "description": "..." },
    "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 }
  },
  "required": ["field_name", "confidence"],
  "additionalProperties": false
}
```

### C. Model routing entry: append to `config/task_types.yaml`

```yaml
  <task_type>:
    description: "<one-line description>"
    model: <alias>
    prompt_template: prompts/<task_type>.md
    output_schema: schemas/<task_type>_output.json
    tools: []
```

3. **If using a new model alias**, also add the routing entry in `config/donna_models.yaml` under `routing:`.

4. **Remind the user** that the prompt goes through `complete(prompt, schema, model_alias)` — never call a provider directly.

## Conventions
- All prompts include a `confidence` field (0.0-1.0) in their output schema
- Prompt templates use `{{ variable }}` for Jinja2 or describe context sections for plain markdown
- Schema files use JSON Schema draft 2020-12 with `additionalProperties: false`
- Task type names match across all three files (prompt filename, schema filename, config key)
- Check `config/donna_models.yaml` routing section — if the task type needs a fallback, add one
