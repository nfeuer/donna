# Manual tool build — `{{ task_type }}`

> **Correlation ID:** `{{ correlation_id }}`
> **Tool:** `{{ capability_name }}`
> **Branch:** `{{ branch_name }}`
> **Base SHA:** `{{ base_sha }}`
> **Dashboard:** {{ dashboard_url }}

## Task summary

{{ task_summary }}

{% if proposed_signature %}
## Proposed signature

```python
async def {{ capability_name }}(
{%- for param in proposed_signature.get("params", []) %}
    {{ param.name }}: {{ param.type }}{% if not param.get("required", True) %} = None{% endif %},
{%- endfor %}
) -> {{ proposed_signature.get("returns", "Any") }}:
    """{{ proposed_signature.get("summary", "TODO: summary") }}"""
```
{% if proposed_signature.get("errors_raised") -%}
**Errors expected:** {% for err in proposed_signature["errors_raised"] %}`{{ err }}`{% if not loop.last %}, {% endif %}{% endfor %}
{% endif %}
{% endif %}

## Acceptance criteria

{% for line in acceptance_criteria -%}
- {{ line }}
{% endfor %}

The branch must pass:

1. **Diff validator** — only paths under the target list below.
2. **Tool lint** — see §10.5 (rules listed below).
3. **Import smoke** — `python -c "import donna.skills.tools.{{ capability_name }}"`
   exits 0 against the worktree.

Iteration cap is **{{ iteration_limit }}**.

## Target files

You **must** edit only these paths.

{% for label, path in target_paths.items() -%}
- **{{ label }}** — `{{ path }}`
{% endfor %}

## Required tool metadata (top of `src/donna/skills/tools/{{ capability_name }}.py`)

```python
"""<one-line summary; longer docstring optional>"""

# Slice 22 §10.5 metadata — both required.
requires_rebuild = {{ "True" if requires_rebuild_default else "False" }}  # set True if you add a new dep to pyproject.toml
default_timeout_seconds = {{ default_timeout_seconds | default(5) }}

# Optional: signal that this tool is intentionally not on any agent allowlist.
# unallowlisted = True
```

If `requires_rebuild = True`, the registry will refuse to mark this
tool active until the orchestrator restarts with a new build SHA.

## Allowlist (REQUIRED)

Update **at least one** of:

- `config/agents.yaml` — add `{{ capability_name }}` under an agent's
  `tools:` array.
- `config/skills.yaml` — declare the new tool in the registry section.
- `config/task_types.yaml` — add to a task type's allowlist.

OR, if the tool is intentionally defined-but-unusable, set
`unallowlisted = True` at the module's top level.

## Required test fixture

Add `tests/skills/tools/test_{{ capability_name }}.py`:

```python
from donna.skills.tool_test_kit import is_inert_at_import


def test_no_io_at_import():
    """§10.5 row 5 — tool must be inert at import time."""
    is_inert_at_import("donna.skills.tools.{{ capability_name }}")
```

## Reference module

Mimic the structure of `{{ reference_module_path }}` (other registered
tools live alongside under `src/donna/skills/tools/`). Match Donna's
conventions: async everywhere, structlog, type hints, no global
state.

## Forbidden patterns

The lint pipeline will reject the branch on any of:

- `import anthropic` outside `src/donna/llm/` — route through the
  donna.llm gateway, never directly.
- Module-level network/disk I/O (`requests`, `urllib`, `aiohttp`,
  `httpx`, `socket`, `subprocess`, `open`, `pathlib.Path(...).read_*`).
  Wrap calls in functions and call from a deferred entrypoint.
- Hardcoded credentials — use `vault.read('<name>')`. The secret
  scanner covers `sk-…`, `xoxb-…`, `ghp_…`, `AKIA…`, PEM private keys,
  and the vault-key naming convention.
- Other LLM SDKs (OpenAI, Cohere, etc.) — route through `donna.llm`.

{% for pattern in forbidden_patterns -%}
- Per per-task-type config: `{{ pattern }}`
{% endfor %}

## How to build

```bash
cd "{{ host_repo_path }}"
git fetch origin
{{ worktree_command }}
cd "{{ worktree_path }}"

# Open Claude Code here, paste this whole spec, build the tool.
# Then commit on the branch:
git add {{ target_paths_for_add }}
git commit -m "feat(tools/{{ capability_name }}): manual handoff for {{ correlation_id }}"
git push -u origin {{ branch_name }}
```

When done, click **Mark as built** in the dashboard
(`{{ dashboard_url }}`) or run:

```
/donna submit {{ correlation_id }} --branch {{ branch_name }}
```

Donna will diff the branch against `{{ base_sha }}`, run the lint +
import smoke, and either mark the `tool_request` row **completed** (you
then merge manually + restart the orchestrator) or post failures back
here for iteration.

## Activation

Donna does **not** auto-merge or auto-register the tool. After
validation passes:

1. Click **Mark as merged** in the dashboard once you run
   `git checkout main && git merge --no-ff {{ branch_name }} && git push`.
2. If `requires_rebuild = True`, rebuild the orchestrator image.
3. Restart the orchestrator. The new tool is registered at boot.
