from dataclasses import dataclass
from typing import Final

from gateway_config import EmbeddingModel, GatewayModel, ModelPin, Problem
from pydantic import BaseModel, ConfigDict, ValidationError


class ModelLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    max_prompt_tokens: int | None = None
    max_output_tokens: int | None = None
    max_context_window_tokens: int | None = None


class ModelSupport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tool_calls: bool = False
    parallel_tool_calls: bool = False
    streaming: bool = False
    vision: bool = False
    reasoning_effort: tuple[str, ...] = ()


class Capabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str = "chat"
    limits: ModelLimits = ModelLimits()
    supports: ModelSupport = ModelSupport()


class ModelPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    state: str


class CatalogModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    name: str = ""
    supported_endpoints: tuple[str, ...] | None = None
    capabilities: Capabilities = Capabilities()
    policy: ModelPolicy | None = None

    def profile(self) -> GatewayModel | Problem:
        if self.capabilities.type in ("embedding", "embeddings"):
            try:
                return EmbeddingModel(id=self.id, display_name=self.name or self.id)
            except ValidationError:
                return Problem("Copilot advertised an invalid embedding model ID.")
        if self.capabilities.type != "chat":
            return Problem(f"Unrecognized model type for {self.id}: {self.capabilities.type}")
        endpoints: Final = ("/chat/completions",) if self.supported_endpoints is None else self.supported_endpoints
        if not frozenset(endpoints).intersection(("/responses", "/v1/responses", "/chat/completions")):
            return Problem(f"No supported inference endpoint was advertised for {self.id}.")
        limits: Final = self.capabilities.limits
        support: Final = self.capabilities.supports
        try:
            profile: Final = ModelPin.model_validate(
                {
                    "id": self.id,
                    "display_name": self.name or self.id,
                    "context_window_tokens": limits.max_context_window_tokens,
                    "max_input_tokens": limits.max_prompt_tokens,
                    "max_output_tokens": limits.max_output_tokens,
                    "reasoning_efforts": support.reasoning_effort,
                    "supported_endpoints": endpoints,
                    "supports_tools": support.tool_calls,
                    "supports_parallel_tools": support.parallel_tool_calls,
                    "supports_vision": support.vision,
                    "supports_streaming": support.streaming,
                }
            )
        except ValidationError:
            return Problem(f"Copilot advertised invalid or missing model capabilities for {self.id}.")
        if profile.max_input_tokens > profile.context_window_tokens or profile.safe_input_tokens <= 0:
            return Problem(f"Copilot advertised inconsistent token limits for {self.id}.")
        return profile


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    data: tuple[CatalogModel, ...]


@dataclass(frozen=True, slots=True)
class ModelDiscovery:
    models: tuple[GatewayModel, ...]
    disabled: tuple[str, ...]


def discover_models(catalog: tuple[CatalogModel, ...]) -> ModelDiscovery | Problem:
    eligible: Final = tuple(model for model in catalog if model.policy is None or model.policy.state == "enabled")
    disabled: Final = tuple(
        model.id for model in catalog if model.policy is not None and model.policy.state != "enabled"
    )
    if not eligible:
        return Problem("No enabled Copilot models were returned. Existing configuration was not changed.")
    if len(frozenset(model.id for model in catalog)) != len(catalog):
        return Problem("Copilot returned duplicate model IDs. Existing configuration was not changed.")
    profiles: Final = tuple(model.profile() for model in eligible)
    error: Final = next((profile for profile in profiles if isinstance(profile, Problem)), None)
    if error is not None:
        return error
    return ModelDiscovery(
        tuple(profile for profile in profiles if isinstance(profile, (ModelPin, EmbeddingModel))), disabled
    )


def validate_catalog(pins: tuple[ModelPin, ...], catalog: tuple[CatalogModel, ...]) -> Problem | None:
    return next(filter(None, (validate_model(pin, catalog) for pin in pins)), None)


def validate_model(pin: ModelPin, catalog: tuple[CatalogModel, ...]) -> Problem | None:
    entry: Final = next((entry for entry in catalog if entry.id == pin.id), None)
    if entry is None or (entry.policy is not None and entry.policy.state != "enabled"):
        return Problem(f"Copilot did not advertise the selected model {pin.id} as available. No fallback was selected.")
    profile: Final = entry.profile()
    if isinstance(profile, Problem):
        return profile
    if not isinstance(profile, ModelPin):
        return Problem(f"The selected model {pin.id} is not a conversation model.")
    if not frozenset(pin.supported_endpoints).issubset(profile.supported_endpoints):
        return Problem(f"Copilot did not advertise the selected endpoint support for {pin.id}.")
    if (
        pin.max_input_tokens > profile.max_input_tokens
        or pin.max_output_tokens > profile.max_output_tokens
        or pin.context_window_tokens > profile.context_window_tokens
    ):
        return Problem(f"The configured token limits exceed the current Copilot limits for {pin.id}.")
    if pin.supports_tools and not profile.supports_tools:
        return Problem(f"Copilot did not advertise tool support for {pin.id}.")
    if not frozenset(pin.reasoning_efforts).issubset(profile.reasoning_efforts):
        return Problem(f"The advertised reasoning levels do not match the selected model {pin.id}.")
    return None
