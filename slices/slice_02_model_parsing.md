# Slice 2: Model Layer & Task Parsing

> **Goal:** Wire the model abstraction layer end-to-end. Send a natural language input through the AnthropicProvider, get structured task output back, validate it against the schema, and log the invocation.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/model-layer.md` — Model interface, routing config, invocation logging
- `docs/task-system.md` — Task schema, natural language parsing examples

## What to Build

1. **Wire up the `ModelRouter`** to load config from `config/donna_models.yaml`, instantiate `AnthropicProvider`, and route calls based on task type.

2. **Implement the input parsing pipeline** (`src/donna/orchestrator/input_parser.py`):
   - Takes raw text input + channel metadata
   - Loads the `prompts/parse_task.md` template, fills in template variables (date, time)
   - Calls `ModelRouter.complete()` with task_type="task_parse"
   - Validates the response against `schemas/task_parse_output.json`
   - Returns a structured `TaskParseResult` or raises a validation error
   - Logs the invocation via the invocation logger (Slice 1)

3. **Implement response validation** (`src/donna/models/validation.py`):
   - Validates LLM JSON output against a JSON schema
   - On schema mismatch: flag for retry (counted against retry budget from resilience layer)
   - On success: return validated and typed output

4. **Write tests:**
   - Unit test: mock the Anthropic API, verify the parsing pipeline produces valid output
   - Unit test: verify schema validation catches malformed responses
   - Smoke test (marked `@pytest.mark.llm`): send 3 Tier 1 fixture inputs through the real API and verify the output matches expected fields

## Acceptance Criteria

- [ ] `ModelRouter` resolves `task_parse` → `parser` alias → `AnthropicProvider`
- [ ] Input parser loads the prompt template and fills in current date/time
- [ ] API response is validated against `schemas/task_parse_output.json`
- [ ] Invocation is logged to `invocation_log` with correct `task_type`, `cost_usd`, `latency_ms`
- [ ] "Buy milk" parses to: title="Buy milk", domain="personal", priority=1
- [ ] "Pay electric bill by Friday" parses with a deadline and deadline_type="hard"
- [ ] Malformed API response triggers a validation error (not a crash)
- [ ] Resilient retry wrapper is used for the API call
- [ ] Unit tests pass with mocked API
- [ ] Smoke test passes against real API (costs ~$0.05)

## Not in Scope

- No Discord input yet (parsing is called programmatically)
- No deduplication
- No preference engine post-processing
- No shadow mode execution (config exists but not triggered yet)

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/model-layer.md`, `docs/task-system.md`, `prompts/parse_task.md`, `schemas/task_parse_output.json`
