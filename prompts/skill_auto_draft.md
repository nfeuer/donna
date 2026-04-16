# skill_auto_draft prompt

Claude is generating a Donna skill — YAML backbone + per-step prompts + per-step
output schemas + 3-5 fixture test cases — for a capability flagged as a
high-value draft candidate by the SkillCandidateDetector.

The runtime constructs the actual prompt programmatically in
`AutoDrafter._build_prompt(capability, samples)`; this file documents the
structure and contract. Keep the programmatic builder and this document in
sync whenever the prompt changes.

## Inputs threaded in by the orchestrator

- `capability.name` — unique capability identifier.
- `capability.description` — human-written summary.
- `capability.input_schema` — JSON Schema defining the capability input shape.
- `samples` — up to 5 recent `invocation_log` rows for the task_type
  matching the capability, used as in-context exemplars. Each sample is
  presented as `{"input_hash": "...", "output": "..."}`; raw inputs are
  not stored in the log.

## Task

Produce a strict-JSON payload conforming to `schemas/skill_auto_draft_output.json`:

```
{
  "skill_yaml": "<skill.yaml backbone — capability_name, version, description, inputs, 1–3 llm steps, final_output>",
  "step_prompts": {"<step_name>": "<markdown prompt body>"},
  "output_schemas": {"<step_name>": {<JSON Schema>}},
  "fixtures": [
    {
      "case_name": "<short snake_case>",
      "input": {<concrete example>},
      "expected_output_shape": {<JSON Schema fragment>}
    }
  ]
}
```

## Rules

1. **Strict JSON.** No preamble, no trailing prose, no code fences — just JSON.
2. **1-3 llm steps.** More steps are a refactor, not a first draft.
3. **3-5 fixtures.** One happy path, one near-miss, one edge case minimum.
4. **Safety first.** Generated skills will start in `draft` and require
   human approval before sandbox promotion. Prefer conservative logic;
   do not request tools with side effects.
5. **Respect the capability input schema.** Every fixture `input` must
   validate against `capability.input_schema`.
6. **Expected outputs are *shapes*, not exact values.** Use JSON Schema
   fragments describing the output structure Claude should produce.

The AutoDrafter will then:

- Validate each fixture against the generated skill in a sandbox
  executor (when one is wired; otherwise validation is deferred).
- Persist `skill` + `skill_version` rows in `claude_native`, transition
  through `skill_candidate → draft` via `SkillLifecycleManager`, and
  mark the originating candidate report as `drafted`.
