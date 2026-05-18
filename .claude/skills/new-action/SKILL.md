---
name: new-action
description: Scaffold a new chat action handler, config entry, prompt template, and schema
disable-model-invocation: true
---

# New Action

Scaffold a new chat action for the config-driven action registry. Chat actions let Donna's chat mode execute real operations (query tasks, create events, etc.).

## Workflow

1. **Ask the user for:**
   - Action name (snake_case, e.g. `search_calendar`)
   - One-line description
   - Domain: `tasks`, `calendar`, `system`, `preferences`, `notifications`, or a new domain
   - Safety level: `read` (no confirmation needed) or `write` (requires user confirmation)
   - Parameters (name, type, required/optional, enum values if any)

2. **Create/update four files:**

### A. Config entry: append to `config/chat_actions.yaml`

```yaml
  <action_name>:
    description: "<one-line description>"
    domain: <domain>
    safety: <read|write>
    handler: donna.chat.actions.<domain>.<action_name>
    parameters:
      type: object
      properties:
        <param_name>:
          type: <string|integer|boolean>
          description: "<description>"
          # enum: [value1, value2]  # if applicable
      required: [<required_params>]
```

### B. Handler function: `src/donna/chat/actions/<domain>.py`

If the domain file exists, add the function. If not, create the module.

```python
async def <action_name>(params: dict, ctx: ActionContext) -> ActionResult:
    """<One-line description>.

    Args:
        params: Validated parameters from chat action config.
        ctx: Action context with db, user_id, config.

    Returns:
        ActionResult with data and display message.
    """
    # Implementation
    return ActionResult(
        success=True,
        data={...},
        message="<human-readable summary>",
    )
```

### C. Action result summary prompt (if the action returns complex data): `prompts/chat/summarize_<action_name>.md`

Only needed for actions that return data the LLM should narrate. Simple CRUD actions use the generic `summarize_action_result.md`.

### D. Test: `tests/unit/chat/test_action_<action_name>.py`

Minimal test that the handler returns the expected ActionResult structure.

3. **Verify the action loads:**
   ```bash
   python3 -c "import yaml; d=yaml.safe_load(open('config/chat_actions.yaml')); print(d['actions']['<action_name>'])"
   ```

## Conventions
- Handler module path must match config: `donna.chat.actions.<domain>.<action_name>`
- `read` actions never mutate state; `write` actions always do
- All handlers are `async def` and accept `(params, ctx)`
- Parameters are validated against the config schema before the handler runs
- Actions that return lists should include a `count` field in the result data
