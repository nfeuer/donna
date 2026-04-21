"""Generate MkDocs pages for API reference, config files, and JSON schemas.

Hooked via the mkdocs-gen-files plugin (see mkdocs.yml). Runs on every
`mkdocs build` / `mkdocs serve` and emits virtual pages into the docs tree:

- ``reference/`` — one page per Python module under ``src/donna/`` that
  invokes mkdocstrings to render classes, functions, and attributes with
  source links.
- ``config/`` — one page per YAML file under ``config/`` that embeds the
  file contents and links back to the source.
- ``schemas/`` — one page per JSON schema under ``schemas/``.

Pages are generated, not written to disk, so nothing in the repo changes.
The navigation for each section is written via a ``SUMMARY.md`` consumed by
the mkdocs-literate-nav plugin.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
CONFIG_DIR = REPO_ROOT / "config"
SCHEMAS_DIR = REPO_ROOT / "schemas"


def _generate_api_reference() -> None:
    """Walk ``src/donna`` and emit one mkdocstrings page per module."""
    nav = mkdocs_gen_files.Nav()

    with mkdocs_gen_files.open("reference/index.md", "w") as fd:
        fd.write("# API Reference\n\n")
        fd.write(
            "Every module under `src/donna/` is rendered below with types, "
            "signatures, and source links. Pages are generated on every "
            "`mkdocs build` by `scripts/gen_ref_pages.py` — do not edit them "
            "directly.\n\n"
            "Start with [`donna.orchestrator`](donna/orchestrator/index.md) "
            "or [`donna.skills.executor`](donna/skills/executor.md) — those "
            "are the hottest paths. Use the sidebar or search to jump to a "
            "specific symbol.\n"
        )

    for path in sorted(SRC.rglob("*.py")):
        module_path = path.relative_to(SRC).with_suffix("")
        doc_path = Path("reference", *module_path.parts).with_suffix(".md")
        parts = tuple(module_path.parts)

        if parts[-1] == "__init__":
            parts = parts[:-1]
            doc_path = doc_path.with_name("index.md")
        elif parts[-1] == "__main__" or parts[-1].startswith("_"):
            continue

        if not parts:
            continue

        nav_parts = parts[1:] if parts[0] == "donna" else parts
        if not nav_parts:
            nav_parts = ("donna",)
        nav[nav_parts] = doc_path.relative_to("reference").as_posix()

        with mkdocs_gen_files.open(doc_path, "w") as fd:
            identifier = ".".join(parts)
            fd.write(f"# `{identifier}`\n\n")
            fd.write(f"::: {identifier}\n")

        mkdocs_gen_files.set_edit_path(doc_path, path.relative_to(REPO_ROOT))

    with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as fd:
        fd.write("* [Overview](index.md)\n")
        fd.writelines(nav.build_literate_nav())


def _generate_config_pages() -> None:
    """Emit one page per YAML file under ``config/`` with embedded contents."""
    if not CONFIG_DIR.is_dir():
        return

    nav = mkdocs_gen_files.Nav()
    files = sorted(p for p in CONFIG_DIR.iterdir() if p.suffix in {".yaml", ".yml"})

    with mkdocs_gen_files.open("config/index.md", "w") as fd:
        fd.write("# Configuration Files\n\n")
        fd.write(
            "Donna follows a **config-over-code** principle (see "
            "[`spec_v3.md` §1.3](../reference-specs/spec-v3.md) and "
            "[`CLAUDE.md`](../start-here/conventions.md)). The files below "
            "drive task types, state transitions, model routing, prompts, "
            "and channel configuration at runtime.\n\n"
        )
        fd.write("| File | Purpose |\n")
        fd.write("|------|---------|\n")
        for path in files:
            fd.write(f"| [`{path.name}`]({path.stem}.md) | _see page_ |\n")

    for path in files:
        rel_repo_path = path.relative_to(REPO_ROOT).as_posix()
        doc_path = Path("config", f"{path.stem}.md")
        with mkdocs_gen_files.open(doc_path, "w") as fd:
            fd.write(f"# `{path.name}`\n\n")
            fd.write(f"Source: [`{rel_repo_path}`](")
            fd.write(
                f"https://github.com/nfeuer/donna/blob/main/{rel_repo_path})\n\n"
            )
            fd.write("```yaml\n")
            fd.write(f'--8<-- "{rel_repo_path}"\n')
            fd.write("```\n")
        mkdocs_gen_files.set_edit_path(doc_path, path.relative_to(REPO_ROOT))
        nav[(path.stem,)] = f"{path.stem}.md"

    with mkdocs_gen_files.open("config/SUMMARY.md", "w") as fd:
        fd.write("* [Overview](index.md)\n")
        fd.writelines(nav.build_literate_nav())


def _generate_schema_pages() -> None:
    """Emit one page per JSON schema under ``schemas/``."""
    if not SCHEMAS_DIR.is_dir():
        return

    nav = mkdocs_gen_files.Nav()
    files = sorted(SCHEMAS_DIR.glob("*.json"))

    with mkdocs_gen_files.open("schemas/index.md", "w") as fd:
        fd.write("# Structured-Output Schemas\n\n")
        fd.write(
            "Every LLM call that returns structured output is validated "
            "against a JSON schema under `schemas/`. Schemas keep model "
            "outputs machine-checkable and make the contract explicit — "
            "see [`spec_v3.md` §3.6.4 Response Validation]"
            "(../reference-specs/spec-v3.md).\n\n"
        )
        fd.write("| Schema | File |\n")
        fd.write("|--------|------|\n")
        for path in files:
            fd.write(f"| [{path.stem}]({path.stem}.md) | `{path.name}` |\n")

    for path in files:
        rel_repo_path = path.relative_to(REPO_ROOT).as_posix()
        doc_path = Path("schemas", f"{path.stem}.md")
        with mkdocs_gen_files.open(doc_path, "w") as fd:
            fd.write(f"# `{path.name}`\n\n")
            fd.write(f"Source: [`{rel_repo_path}`](")
            fd.write(
                f"https://github.com/nfeuer/donna/blob/main/{rel_repo_path})\n\n"
            )
            fd.write("```json\n")
            fd.write(f'--8<-- "{rel_repo_path}"\n')
            fd.write("```\n")
        mkdocs_gen_files.set_edit_path(doc_path, path.relative_to(REPO_ROOT))
        nav[(path.stem,)] = f"{path.stem}.md"

    with mkdocs_gen_files.open("schemas/SUMMARY.md", "w") as fd:
        fd.write("* [Overview](index.md)\n")
        fd.writelines(nav.build_literate_nav())


_generate_api_reference()
_generate_config_pages()
_generate_schema_pages()
