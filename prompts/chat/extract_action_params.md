# Action Parameter Extraction

Extract the parameters for the action **{{ action_name }}** from the user's message.

## Action Description

{{ action_description }}

## Parameter Schema

{{ parameter_schema }}

## Dashboard Context

{{ dashboard_context }}

When the user says "this", "it", or similar pronouns, resolve them using the dashboard context above.

## Conversation History

{{ conversation_history }}

## User Message

{{ user_input }}

## Output

Respond with a JSON object containing ONLY the parameter values. Use the exact field names from the schema. Omit fields that cannot be determined from the message. Example:

```json
{{ example_output }}
```
