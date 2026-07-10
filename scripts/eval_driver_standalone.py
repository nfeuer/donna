#!/usr/bin/env python
"""Standalone eval driver reusing Donna's own harness helpers.

Differences from `donna eval`:
- Ollama base_url forced to localhost:11434 (published port) so it runs from host.
- Records per-case latency and writes JSONL results for later analysis.
- Supports --template-file/--schema-file overrides for task types without
  task_types.yaml-registered fixtures (e.g. the challenger dispatch prompt).
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO = Path("/mnt/donna/donna")
sys.path.insert(0, str(REPO / "src"))

from donna.cli import _compare_fields, _render_eval_prompt  # noqa: E402
from donna.config import load_models_config, load_task_types_config  # noqa: E402
from donna.models.router import ModelRouter  # noqa: E402
from donna.models.validation import validate_output  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    config_dir = REPO / "config"
    models_config = load_models_config(config_dir)
    task_types_config = load_task_types_config(config_dir)

    jinja_env = None
    capabilities = None
    if args.jinja:
        import yaml
        from jinja2 import Environment
        jinja_env = Environment()
        capabilities = yaml.safe_load(
            (REPO / "config/capabilities.yaml").read_text()
        )["capabilities"]

    if args.template_file:
        template = Path(args.template_file).read_text()
        schema = json.loads(Path(args.schema_file).read_text())
    else:
        router = ModelRouter(models_config, task_types_config, REPO)
        template = router.get_prompt_template(args.task_type)
        schema = router.get_output_schema(args.task_type)

    provider_name, model_id = args.model.split("/", 1)
    if provider_name == "ollama":
        from donna.models.providers.ollama import OllamaProvider
        provider = OllamaProvider(base_url="http://localhost:11434", timeout_s=180)
    elif provider_name == "anthropic":
        from donna.models.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider()
    else:
        print(f"Unknown provider: {provider_name}")
        return 2

    fixtures_dir = Path(args.fixtures_dir)
    fixture_files = sorted(fixtures_dir.glob("tier*.json"))
    if args.tier is not None:
        fixture_files = [f for f in fixture_files if f.name.startswith(f"tier{args.tier}")]
    if not fixture_files:
        print(f"No fixtures in {fixtures_dir}")
        return 2

    out = open(args.out, "a") if args.out else None
    overall_pass = True
    for fixture_path in fixture_files:
        fixture = json.loads(fixture_path.read_text())
        tier, name, gate = fixture["tier"], fixture["name"], fixture["pass_gate"]
        cases = fixture["cases"]
        passed = 0
        print(f"\nTier {tier} — {name}  ({len(cases)} cases, gate {gate:.0%})  [{args.model}]")
        for case in cases:
            if jinja_env is not None:
                prompt = jinja_env.from_string(template).render(
                    capabilities=capabilities, **case["input"]
                )
            else:
                prompt = _render_eval_prompt(template, case["input"])
            t0 = time.monotonic()
            row = {"suite": args.task_type, "model": args.model, "tier": tier,
                   "case": case["id"]}
            try:
                if provider_name == "ollama":
                    result, _usage = await provider.complete(
                        prompt, model_id, output_schema=schema
                    )
                else:
                    result, _usage = await provider.complete(prompt, model_id)
                latency = time.monotonic() - t0
                validate_output(result, schema)
                mismatches = _compare_fields(case["expected"], result)
                ok = not mismatches
                passed += ok
                row.update(status="pass" if ok else "fail",
                           mismatches=mismatches, latency_s=round(latency, 2),
                           output=result)
                print(f"  {'PASS' if ok else 'FAIL'}  {case['id']}  {latency:5.1f}s"
                      + ("" if ok else f"  — {'; '.join(mismatches)}"))
            except Exception as exc:
                latency = time.monotonic() - t0
                row.update(status="error", error=str(exc)[:300],
                           latency_s=round(latency, 2))
                print(f"  ERROR {case['id']}  {latency:5.1f}s  — {exc}")
            if out:
                out.write(json.dumps(row) + "\n")
                out.flush()
        rate = passed / len(cases)
        tier_ok = rate >= gate
        overall_pass = overall_pass and tier_ok
        print(f"  => {'PASS' if tier_ok else 'FAIL'}  {passed}/{len(cases)} ({rate:.0%})")
    if out:
        out.close()
    print(f"\nOverall: {'PASS' if overall_pass else 'FAIL'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-type", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--fixtures-dir", required=True)
    ap.add_argument("--tier", type=int)
    ap.add_argument("--template-file")
    ap.add_argument("--schema-file")
    ap.add_argument("--jinja", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a)))
