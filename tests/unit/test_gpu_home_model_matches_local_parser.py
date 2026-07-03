"""Config-consistency test: GPU home_model must match local_parser.model (9b).

If this test ever fails it means one of the two configs drifted from the
other, which causes every local_parser call to trigger a spurious Ollama
model swap.  Fix by making both configs agree on the same quantization tag.
"""
from __future__ import annotations

from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.skipif(
    not (CONFIG_DIR / "llm_gateway.yaml").exists()
    or not (CONFIG_DIR / "donna_models.yaml").exists(),
    reason="Config files not present — run from repo root",
)
def test_gpu_home_model_matches_local_parser_model() -> None:
    """llm_gateway.gpu.home_model must equal donna_models.models.local_parser.model.

    The LLM queue compares model tags by string equality.  If the two strings
    diverge, every local_parser call will look like non-home work and will
    trigger a GPU model swap out-and-back on each call.
    """
    from donna.config import load_models_config
    from donna.llm.types import load_gateway_config

    gw = load_gateway_config(CONFIG_DIR)
    models = load_models_config(CONFIG_DIR)

    local_parser = models.models.get("local_parser")
    assert local_parser is not None, "donna_models.yaml must define models.local_parser"

    assert gw.gpu.home_model == local_parser.model, (
        f"GPU home_model ({gw.gpu.home_model!r}) does not match "
        f"local_parser.model ({local_parser.model!r}).  "
        "Update config/llm_gateway.yaml gpu.home_model to agree."
    )
