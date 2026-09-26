import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from gateway_config import Checkpoint, GatewayModel, ModelPin, Selection
from pydantic import JsonValue


def codex_configuration(checkpoint: Checkpoint, selection: Selection, pin: ModelPin, catalog_path: Path) -> str:
    return "\n".join(
        (
            f"model = {json.dumps(selection.codex_model)}",
            'model_provider = "copilot_gateway"',
            f"model_catalog_json = {json.dumps(str(catalog_path))}",
            'approval_policy = "on-request"',
            'sandbox_mode = "workspace-write"',
            'web_search = "disabled"',
            "check_for_update_on_startup = false",
            "",
            "[model_providers.copilot_gateway]",
            'name = "Local Copilot gateway"',
            f'base_url = "http://127.0.0.1:{selection.port}/v1"',
            'env_key = "LITELLM_API_KEY"',
            'wire_api = "responses"',
            "requires_openai_auth = false",
            "supports_websockets = false",
            "request_max_retries = 2",
            "stream_max_retries = 5",
            "stream_idle_timeout_ms = 600000",
            "",
        )
    )


def codex_model(checkpoint: Checkpoint, model: ModelPin, prompt: str, priority: int) -> dict[str, JsonValue]:
    default_effort: Final = next(
        (
            effort
            for effort in (checkpoint.reasoning_effort, "medium", *model.reasoning_efforts)
            if effort in model.reasoning_efforts
        ),
        None,
    )
    supports_summary: Final = model.native_responses and model.id.startswith("gpt-") and bool(model.reasoning_efforts)
    return {
        "slug": model.id,
        "display_name": model.display_name or model.id,
        "description": "Authorized Copilot model",
        "default_reasoning_level": default_effort,
        "supported_reasoning_levels": [
            {"effort": effort, "description": f"{effort.capitalize()} reasoning"} for effort in model.reasoning_efforts
        ],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": prompt,
        "model_messages": {"instructions_template": prompt},
        "supports_reasoning_summary_parameter": supports_summary,
        "default_reasoning_summary": "auto" if supports_summary else "none",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": "freeform",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": model.supports_parallel_tools,
        "context_window": model.context_window_tokens,
        "max_context_window": model.context_window_tokens,
        "auto_compact_token_limit": model.safe_input_tokens * 9 // 10,
        "effective_context_window_percent": model.safe_input_tokens * 100 // model.context_window_tokens,
        "experimental_supported_tools": [],
        "input_modalities": ["text", "image"] if model.supports_vision else ["text"],
    }


def codex_catalog(checkpoint: Checkpoint, models: tuple[GatewayModel, ...], prompt: str) -> Mapping[str, JsonValue]:
    selectable: Final = tuple(model for model in models if isinstance(model, ModelPin) and model.supports_tools)
    ordered: Final = sorted(selectable, key=lambda model: model.id != checkpoint.default_codex_model)
    return {
        "models": [codex_model(checkpoint, model, prompt, priority) for priority, model in enumerate(ordered, start=1)]
    }


def claude_environment(selection: Selection) -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{selection.port}",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_FABLE_MODEL": selection.claude_model,
        "ANTHROPIC_SMALL_FAST_MODEL": selection.claude_model,
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        "ENABLE_TOOL_SEARCH": "false",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }


def claude_configuration(selection: Selection) -> Mapping[str, JsonValue]:
    options: Final[list[JsonValue]] = [
        {"model": model.id, "label": model.display_name or model.id}
        for model in selection.catalog_models
        if isinstance(model, ModelPin) and model.supports_tools
    ]
    return {
        "model": selection.claude_model,
        "modelPicker": {
            "options": options or [{"model": selection.claude_model, "label": selection.claude_model}],
            "replaceBuiltInOptions": True,
        },
        "env": dict[str, JsonValue](claude_environment(selection)),
        "permissions": {"deny": ["WebSearch"]},
    }
