# Evaluation Harness

The offline eval system answers the question *"is this model (or this
prompt change) still good enough to ship?"* for each task type. Design
reference: [`spec_v3.md` §4.5](../reference-specs/spec-v3.md).

## Fixture Layout

```
fixtures/
  parse_task/
    tier1/           # baseline
    tier2/           # nuance
    tier3/           # complexity
    tier4/           # adversarial
  classify_priority/
  deduplication/
  escalation_awareness/
  instruction_following/
  generate_digest/
  ...
```

Each tier directory contains `input` / `expected` pairs. Pass gates are
numeric per tier (see [Workflows → Run Evals](../workflows/run-evals.md)).

## CLI

Implemented in [`donna.cli`](../reference/donna/cli.md):

```bash
donna eval --task-type <type> --model <alias> [--tier N]
```

## Scoring Dimensions

- **Structural correctness** (schema-valid JSON)
- **Semantic correctness** (matches `expected`)
- **Escalation awareness** — did the model know when to punt?
- **Instruction following** — did it respect constraints in the prompt?

Escalation and instruction-following each get their own fixture sets.

## Workflow

See [Workflows → Run Evals](../workflows/run-evals.md) for the operator
playbook. After a run, inspect `invocation_log` for per-call cost.
