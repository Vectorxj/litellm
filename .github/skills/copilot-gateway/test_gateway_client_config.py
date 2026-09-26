import json
from pathlib import Path
from typing import Final

import pytest
import tomllib
from gateway_client_config import claude_configuration, claude_environment, codex_catalog, codex_configuration
from gateway_config import Checkpoint, EmbeddingModel, GatewayModel, ModelPin, Selection
from pydantic import JsonValue, TypeAdapter

CHECKPOINT: Final = Checkpoint.model_validate_json(Path(__file__).with_name("checkpoint.json").read_bytes())
JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
REASONING_MODEL: Final = ModelPin(
    id="gpt-6-sol",
    display_name="GPT-6 Sol",
    context_window_tokens=1000000,
    max_input_tokens=1000000,
    max_output_tokens=128000,
    reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
)
CLAUDE_MODEL: Final = ModelPin(
    id="claude-opus-5.5",
    display_name="Claude Opus 5.5",
    context_window_tokens=1000000,
    max_input_tokens=872000,
    max_output_tokens=128000,
    reasoning_efforts=("low", "medium", "high", "xhigh", "max"),
    supported_endpoints=("/v1/messages", "/chat/completions"),
)
PLAIN_MODEL: Final = ModelPin(
    id="fixture-plain-chat",
    context_window_tokens=100000,
    max_input_tokens=80000,
    max_output_tokens=4000,
    reasoning_efforts=(),
    supported_endpoints=("/chat/completions",),
    supports_vision=False,
    supports_parallel_tools=False,
)
MODELS: Final[tuple[GatewayModel, ...]] = (
    REASONING_MODEL,
    CLAUDE_MODEL,
    PLAIN_MODEL,
    PLAIN_MODEL.model_copy(update={"id": "fixture-no-tools", "supports_tools": False}),
    EmbeddingModel(id="fixture-embedding", display_name="Fixture embedding"),
)
SELECTION: Final = Selection(
    codex_model="gpt-6-sol",
    claude_model="claude-opus-5.5",
    port=14000,
    checkpoint=CHECKPOINT.checkpoint,
    checkpoint_sha256="a" * 64,
    catalog_models=MODELS,
)


def test_codex_catalog_lists_every_tool_capable_chat_with_its_own_limits() -> None:
    catalog: Final = JSON_OBJECT.validate_json(json.dumps(codex_catalog(CHECKPOINT, MODELS, "Verified prompt fixture")))
    models: Final = catalog["models"]
    assert isinstance(models, list)
    assert len(models) == 3
    reasoning, claude, plain = models
    assert isinstance(reasoning, dict)
    assert isinstance(claude, dict)
    assert isinstance(plain, dict)
    assert [reasoning["slug"], claude["slug"], plain["slug"]] == [
        "gpt-6-sol",
        "claude-opus-5.5",
        "fixture-plain-chat",
    ]
    assert reasoning["display_name"] == "GPT-6 Sol"
    assert claude["display_name"] == "Claude Opus 5.5"
    assert plain["display_name"] == "fixture-plain-chat"
    assert reasoning["context_window"] == reasoning["max_context_window"] == 1000000
    assert reasoning["effective_context_window_percent"] == 87
    assert reasoning["auto_compact_token_limit"] == 784800
    assert plain["context_window"] == plain["max_context_window"] == 100000
    assert plain["effective_context_window_percent"] == 80
    assert plain["auto_compact_token_limit"] == 72000
    assert reasoning["default_reasoning_level"] == "high"
    assert reasoning["supported_reasoning_levels"] == [
        {"effort": effort, "description": f"{effort.capitalize()} reasoning"}
        for effort in ("none", "low", "medium", "high", "xhigh", "max")
    ]
    assert plain["default_reasoning_level"] is None
    assert plain["supported_reasoning_levels"] == []
    assert reasoning["supports_parallel_tool_calls"] is True
    assert plain["supports_parallel_tool_calls"] is False
    assert reasoning["input_modalities"] == ["text", "image"]
    assert plain["input_modalities"] == ["text"]
    for model in (reasoning, claude, plain):
        assert model["visibility"] == "list"
        assert model["supported_in_api"] is True
        assert model["base_instructions"] == "Verified prompt fixture"
        assert model["model_messages"] == {"instructions_template": "Verified prompt fixture"}
        assert model["apply_patch_tool_type"] == "freeform"
        assert model["upgrade"] is None
    assert reasoning["supports_reasoning_summary_parameter"] is True
    assert reasoning["default_reasoning_summary"] == "auto"
    assert claude["supports_reasoning_summary_parameter"] is False
    assert plain["supports_reasoning_summary_parameter"] is False
    assert claude["default_reasoning_summary"] == plain["default_reasoning_summary"] == "none"


def test_codex_catalog_prefers_the_checkpoint_default_without_hiding_other_models() -> None:
    checkpoint: Final = CHECKPOINT.model_copy(update={"default_codex_model": PLAIN_MODEL.id})
    catalog: Final = codex_catalog(checkpoint, MODELS, "Prompt fixture")
    models: Final = catalog["models"]
    assert isinstance(models, list)
    ids: Final = tuple(model["slug"] for model in models if isinstance(model, dict))
    assert ids == (PLAIN_MODEL.id, REASONING_MODEL.id, CLAUDE_MODEL.id)
    selection: Final = SELECTION.model_copy(update={"codex_model": REASONING_MODEL.id})
    config: Final = tomllib.loads(codex_configuration(checkpoint, selection, REASONING_MODEL, Path("/catalog.json")))
    assert config["model"] == REASONING_MODEL.id


@pytest.mark.parametrize(
    ("model", "expected"),
    (
        (REASONING_MODEL, "high"),
        (REASONING_MODEL.model_copy(update={"reasoning_efforts": ("low", "medium")}), "medium"),
        (REASONING_MODEL.model_copy(update={"reasoning_efforts": ("xhigh",)}), "xhigh"),
        (REASONING_MODEL.model_copy(update={"reasoning_efforts": ("none",)}), "none"),
        (PLAIN_MODEL, None),
    ),
)
def test_codex_reasoning_defaults_are_always_advertised(model: ModelPin, expected: str | None) -> None:
    catalog: Final = codex_catalog(CHECKPOINT, (model,), "Prompt fixture")
    models: Final = catalog["models"]
    assert isinstance(models, list)
    entry: Final = models[0]
    assert isinstance(entry, dict)
    assert entry["default_reasoning_level"] == expected
    assert expected is None or expected in model.reasoning_efforts


@pytest.mark.parametrize("model", (REASONING_MODEL, PLAIN_MODEL))
def test_codex_default_is_editable_without_global_model_tuning(model: ModelPin) -> None:
    selection: Final = SELECTION.model_copy(update={"codex_model": model.id})
    rendered: Final = codex_configuration(CHECKPOINT, selection, model, Path("/private/catalog.json"))
    config: Final = tomllib.loads(rendered)
    assert config["model"] == model.id
    assert config["model_provider"] == "copilot_gateway"
    assert config["model_catalog_json"] == "/private/catalog.json"
    assert not {"model_context_window", "model_auto_compact_token_limit", "model_reasoning_effort"}.intersection(config)
    assert config["model_providers"]["copilot_gateway"] == {
        "name": "Local Copilot gateway",
        "base_url": "http://127.0.0.1:14000/v1",
        "env_key": "LITELLM_API_KEY",
        "wire_api": "responses",
        "requires_openai_auth": False,
        "supports_websockets": False,
        "request_max_retries": 2,
        "stream_max_retries": 5,
        "stream_idle_timeout_ms": 600000,
    }


@pytest.mark.parametrize("claude_only", (False, True))
def test_claude_picker_lists_real_ids_without_forcing_session_or_subagent_defaults(claude_only: bool) -> None:
    selection: Final = SELECTION.model_copy(update={"claude_only": claude_only})
    settings: Final = claude_configuration(selection)
    assert settings["model"] == "claude-opus-5.5"
    assert settings["modelPicker"] == {
        "options": [
            {"model": "gpt-6-sol", "label": "GPT-6 Sol"},
            {"model": "claude-opus-5.5", "label": "Claude Opus 5.5"},
            {"model": "fixture-plain-chat", "label": "fixture-plain-chat"},
        ],
        "replaceBuiltInOptions": True,
    }
    environment: Final = claude_environment(selection)
    assert not {
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",
    }.intersection(environment)
    assert environment["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:14000"
    assert environment["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] == "1"
    assert environment["ENABLE_TOOL_SEARCH"] == "false"
    assert {
        value
        for key, value in environment.items()
        if key.startswith("ANTHROPIC_DEFAULT_") or key == "ANTHROPIC_SMALL_FAST_MODEL"
    } == {"claude-opus-5.5"}
    assert settings["permissions"] == {"deny": ["WebSearch"]}
    assert settings["env"] == environment


def test_claude_picker_without_discovery_still_offers_the_selected_model() -> None:
    selection: Final = SELECTION.model_copy(update={"claude_model": "gpt-6-sol", "catalog_models": ()})
    settings: Final = claude_configuration(selection)
    assert settings["model"] == "gpt-6-sol"
    assert settings["modelPicker"] == {
        "options": [{"model": "gpt-6-sol", "label": "gpt-6-sol"}],
        "replaceBuiltInOptions": True,
    }
