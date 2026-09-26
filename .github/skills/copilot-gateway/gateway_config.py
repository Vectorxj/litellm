from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue

API_BASE: Final = "https://api.githubcopilot.com"
UPSTREAM_TOKEN_ENV: Final = "COPILOT_GATEWAY_UPSTREAM_TOKEN"
PROXY_KEY_ENV: Final = "COPILOT_GATEWAY_PROXY_KEY"


class ModelPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")
    kind: Literal["chat"] = "chat"
    display_name: str = ""
    context_window_tokens: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    reasoning_efforts: tuple[Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"], ...] = ()
    supported_endpoints: tuple[str, ...] = ("/responses",)
    supports_tools: bool = True
    supports_parallel_tools: bool = True
    supports_vision: bool = True
    supports_streaming: bool = True

    @property
    def native_responses(self) -> bool:
        return bool(frozenset(self.supported_endpoints).intersection(("/responses", "/v1/responses")))

    @property
    def native_messages(self) -> bool:
        return "/v1/messages" in self.supported_endpoints

    @property
    def safe_input_tokens(self) -> int:
        return min(self.max_input_tokens, self.context_window_tokens - self.max_output_tokens)


class EmbeddingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")
    kind: Literal["embedding"] = "embedding"
    display_name: str = ""


GatewayModel: TypeAlias = ModelPin | EmbeddingModel


class Checkpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    checkpoint: str
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    uv_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    python_version: str
    uv_version: str
    codex_version: str
    native_codex_version: str | None = None
    codex_prompt_url: str = Field(
        pattern=r"^https://raw\.githubusercontent\.com/openai/codex/[^/]+/codex-rs/[^?#]+\.md$"
    )
    codex_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claude_version: str
    native_claude_version: str
    default_codex_model: str
    default_claude_model: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"]
    upstream_headers: Mapping[str, str]
    models: tuple[ModelPin, ...]


class Selection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    codex_model: str
    claude_model: str
    claude_only: bool = False
    port: int = Field(ge=1024, le=65535)
    checkpoint: str
    checkpoint_sha256: str
    catalog_models: tuple[GatewayModel, ...] = ()


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
    requested: Final = frozenset(
        (selection.claude_model,) if selection.claude_only else (selection.codex_model, selection.claude_model)
    )
    models: Final = available_models(checkpoint, selection)
    available: Final = frozenset(model.id for model in models if isinstance(model, ModelPin))
    missing: Final = requested - available
    if missing:
        return Problem(
            f"Selected conversation models are not in this catalog: {', '.join(sorted(missing))}. "
            "Refresh the authorized catalog; no fallback was selected."
        )
    if any(
        model.max_input_tokens > model.context_window_tokens or model.safe_input_tokens <= 0 or not model.supports_tools
        for model in models
        if isinstance(model, ModelPin) and model.id in requested
    ):
        return Problem("Selected models must have valid context limits and advertised tool support.")
    return tuple(model for model in models if isinstance(model, ModelPin) and model.id in requested)


def available_models(checkpoint: Checkpoint, selection: Selection) -> tuple[GatewayModel, ...]:
    return selection.catalog_models or checkpoint.models


def model_deployment(checkpoint: Checkpoint, model: GatewayModel) -> dict[str, JsonValue]:
    common: Final[dict[str, JsonValue]] = {
        "model": f"openai/{model.id}",
        "api_base": API_BASE,
        "api_key": f"os.environ/{UPSTREAM_TOKEN_ENV}",
        "extra_headers": dict(checkpoint.upstream_headers),
        "timeout": 600,
        "max_retries": 0,
    }
    if isinstance(model, EmbeddingModel):
        return {
            "model_name": model.id,
            "litellm_params": common,
            "model_info": {"mode": "embedding"},
        }
    return {
        "model_name": model.id,
        "litellm_params": {
            **common,
            **({"store": False} if model.native_responses else {"use_chat_completions_api": True}),
        },
        "model_info": {
            "mode": "responses" if model.native_responses else "chat",
            "max_input_tokens": model.max_input_tokens,
            "max_output_tokens": model.max_output_tokens,
            "supports_function_calling": model.supports_tools,
            "supports_parallel_function_calling": model.supports_parallel_tools,
            "supports_reasoning": bool(model.reasoning_efforts),
            "supports_vision": model.supports_vision,
            "supports_native_streaming": model.supports_streaming,
            "supported_endpoints": list(model.supported_endpoints),
        },
    }


def proxy_configuration(checkpoint: Checkpoint, pins: tuple[GatewayModel, ...]) -> Mapping[str, JsonValue]:
    return {
        "model_list": [model_deployment(checkpoint, model) for model in pins],
        "general_settings": {
            "master_key": f"os.environ/{PROXY_KEY_ENV}",
            "disable_spend_logs": True,
        },
        "litellm_settings": {
            "callbacks": ["gateway_stop_sequences.handler"],
            "drop_params": False,
            "set_verbose": False,
            "num_retries": 0,
            "use_chat_completions_url_for_anthropic_messages": True,
        },
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
