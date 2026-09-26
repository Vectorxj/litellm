import json
from pathlib import Path
from typing import Final

import httpx
import pytest
from gateway_catalog import Capabilities, CatalogModel, ModelLimits, ModelPolicy, ModelSupport, discover_models
from gateway_config import Checkpoint, EmbeddingModel, ModelPin, Problem, model_deployment, proxy_configuration

CHECKPOINT: Final = Checkpoint.model_validate_json(Path(__file__).with_name("checkpoint.json").read_bytes())
LIMITS: Final = ModelLimits(max_prompt_tokens=48000, max_output_tokens=16000, max_context_window_tokens=64000)
SUPPORT: Final = ModelSupport(tool_calls=True, parallel_tool_calls=True, streaming=True, vision=False)
RESPONSE_MODEL: Final = CatalogModel(
    id="gpt-response-fixture",
    name="Responses fixture",
    supported_endpoints=("/responses",),
    capabilities=Capabilities(limits=LIMITS, supports=SUPPORT),
)
CHAT_MODEL: Final = CatalogModel(
    id="chat-fixture",
    name="Chat fixture",
    supported_endpoints=("/chat/completions",),
    capabilities=Capabilities(limits=LIMITS, supports=SUPPORT),
)
CLAUDE_MODEL: Final = CatalogModel(
    id="claude-messages-fixture",
    name="Messages fixture",
    supported_endpoints=("/v1/messages", "/chat/completions"),
    capabilities=Capabilities(limits=LIMITS, supports=SUPPORT),
)
EMBEDDING_MODEL: Final = CatalogModel(id="embedding-fixture", capabilities=Capabilities(type="embeddings"))


def test_catalog_exposes_every_enabled_model_with_its_actual_api() -> None:
    disabled: Final = RESPONSE_MODEL.model_copy(
        update={"id": "disabled-fixture", "policy": ModelPolicy(state="disabled")}
    )
    result: Final = discover_models((RESPONSE_MODEL, CHAT_MODEL, CLAUDE_MODEL, EMBEDDING_MODEL, disabled))
    assert not isinstance(result, Problem)
    assert tuple(model.id for model in result.models) == (
        "gpt-response-fixture",
        "chat-fixture",
        "claude-messages-fixture",
        "embedding-fixture",
    )
    assert result.disabled == ("disabled-fixture",)
    deployments: Final = {model.id: model_deployment(CHECKPOINT, model) for model in result.models}
    responses: Final = deployments["gpt-response-fixture"]
    chat: Final = deployments["chat-fixture"]
    claude: Final = deployments["claude-messages-fixture"]
    embedding: Final = deployments["embedding-fixture"]
    assert responses["litellm_params"]["store"] is False
    assert "use_chat_completions_api" not in responses["litellm_params"]
    assert chat["litellm_params"]["use_chat_completions_api"] is True
    assert "store" not in chat["litellm_params"]
    assert claude["model_info"]["supported_endpoints"] == ["/v1/messages", "/chat/completions"]
    assert claude["litellm_params"]["use_chat_completions_api"] is True
    assert embedding["model_info"] == {"mode": "embedding"}
    assert "store" not in embedding["litellm_params"]
    assert not chat["model_info"]["supports_reasoning"]
    assert not chat["model_info"]["supports_vision"]
    assert len(proxy_configuration(CHECKPOINT, result.models)["model_list"]) == 4


def test_legacy_catalog_entries_use_chat_or_embeddings_without_inventing_limits() -> None:
    result: Final = discover_models(
        (
            CHAT_MODEL.model_copy(update={"supported_endpoints": None}),
            EMBEDDING_MODEL,
        )
    )
    assert not isinstance(result, Problem)
    chat, embedding = result.models
    assert isinstance(chat, ModelPin)
    assert chat.supported_endpoints == ("/chat/completions",)
    assert isinstance(embedding, EmbeddingModel)
    assert "context_window_tokens" not in embedding.model_dump()


@pytest.mark.parametrize(
    "entries",
    (
        (),
        (RESPONSE_MODEL, RESPONSE_MODEL),
        (CatalogModel(id="chat-missing-limits"),),
        (CHAT_MODEL.model_copy(update={"supported_endpoints": ()}),),
        (CatalogModel(id="unknown-kind", capabilities=Capabilities(type="unknown")),),
    ),
)
def test_invalid_discovery_is_explicit_instead_of_a_partial_catalog(entries: tuple[CatalogModel, ...]) -> None:
    result: Final = discover_models(entries)
    assert isinstance(result, Problem)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "surface", "expected_path"),
    (
        (RESPONSE_MODEL, "responses", "/responses"),
        (CHAT_MODEL, "responses", "/chat/completions"),
        (CLAUDE_MODEL, "responses", "/chat/completions"),
        (CLAUDE_MODEL, "messages", "/v1/messages"),
        (EMBEDDING_MODEL, "embeddings", "/embeddings"),
    ),
)
async def test_dispatch_sends_each_surface_to_the_advertised_upstream(
    entry: CatalogModel, surface: str, expected_path: str
) -> None:
    from openai import AsyncOpenAI

    import litellm
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

    profile: Final = entry.profile()
    assert not isinstance(profile, Problem)
    deployment: Final = model_deployment(CHECKPOINT, profile)
    params: Final = deployment["litellm_params"]
    assert isinstance(params, dict)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_path
        assert request.url.host == "copilot.example.test"
        data: Final = json.loads(request.content)
        assert data["model"] == entry.id
        assert "use_chat_completions_api" not in data
        if expected_path == "/v1/messages":
            assert "messages" in data and "input" not in data
            return httpx.Response(
                200,
                json={
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": entry.id,
                    "content": [{"type": "text", "text": "NATIVE_OK"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 2, "output_tokens": 2},
                },
            )
        if expected_path == "/embeddings":
            assert data["input"] == ["hello"]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": entry.id,
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        if expected_path == "/responses":
            assert "input" in data
            return httpx.Response(
                200,
                json={
                    "id": "resp_fixture",
                    "created_at": 1,
                    "model": entry.id,
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg_fixture",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "ROUTE_OK", "annotations": []}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                },
            )
        assert "messages" in data and "input" not in data
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_fixture",
                "object": "chat.completion",
                "created": 1,
                "model": entry.id,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ROUTE_OK"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    handler: Final = AsyncHTTPHandler()
    await handler.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        handler.client = http_client
        sdk: Final = AsyncOpenAI(
            api_key="fixture-key", base_url="https://copilot.example.test", http_client=http_client
        )
        request_params: Final = {
            **params,
            "api_key": "fixture-key",
            "api_base": "https://copilot.example.test",
            "model_info": deployment["model_info"],
        }
        if surface == "messages":
            response: Final = await litellm.anthropic.messages.acreate(
                **request_params, messages=[{"role": "user", "content": "hello"}], max_tokens=16, client=handler
            )
            assert response["content"][0]["text"] == "NATIVE_OK"
        elif surface == "embeddings":
            embeddings: Final = await litellm.aembedding(**request_params, input=["hello"], client=sdk)
            assert embeddings.data[0]["embedding"] == [0.1, 0.2]
        else:
            completion: Final = await litellm.aresponses(
                **request_params,
                input="hello",
                max_output_tokens=16,
                client=handler if expected_path == "/responses" else sdk,
            )
            assert completion.status == "completed"
            assert completion.output[0].content[0].text == "ROUTE_OK"
