# Testing

## Layout

```
tests/
  unit/          # no external deps — fast
  integration/   # may use real SQLite, mocked HTTP
  conftest.py    # async fixtures (state machine, database)
```

## Markers

Configured in [`pyproject.toml`](https://github.com/nfeuer/donna/blob/main/pyproject.toml):

- `@pytest.mark.unit` — fast, no external deps
- `@pytest.mark.integration` — real SQLite, mocked HTTP
- `@pytest.mark.llm` — calls a real LLM API (costs money)
- `@pytest.mark.slow` — skip by default

## Running

```bash
# Default — unit only
pytest tests/unit/

# Include integration
pytest tests/unit/ tests/integration/ -m "not slow and not llm"

# Everything (including LLM-hitting tests)
pytest
```

## CI

`.github/workflows/ci.yml` runs `pytest -m "not slow and not llm"` plus
`ruff check` and `mypy --strict`.

## Conventions

- Async throughout — `pytest-asyncio` is in `auto` mode.
- Mock `ModelRouter` in unit tests rather than hitting a real provider.
- Every new skill gets at least a schema-validation failure test.
