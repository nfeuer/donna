# Manual skill build — `{{ task_type }}`

> **Correlation ID:** `{{ correlation_id }}`
> **Capability:** `{{ capability_name }}`
> **Branch:** `{{ branch_name }}`
> **Base SHA:** `{{ base_sha }}`
> **Dashboard:** {{ dashboard_url }}

## Task summary

{{ task_summary }}

## Acceptance criteria

{% for line in acceptance_criteria -%}
- {{ line }}
{% endfor %}

The skill must pass `ValidationExecutor` against the fixtures you commit
under `fixtures/skills/{{ capability_name }}.json`. Donna's poller runs
this automatically once you click **Mark as built** in the dashboard.

## Target files

You **must** edit only these paths. Anything outside this list will be
rejected by the diff validator with a list of out-of-scope files (you
can iterate; iteration cap is **{{ iteration_limit }}**).

{% for label, path in target_paths.items() -%}
- **{{ label }}** — `{{ path }}`
{% endfor %}

## Reference module

Mimic the structure of `{{ reference_module_path }}`. Read it first to
match Donna's conventions (async-everywhere, structlog, type hints,
single-connection aiosqlite).

## Forbidden patterns

The pre-validation lint will reject the branch if any commit on it
matches:

{% for pattern in forbidden_patterns -%}
- `{{ pattern }}`
{% endfor %}

In particular: never embed secret values; reference vault entries by
name (`vault.read('<name>')`). Never `import anthropic` outside
`src/donna/llm/`.

## How to build

```bash
cd "{{ host_repo_path }}"
git fetch origin
{{ worktree_command }}
cd "{{ worktree_path }}"

# open Claude Code here, paste this whole spec, ship a working build
# then commit on the branch:
git add {{ target_paths_for_add }}
git commit -m "feat({{ capability_name }}): manual handoff for {{ correlation_id }}"
git push -u origin {{ branch_name }}
```

When done, click **Mark as built** in the dashboard
(`{{ dashboard_url }}`) or run:

```
/donna submit {{ correlation_id }} --branch {{ branch_name }}
```

Donna will diff the branch against `{{ base_sha }}`, run the validator,
and either promote the skill into `sandbox` (validated) or post failures
back here for iteration.
