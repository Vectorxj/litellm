from collections.abc import Mapping
from itertools import accumulate
from typing import Annotated, Final

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from litellm.caching.caching import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral, LLMResponseTypes

JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
DROP_PARAMETERS: Final = TypeAdapter(tuple[str, ...])
CONTENT_BLOCKS: Final = TypeAdapter(tuple[dict[str, JsonValue], ...])
TEXT: Final = TypeAdapter(str)


class StopRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    model: str
    stop_sequences: tuple[Annotated[str, Field(min_length=1)], ...] = ()
    stream: bool = False
    tools: tuple[JsonValue, ...] | None = None

    @property
    def needs_emulation(self) -> bool:
        return self.model.startswith("gpt-") and bool(self.stop_sequences) and not self.stream and not self.tools


def apply_stop_sequences(response: Mapping[str, JsonValue], sequences: tuple[str, ...]) -> dict[str, JsonValue] | None:
    blocks: Final = CONTENT_BLOCKS.validate_python(response["content"])
    text_blocks: Final = tuple(
        (index, TEXT.validate_python(block["text"]))
        for index, block in enumerate(blocks)
        if block.get("type") == "text"
    )
    text: Final = "".join(text for _, text in text_blocks)
    matches: Final = tuple(
        (position, order, sequence)
        for order, sequence in enumerate(sequences)
        if (position := text.find(sequence)) >= 0
    )
    if not matches:
        return None
    position, _, sequence = min(matches)
    starts: Final = accumulate((len(text) for _, text in text_blocks), initial=0)
    block_index, block_text, offset = next(
        (index, text, position - start)
        for (index, text), start in zip(text_blocks, starts)
        if start + len(text) > position
    )
    return {
        **response,
        "content": [*blocks[:block_index], {**blocks[block_index], "text": block_text[:offset]}],
        "stop_reason": "stop_sequence",
        "stop_sequence": sequence,
    }


class GatewayStopSequences(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: Mapping[str, object],
        call_type: CallTypesLiteral,
    ) -> dict[str, object] | None:
        if call_type != "anthropic_messages" or not data.get("stop_sequences"):
            return None
        request: Final = StopRequest.model_validate(data)
        if not request.needs_emulation:
            return None
        configured_drops: Final = data.get("additional_drop_params")
        dropped: Final = DROP_PARAMETERS.validate_python(() if configured_drops is None else configured_drops)
        return {**data, "additional_drop_params": list(dict.fromkeys((*dropped, "stop")))}

    async def async_post_call_success_hook(
        self,
        data: Mapping[str, object],
        user_api_key_dict: UserAPIKeyAuth,
        response: LLMResponseTypes,
    ) -> dict[str, JsonValue] | LLMResponseTypes:
        if not data.get("stop_sequences") or not isinstance(response, dict):
            return response
        request: Final = StopRequest.model_validate(data)
        if not request.needs_emulation:
            return response
        result: Final = apply_stop_sequences(
            JSON_OBJECT.validate_python(jsonable_encoder(response)), request.stop_sequences
        )
        return response if result is None else result


handler: Final = GatewayStopSequences()
