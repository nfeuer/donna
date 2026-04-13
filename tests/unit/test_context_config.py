"""Unit tests for the new context-window config fields."""

from donna.config import ModelConfig, ModelsConfig, OllamaConfig


def test_ollama_config_defaults_for_num_ctx() -> None:
    cfg = OllamaConfig()
    assert cfg.default_num_ctx == 8192
    assert cfg.default_output_reserve == 1024


def test_ollama_config_accepts_overrides() -> None:
    cfg = OllamaConfig(default_num_ctx=4096, default_output_reserve=512)
    assert cfg.default_num_ctx == 4096
    assert cfg.default_output_reserve == 512


def test_model_config_num_ctx_defaults_to_none() -> None:
    mc = ModelConfig(provider="ollama", model="qwen2.5:32b-instruct-q6_K")
    assert mc.num_ctx is None


def test_model_config_accepts_num_ctx_override() -> None:
    mc = ModelConfig(
        provider="ollama", model="qwen2.5:32b-instruct-q6_K", num_ctx=16384
    )
    assert mc.num_ctx == 16384


def test_models_config_roundtrip_with_new_fields() -> None:
    data = {
        "models": {
            "local_parser": {
                "provider": "ollama",
                "model": "qwen2.5:32b-instruct-q6_K",
                "num_ctx": 16384,
            }
        },
        "routing": {},
        "ollama": {
            "base_url": "http://localhost:11434",
            "timeout_s": 120,
            "keepalive": "5m",
            "default_num_ctx": 8192,
            "default_output_reserve": 1024,
        },
    }
    cfg = ModelsConfig(**data)
    assert cfg.ollama.default_num_ctx == 8192
    assert cfg.models["local_parser"].num_ctx == 16384
