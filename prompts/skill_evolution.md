# Skill Evolution Prompt

You are analyzing a degraded skill and regenerating it with corrections.

## Input

You receive:
1. **Current skill version** — YAML backbone, step definitions, output schemas
2. **Divergence cases** — Run traces showing where the skill failed or diverged from expected
3. **Correction log** — User-initiated corrections tied to failure patterns
4. **Statistical summary** — Failure counts, patterns, confidence in each identified issue
5. **Prior evolution attempts** — History of previous regenerations (if any) and their outcomes
6. **Fixture library** — Expected input/output pairs the skill must satisfy

## Task

Diagnose the primary failure pattern by analyzing divergence cases and corrections. Identify which step(s) or decision points are causing failures. Generate a new skill version that:

1. **Preserves workflow structure** — Keep the overall task flow unless restructuring is essential
2. **Fixes identified issues** — Update prompts, schemas, or step logic to address the diagnosed failure
3. **Passes fixture library** — Ensure the new version satisfies all test cases
4. **Avoids regression** — Remain conservative; only change what's necessary to fix the identified issue

## Output

Return a JSON object strictly matching the `schemas/skill_evolution_output.json` schema:

```json
{
  "diagnosis": {
    "identified_failure_step": "<step_name>",
    "failure_pattern": "<short description of the recurring failure>",
    "confidence": 0.8
  },
  "new_skill_version": {
    "yaml_backbone": "<full YAML string of the regenerated skill>",
    "step_content": {
      "<step_name>": "<updated prompt markdown>",
      ...
    },
    "output_schemas": {
      "<step_name>": { <JSON schema object> },
      ...
    }
  },
  "changelog": "<short summary of what changed and why>",
  "targeted_failure_cases": ["<run_id>", ...],
  "expected_improvement": "<one sentence predicting the outcome>"
}
```

## Validation Gates

The executor will validate your output against four gates before replacing the current version:

1. **Schema compliance** — Output must match the exact structure above
2. **YAML syntax** — `yaml_backbone` must be valid YAML
3. **Fixture pass rate** — New version must pass >= `evolution_fixture_regression_pass_rate` (95% by default) of the fixture library
4. **Improvement signal** — If the new version passes fewer fixtures than the current version, it is rejected

If validation fails, the skill remains unchanged and an evolution attempt is logged.
