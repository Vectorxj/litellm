from collections.abc import Mapping
from typing import Final

import pytest
from gateway_stop_sequences import GatewayStopSequences, apply_stop_sequences
from pydantic import JsonValue, TypeAdapter

from litellm.caching.caching import DualCache
from litellm.proxy._types import UserAPIKeyAuth
from litellm.types.llms.anthropic_messages.anthropic_response import AnthropicMessagesResponse

RESPONSE: Final[dict[str, JsonValue]] = {
    "id": "msg_fixture",
    "type": "message",
    "role": "assistant",
    "model": "gpt-6-astra",
    "content": [{"type": "text", "text": "<block>deny</block>ignored"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 100, "output_tokens": 12},
}
REQUEST: Final[dict[str, object]] = {"model": "gpt-6-astra", "stop_sequences": ["</block>"]}
AUTH: Final = UserAPIKeyAuth()
RESPONSE_ADAPTER: Final = TypeAdapter(AnthropicMessagesResponse)


@pytest.mark.asyncio
async def test_classifier_preserves_real_decision_and_stop_semantics() -> None:
    handler: Final = GatewayStopSequences()
    prepared: Final = await handler.async_pre_call_hook(
        AUTH, DualCache(), {**REQUEST, "additional_drop_params": ["user"]}, "anthropic_messages"
    )
    assert prepared is not None
    assert prepared["additional_drop_params"] == ["user", "stop"]
    assert prepared["stop_sequences"] == ["</block>"]
    response: Final = RESPONSE_ADAPTER.validate_python(RESPONSE)
    result: Final = await handler.async_post_call_success_hook(prepared, AUTH, response)
    assert isinstance(result, dict)
    assert result["content"] == [{"type": "text", "text": "<block>deny"}]
    assert result["stop_reason"] == "stop_sequence"
    assert result["stop_sequence"] == "</block>"
    assert result["usage"] == RESPONSE["usage"]
    assert RESPONSE_ADAPTER.dump_python(response, mode="json") == RESPONSE
    assert "additional_drop_params" not in REQUEST
    assert await handler.async_pre_call_hook(AUTH, DualCache(), REQUEST, "completion") is None


@pytest.mark.parametrize(
    "changes",
    (
        {"stream": True},
        {"tools": [{"name": "Bash"}]},
        {"stop_sequences": []},
        {"model": "claude-provider-model"},
    ),
)
@pytest.mark.asyncio
async def test_unqualified_requests_are_not_modified(changes: Mapping[str, object]) -> None:
    handler: Final = GatewayStopSequences()
    data: Final = {**REQUEST, **changes}
    assert await handler.async_pre_call_hook(AUTH, DualCache(), data, "anthropic_messages") is None
    response: Final = RESPONSE_ADAPTER.validate_python(RESPONSE)
    assert await handler.async_post_call_success_hook(data, AUTH, response) is response


@pytest.mark.parametrize(
    ("parts", "sequences", "expected", "stop"),
    (
        (("<block>deny</bl", "ock>ignored"), ("</block>",), ("<block>deny",), "</block>"),
        (("abcSECONDignoredFIRST",), ("FIRST", "SECOND"), ("abc",), "SECOND"),
        (("STOPignored",), ("STOP",), ("",), "STOP"),
        (("abc", "STOPignored"), ("STOP",), ("abc", ""), "STOP"),
        (("keep-no-marker",), ("STOP",), (), None),
    ),
)
def test_stop_boundaries_and_earliest_match(
    parts: tuple[str, ...], sequences: tuple[str, ...], expected: tuple[str, ...], stop: str | None
) -> None:
    response: Final[dict[str, JsonValue]] = {
        **RESPONSE,
        "content": [{"type": "text", "text": part} for part in parts],
    }
    result: Final = apply_stop_sequences(response, sequences)
    if stop is None:
        assert result is None
        return
    assert result is not None
    assert result["content"] == [{"type": "text", "text": part} for part in expected]
    assert result["stop_sequence"] == stop


def test_stop_does_not_match_reasoning_or_change_billing_usage() -> None:
    response: Final[dict[str, JsonValue]] = {
        **RESPONSE,
        "content": [
            {"type": "thinking", "thinking": "</block>reasoning", "signature": "fixture"},
            {"type": "text", "text": "allow"},
        ],
    }
    assert apply_stop_sequences(response, ("</block>",)) is None
