import hashlib
import json
import platform
import stat
import sys
from pathlib import Path
from typing import Final

import httpx
import pytest
import tomllib
from gateway_config import (
    API_BASE,
    UPSTREAM_TOKEN_ENV,
    Checkpoint,
    Paths,
    Problem,
    Selection,
    clean_environment,
    selected_pins,
)
from pydantic import JsonValue, SecretStr

from gateway import (
    Capabilities,
    CatalogModel,
    ModelLimits,
    ModelSupport,
    Options,
    configure,
    fetch_catalog,
    fetch_codex_prompt,
    parse_options,
    private_directory,
    provided_token,
    validate_catalog,
    verify_client,
    write_private,
)

KIT: Final = Path(__file__).resolve().parent
CHECKPOINT_BYTES: Final = (KIT / "checkpoint.json").read_bytes()
CHECKPOINT: Final = Checkpoint.model_validate_json(CHECKPOINT_BYTES)
TOKEN: Final = SecretStr("gho_test_fixture")
SELECTION: Final = Selection(
    codex_model="gpt-6-astra",
    claude_model="gpt-6-astra",
    port=14000,
    checkpoint=CHECKPOINT.checkpoint,
    checkpoint_sha256=hashlib.sha256(CHECKPOINT_BYTES).hexdigest(),
)
CATALOG: Final = tuple(
    CatalogModel(
        id=pin.id,
        supported_endpoints=("/responses",),
        capabilities=Capabilities(
            limits=ModelLimits(
                max_prompt_tokens=pin.max_input_tokens,
                max_output_tokens=pin.max_output_tokens,
                max_context_window_tokens=pin.context_window_tokens,
            ),
            supports=ModelSupport(
                tool_calls=True,
                parallel_tool_calls=True,
                streaming=True,
                vision=True,
                reasoning_effort=pin.reasoning_efforts,
            ),
        ),
    )
    for pin in CHECKPOINT.models
)


def test_configuration_routes_both_clients_and_keeps_credentials_private(tmp_path: Path) -> None:
    paths: Final = Paths(kit=KIT, state=tmp_path / "gateway")
    assert configure(paths, CHECKPOINT, SELECTION, TOKEN, CATALOG, "Verified test prompt") is None
    proxy: Final[dict[str, JsonValue]] = json.loads(paths.proxy_config.read_text())
    codex: Final = tomllib.loads((paths.state / "codex/config.toml").read_text())
    claude: Final[dict[str, JsonValue]] = json.loads((paths.state / "claude/settings.json").read_text())
    key: Final = paths.key.read_text()
    assert paths.token.read_text().strip() == TOKEN.get_secret_value()
    assert key.startswith("sk-")
    assert proxy["model_list"] == [
        {
            "model_name": pin.id,
            "litellm_params": {
                "model": f"openai/{pin.id}",
                "api_base": API_BASE,
                "api_key": f"os.environ/{UPSTREAM_TOKEN_ENV}",
                "extra_headers": dict(CHECKPOINT.upstream_headers),
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
        for pin in CHECKPOINT.models
    ]
    settings: Final = proxy["litellm_settings"]
    assert isinstance(settings, dict)
    assert settings["use_chat_completions_url_for_anthropic_messages"] is True
    assert codex["model"] == SELECTION.codex_model
    model_catalog: Final[dict[str, JsonValue]] = json.loads((paths.state / "codex/model-catalog.json").read_text())
    models: Final = model_catalog["models"]
    assert isinstance(models, list) and len(models) == 1
    metadata: Final = models[0]
    assert isinstance(metadata, dict)
    assert metadata["slug"] == SELECTION.codex_model
    assert metadata["base_instructions"] == "Verified test prompt"
    assert metadata["context_window"] == CHECKPOINT.models[0].context_window_tokens
    assert metadata["supported_reasoning_levels"] == [
        {"effort": effort, "description": f"{effort.capitalize()} reasoning"}
        for effort in CHECKPOINT.models[0].reasoning_efforts
    ]
    assert codex["model_providers"]["copilot_gateway"]["base_url"] == "http://127.0.0.1:14000/v1"
    assert codex["model_providers"]["copilot_gateway"]["wire_api"] == "responses"
    assert codex["model_providers"]["copilot_gateway"]["supports_websockets"] is False
    assert codex["check_for_update_on_startup"] is False
    assert codex["model_auto_compact_token_limit"] < codex["model_context_window"]
    assert claude["model"] == SELECTION.claude_model
    claude_env: Final = claude["env"]
    assert isinstance(claude_env, dict)
    assert claude_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:14000"
    for family in ("SONNET", "OPUS", "HAIKU", "FABLE"):
        assert claude_env[f"ANTHROPIC_DEFAULT_{family}_MODEL"] == SELECTION.claude_model
    assert claude_env["CLAUDE_CODE_SUBAGENT_MODEL"] == SELECTION.claude_model
    assert claude_env["ENABLE_TOOL_SEARCH"] == "false"
    assert claude_env["DISABLE_AUTOUPDATER"] == "1"
    config_contents: Final = tuple(
        path.read_text()
        for path in (paths.proxy_config, paths.state / "codex/config.toml", paths.state / "claude/settings.json")
    )
    assert all(TOKEN.get_secret_value() not in content for content in config_contents)
    assert all(key.strip() not in content for content in config_contents)
    for path in paths.state.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert configure(paths, CHECKPOINT, SELECTION, TOKEN, CATALOG, "Verified test prompt") is None
    assert paths.key.read_text() == key
    assert json.loads(paths.proxy_config.read_text()) == proxy


def test_unavailable_model_is_rejected_before_writing_credentials(tmp_path: Path) -> None:
    paths: Final = Paths(kit=KIT, state=tmp_path / "gateway")
    result: Final = configure(paths, CHECKPOINT, SELECTION, TOKEN, (), "Verified test prompt")
    assert isinstance(result, Problem)
    assert "No fallback" in result.message
    assert not paths.state.exists()


def test_unpinned_mapping_is_rejected() -> None:
    selection: Final = SELECTION.model_copy(update={"claude_model": "gpt-unqualified"})
    result: Final = selected_pins(CHECKPOINT, selection)
    assert isinstance(result, Problem)
    assert "gpt-unqualified" in result.message


@pytest.mark.parametrize(
    ("model", "expected"),
    (
        (
            CATALOG[0].model_copy(update={"supported_endpoints": ("/chat/completions",)}),
            "Responses support",
        ),
        (
            CATALOG[0].model_copy(update={"capabilities": Capabilities(supports=CATALOG[0].capabilities.supports)}),
            "token limits",
        ),
        (
            CATALOG[0].model_copy(
                update={
                    "capabilities": Capabilities(
                        limits=ModelLimits(max_prompt_tokens=1, max_output_tokens=1, max_context_window_tokens=1),
                        supports=CATALOG[0].capabilities.supports,
                    )
                }
            ),
            "exceed",
        ),
    ),
)
def test_endpoint_and_context_limits_must_be_advertised(model: CatalogModel, expected: str) -> None:
    result: Final = validate_catalog(CHECKPOINT.models, (model,))
    assert isinstance(result, Problem)
    assert expected in result.message


def test_catalog_uses_explicit_token_and_validates_the_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{API_BASE}/models"
        assert request.headers["authorization"] == f"Bearer {TOKEN.get_secret_value()}"
        assert request.headers["user-agent"] == "fixture-gateway"
        return httpx.Response(200, json={"data": [entry.model_dump(mode="json") for entry in CATALOG]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result: Final = fetch_catalog(client, TOKEN, {"User-Agent": "fixture-gateway"})
    assert result == CATALOG


@pytest.mark.parametrize("status", (302, 401, 403, 429, 500))
def test_catalog_errors_never_echo_tokens_or_follow_redirects(status: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.githubcopilot.com"
        return httpx.Response(
            status,
            text=TOKEN.get_secret_value(),
            headers={"location": "https://untrusted.invalid/models"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result: Final = fetch_catalog(client, TOKEN, {})
    assert isinstance(result, Problem)
    assert str(status) in result.message
    assert TOKEN.get_secret_value() not in result.message


def test_invalid_catalog_does_not_become_an_empty_success() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="invalid"))) as client:
        result: Final = fetch_catalog(client, TOKEN, {})
    assert isinstance(result, Problem)
    assert "unrecognized model catalog" in result.message


def test_codex_prompt_must_match_its_checksum_and_never_receives_auth(tmp_path: Path) -> None:
    prompt: Final = b"Official pinned prompt fixture"
    checkpoint: Final = CHECKPOINT.model_copy(update={"codex_prompt_sha256": hashlib.sha256(prompt).hexdigest()})
    paths: Final = Paths(kit=KIT, state=tmp_path / "state")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "raw.githubusercontent.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=prompt)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert fetch_codex_prompt(client, paths, checkpoint) == prompt.decode()
        mismatch: Final = fetch_codex_prompt(client, paths, CHECKPOINT)
    assert isinstance(mismatch, Problem)
    assert "checksum" in mismatch.message


def test_token_is_required_and_never_accepted_as_a_cli_value() -> None:
    with pytest.raises(SystemExit) as missing_token:
        parse_options(("setup", "--claude-model", "gpt-6-astra"))
    assert missing_token.value.code == 2
    with pytest.raises(SystemExit) as missing_mapping:
        parse_options(("setup", "--token-env", "TEST_TOKEN"))
    assert missing_mapping.value.code == 2
    result: Final = provided_token(Options(command="catalog", token_env="TEST_TOKEN"), {})
    assert isinstance(result, Problem)
    assert (
        provided_token(Options(command="catalog", token_env="TEST_TOKEN"), {"TEST_TOKEN": TOKEN.get_secret_value()})
        == TOKEN
    )


def test_token_file_requires_private_permissions_and_rejects_temporary_tokens(tmp_path: Path) -> None:
    token_file: Final = tmp_path / "token"
    token_file.write_text(TOKEN.get_secret_value())
    token_file.chmod(0o644)
    options: Final = Options(command="catalog", token_file=token_file)
    assert isinstance(provided_token(options, {}), Problem)
    token_file.chmod(0o600)
    assert provided_token(options, {}) == TOKEN
    token_file.write_text("tid=temporary-copilot-token")
    assert isinstance(provided_token(options, {}), Problem)


def test_private_writes_refuse_symlinks_without_changing_the_target(tmp_path: Path) -> None:
    target: Final = tmp_path / "existing"
    target.write_text("unchanged")
    link: Final = tmp_path / "link"
    link.symlink_to(target)
    assert isinstance(write_private(link, "replacement"), Problem)
    assert isinstance(private_directory(link), Problem)
    assert target.read_text() == "unchanged"


def test_client_version_drift_is_rejected() -> None:
    executable: Final = Path(sys.executable)
    assert verify_client(executable, f"Python {platform.python_version()}", {}) is None
    result: Final = verify_client(executable, "Python unexpected-version", {})
    assert isinstance(result, Problem)
    assert "pinned client" in result.message


def test_client_environment_excludes_upstream_credentials_and_existing_client_overrides() -> None:
    result: Final = clean_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/example",
            "HTTPS_PROXY": "http://localhost:8080",
            "NO_PROXY": "example.invalid",
            "GITHUB_TOKEN": "credential",
            "COPILOT_GITHUB_TOKEN": TOKEN.get_secret_value(),
            UPSTREAM_TOKEN_ENV: TOKEN.get_secret_value(),
            "ANTHROPIC_API_KEY": "other-provider",
            "ANTHROPIC_BASE_URL": "https://other-provider.invalid",
            "CODEX_HOME": "/old/profile",
            "CLAUDE_CONFIG_DIR": "/old/profile",
            "DATABASE_URL": "private-database",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://telemetry.invalid",
            "CLAUDECODE": "1",
        }
    )
    assert result == {
        "PATH": "/usr/bin",
        "HOME": "/home/example",
        "HTTPS_PROXY": "http://localhost:8080",
        "NO_PROXY": "example.invalid,localhost,127.0.0.1,::1",
        "no_proxy": "example.invalid,localhost,127.0.0.1,::1",
    }


def test_compatibility_stream_closes_tool_blocks_when_copilot_changes_item_ids() -> None:
    from litellm.completion_extras.litellm_responses_transformation.transformation import (
        OpenAiResponsesToChatCompletionStreamIterator,
    )
    from litellm.llms.anthropic.experimental_pass_through.adapters.streaming_iterator import AnthropicStreamWrapper
    from litellm.types.utils import ModelResponseStream

    arguments: Final = '{"file_path":"marker.txt"}'
    tool: Final = {
        "type": "function_call",
        "call_id": "call_fixture",
        "name": "Read",
        "arguments": arguments,
        "status": "completed",
    }
    upstream: Final = (
        {"type": "response.created", "response": {"id": "resp_fixture"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**tool, "id": "item_start", "arguments": "", "status": "in_progress"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "item_id": "item_delta",
            "delta": arguments,
        },
        {"type": "response.output_item.done", "output_index": 0, "item": {**tool, "id": "item_end"}},
        {
            "type": "response.completed",
            "response": {
                "id": "resp_fixture",
                "model": "gpt-6-astra",
                "created_at": 1,
                "object": "response",
                "status": "completed",
                "output": [{**tool, "id": "item_end"}],
                "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
            },
        },
    )
    bridge: Final = OpenAiResponsesToChatCompletionStreamIterator(
        streaming_response=iter(json.dumps(event) for event in upstream), sync_stream=True
    )
    stream: Final = AnthropicStreamWrapper(
        completion_stream=(chunk for chunk in bridge if isinstance(chunk, ModelResponseStream)),
        model="gpt-6-astra",
    )
    events: Final = tuple(stream)
    starts: Final = tuple(
        event
        for event in events
        if event["type"] == "content_block_start" and event["content_block"]["type"] == "tool_use"
    )
    stops: Final = tuple(
        event for event in events if event["type"] == "content_block_stop" and event["index"] == starts[0]["index"]
    )
    assert len(starts) == len(stops) == 1
    assert starts[0]["content_block"]["name"] == "Read"
    assert starts[0]["content_block"]["id"] == "call_fixture"
    assert starts[0]["index"] == stops[0]["index"]
    encoded: Final = "".join(
        event["delta"]["partial_json"]
        for event in events
        if event["type"] == "content_block_delta" and event["delta"]["type"] == "input_json_delta"
    )
    assert json.loads(encoded) == {"file_path": "marker.txt"}
    assert any(event["type"] == "message_delta" and event["delta"]["stop_reason"] == "tool_use" for event in events)
    assert events[-1]["type"] == "message_stop"
