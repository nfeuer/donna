# Workflow: Add a New Skill

Recipe for adding a new skill end-to-end: DSL, schema, fixtures, tests,
wiring. Nothing outside this recipe needs editing — the system is
config-driven.

**Realizes:** the config-over-code principle
([`spec_v3.md` §1.3](../reference-specs/spec-v3.md)).

## Checklist

1. **Write the output schema.**
   Add `schemas/<skill>_output.json`. It will appear automatically under
   [Schemas](../schemas/) on the next build.

2. **Define the skill.**
   Add `skills/<skill>.yaml` with `name`, `version`, `model_alias`,
   `steps`, and `output_schema: <skill>_output.json`.

3. **Register the model alias** (if new) in
   [`config/donna_models.yaml`](../config/donna_models.md).

4. **Add fixtures.**
   Under `fixtures/<skill>/tierN/` add input/expected pairs. Tier gates:
    - tier1 ≥ 0.90 (baseline)
    - tier2 ≥ 0.85 (nuance)
    - tier3 ≥ 0.75 (complexity)
    - tier4 ≥ 0.60 (adversarial)

5. **Wire intent.** If the skill should be invoked by user input, add
   routing in [`donna.orchestrator`](../reference/donna/orchestrator/index.md)
   or the relevant chat intent handler.

6. **Write tests.**
   Under `tests/unit/skills/` cover:
    - step execution with a mocked `ModelRouter`
    - schema-validation failure path
    - tool authorization denial path

7. **Run the eval.**
    ```bash
    donna eval --task-type <skill> --model <alias>
    ```

8. **Document.** Add a brief page under
   [Domain → Skill System](../domain/skill-system.md) if the skill
   introduces a new subsystem behavior.

## Where the Code Lives

| Layer | Module |
|---|---|
| DSL loading & validation | [`donna.skills.validation`](../reference/donna/skills/validation.md) |
| Execution | [`donna.skills.executor`](../reference/donna/skills/executor.md) |
| Tool dispatch | [`donna.skills.tool_dispatch`](../reference/donna/skills/tool_dispatch.md) |
| Triage agent | [`donna.skills`](../reference/donna/skills/index.md) |

## Don't

- Don't call the Anthropic or Ollama SDK directly — route through
  [`ModelRouter`](../reference/donna/models/router.md).
- Don't hand-roll tool execution — let `ToolDispatcher` do it.
- Don't add `print()` — use `structlog`.
- Don't bypass schema validation.
