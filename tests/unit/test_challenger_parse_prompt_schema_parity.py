"""F-W3-G: prompt/schema parity guards.

Prevents silent drift between the JSON schema the LLM output is validated
against and the prompt that instructs the LLM to emit it. When a field is
added/renamed in the schema but forgotten in the prompt (or vice versa),
the LLM's output silently loses or misreports that field until a test
catches it.

These tests assert every top-level property declared in the schema is
mentioned somewhere in the prompt body.
"""
from __future__ import annotations

import json
import pathlib


def test_challenger_parse_prompt_mentions_all_schema_fields() -> None:
    schema_path = pathlib.Path("schemas/challenger_parse.json")
    prompt_path = pathlib.Path("prompts/challenger_parse.md")
    schema = json.loads(schema_path.read_text())
    prompt = prompt_path.read_text()
    for field_name in schema["properties"]:
        assert field_name in prompt, (
            f"field '{field_name}' missing from challenger_parse.md"
        )


def test_claude_novelty_prompt_mentions_all_schema_fields() -> None:
    schema = json.loads(pathlib.Path("schemas/claude_novelty.json").read_text())
    prompt = pathlib.Path("prompts/claude_novelty.md").read_text()
    for field_name in schema["properties"]:
        assert field_name in prompt, (
            f"field '{field_name}' missing from claude_novelty.md"
        )
