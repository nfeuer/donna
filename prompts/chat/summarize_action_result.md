# Action Result Summary

Summarize the result of the action for the user in a conversational tone.

## Action Performed

**{{ action_name }}**: {{ action_description }}

## Parameters Used

{{ params_json }}

## Result

Success: {{ success }}
{{ result_data }}

## Instructions

- If the action succeeded, confirm what was done using the result data
- If the action failed, explain what went wrong clearly
- Keep it concise — one to three sentences
- For read actions, present the data in a readable format
- For write actions, confirm the change that was made

## Output

Respond with a JSON object:

```json
{
  "response_text": "Your summary of what happened",
  "suggested_actions": []
}
```
