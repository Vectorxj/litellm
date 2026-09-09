import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

API_BASE: Final = "https://api.githubcopilot.com"
UPSTREAM_TOKEN_ENV: Final = "COPILOT_GATEWAY_UPSTREAM_TOKEN"
PROXY_KEY_ENV: Final = "COPILOT_GATEWAY_PROXY_KEY"


class ModelPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^gpt-[a-zA-Z0-9._-]+$")
    context_window_tokens: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    reasoning_efforts: tuple[Literal["low", "medium", "high", "xhigh", "max"], ...]


class Checkpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    checkpoint: str
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    uv_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    python_version: str
    uv_version: str
    codex_version: str
    codex_prompt_url: str = Field(
        pattern=r"^https://raw\.githubusercontent\.com/openai/codex/[^/]+/codex-rs/[^?#]+\.md$"
    )
    codex_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claude_version: str
    default_codex_model: str
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"]
    upstream_headers: Mapping[str, str]
    models: tuple[ModelPin, ...]


class Selection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    codex_model: str
    claude_model: str
    port: int = Field(ge=1024, le=65535)
    checkpoint: str
    checkpoint_sha256: str


@dataclass(frozen=True, slots=True)
class Paths:
    kit: Path
    state: Path

    @property
    def repo(self) -> Path:
        return self.kit.parents[2]

    @property
    def proxy_config(self) -> Path:
        return self.state / "proxy.yaml"

    @property
    def token(self) -> Path:
        return self.state / "upstream-token"

    @property
    def key(self) -> Path:
        return self.state / "proxy-key"


@dataclass(frozen=True, slots=True)
class Problem:
    message: str


def selected_pins(checkpoint: Checkpoint, selection: Selection) -> tuple[ModelPin, ...] | Problem:
    requested: Final = frozenset((selection.codex_model, selection.claude_model))
    available: Final = frozenset(model.id for model in checkpoint.models)
    missing: Final = requested - available
    if missing:
        return Problem(
            f"Models are not pinned in this checkpoint: {', '.join(sorted(missing))}. "
            "Use the skill to qualify and pin a new model; no fallback was selected."
        )
    if any(
        model.max_input_tokens + model.max_output_tokens > model.context_window_tokens
        or checkpoint.reasoning_effort not in model.reasoning_efforts
        for model in checkpoint.models
        if model.id in requested
    ):
        return Problem("The checkpoint has inconsistent context limits or an unsupported default reasoning effort.")
    return tuple(model for model in checkpoint.models if model.id in requested)


def proxy_configuration(checkpoint: Checkpoint, pins: tuple[ModelPin, ...]) -> Mapping[str, JsonValue]:
    return {
        "model_list": [
            {
                "model_name": pin.id,
                "litellm_params": {
                    "model": f"openai/{pin.id}",
                    "api_base": API_BASE,
                    "api_key": f"os.environ/{UPSTREAM_TOKEN_ENV}",
                    "extra_headers": dict(checkpoint.upstream_headers),
                    "store": False,
                    "timeout": 600,
                    "max_retries": 0,
                },
                "model_info": {
                    "mode": "responses",
                    "max_input_tokens": pin.max_input_tokens,
                    "max_output_tokens": pin.max_output_tokens,
                    "supports_function_calling": True,
                    "supports_parallel_function_calling": True,
                    "supports_reasoning": True,
                },
            }
            for pin in pins
        ],
        "general_settings": {
            "master_key": f"os.environ/{PROXY_KEY_ENV}",
            "disable_spend_logs": True,
        },
        "litellm_settings": {
            "drop_params": False,
            "set_verbose": False,
            "num_retries": 0,
            "use_chat_completions_url_for_anthropic_messages": True,
        },
    }


def codex_configuration(checkpoint: Checkpoint, selection: Selection, pin: ModelPin, catalog_path: Path) -> str:
    return "\n".join(
        (
            f"model = {json.dumps(selection.codex_model)}",
            'model_provider = "copilot_gateway"',
            f"model_catalog_json = {json.dumps(str(catalog_path))}",
            f"model_reasoning_effort = {json.dumps(checkpoint.reasoning_effort)}",
            f"model_context_window = {pin.context_window_tokens}",
            f"model_auto_compact_token_limit = {pin.max_input_tokens * 9 // 10}",
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
            "stream_max_retries = 0",
            "stream_idle_timeout_ms = 600000",
            "",
        )
    )


def codex_catalog(checkpoint: Checkpoint, pin: ModelPin, prompt: str) -> Mapping[str, JsonValue]:
    return {
        "models": [
            {
                "slug": pin.id,
                "display_name": f"{pin.id} (Copilot)",
                "description": "Explicitly qualified Copilot model",
                "default_reasoning_level": checkpoint.reasoning_effort,
                "supported_reasoning_levels": [
                    {"effort": effort, "description": f"{effort.capitalize()} reasoning"}
                    for effort in pin.reasoning_efforts
                ],
                "shell_type": "shell_command",
                "visibility": "list",
                "supported_in_api": True,
                "priority": 1,
                "availability_nux": None,
                "upgrade": None,
                "base_instructions": prompt,
                "supports_reasoning_summary_parameter": True,
                "default_reasoning_summary": "auto",
                "support_verbosity": False,
                "default_verbosity": None,
                "apply_patch_tool_type": "freeform",
                "truncation_policy": {"mode": "tokens", "limit": 10000},
                "supports_parallel_tool_calls": True,
                "context_window": pin.context_window_tokens,
                "max_context_window": pin.context_window_tokens,
                "auto_compact_token_limit": pin.max_input_tokens * 9 // 10,
                "effective_context_window_percent": pin.max_input_tokens * 100 // pin.context_window_tokens,
                "experimental_supported_tools": [],
                "input_modalities": ["text", "image"],
            }
        ]
    }


def claude_environment(selection: Selection) -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{selection.port}",
        "ANTHROPIC_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": selection.claude_model,
        "ANTHROPIC_DEFAULT_FABLE_MODEL": selection.claude_model,
        "ANTHROPIC_SMALL_FAST_MODEL": selection.claude_model,
        "CLAUDE_CODE_SUBAGENT_MODEL": selection.claude_model,
        "ENABLE_TOOL_SEARCH": "false",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }


def claude_configuration(selection: Selection) -> Mapping[str, JsonValue]:
    return {
        "model": selection.claude_model,
        "env": dict[str, JsonValue](claude_environment(selection)),
        "permissions": {"deny": ["WebSearch"]},
    }


def clean_environment(environ: Mapping[str, str]) -> dict[str, str]:
    allowed: Final = frozenset(
        (
            "HOME",
            "PATH",
            "USER",
            "LOGNAME",
            "SHELL",
            "SSH_AUTH_SOCK",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LANG",
            "TERM",
            "COLORTERM",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
        )
    )
    no_proxy: Final = ",".join(
        part
        for part in (environ.get("NO_PROXY", ""), environ.get("no_proxy", ""), "localhost", "127.0.0.1", "::1")
        if part
    )
    return {
        **{key: value for key, value in environ.items() if key in allowed or key.startswith("LC_")},
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }
