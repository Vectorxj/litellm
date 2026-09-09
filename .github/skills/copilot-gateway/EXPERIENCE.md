# Qualification notes

Qualified on 2026-09-09 with real Copilot-backed inference, Codex 0.146.0, and Claude Code 2.1.220. No OpenAI or Anthropic upstream API key was used. Credentials, complete account catalogs, and raw private logs are intentionally absent from this directory

## Authentication and model discovery

The existing Copilot CLI OAuth token worked directly as a bearer token against `https://api.githubcopilot.com/models`. Discovery needed the Copilot CLI integration identity recorded in `checkpoint.json` to advertise the selected model. Neutral headers and the older VS Code identity did not advertise it in this environment

The final configuration uses the gateway's own `User-Agent: litellm-copilot-gateway/1`, which also advertised the same Astra entry when paired with the documented default CLI/SDK integration ID. The integration ID matters here; copying an alleged CLI version string or adding invented editor/device headers is unnecessary

Both clients' file-editing checks were repeated successfully with this final user-agent before publishing the checkpoint

The legacy `/copilot_internal/v2/token` exchange returned a non-JSON HTTP 403 here. That observation does not prove all CLI tokens are incompatible with that endpoint; it could also reflect endpoint or network policy. The working configuration therefore uses the validated direct OAuth path instead of manufacturing a cached session token or claiming an unlimited token lifetime

The selected model entry advertised:

```json
{
  "id": "gpt-6-astra",
  "version": "gpt-6-astra",
  "supported_endpoints": ["/responses", "ws:/responses"],
  "max_context_window_tokens": 1000000,
  "max_prompt_tokens": 872000,
  "max_output_tokens": 128000,
  "reasoning_effort": ["low", "medium", "high", "xhigh", "max"]
}
```

Streaming, function calls, parallel function calls, and image input were also advertised. Setup checks these capabilities rather than inferring them from an OpenAI model with a similar name. Catalog availability remains account-dependent

## A working text response was not enough

Both `/v1/responses` and `/v1/messages` returned HTTP 200 for simple text. Claude Code still failed on its first tool workflow with:

```text
The model's tool call could not be parsed (retry also failed).
```

A direct streaming Messages request showed valid JSON argument deltas but no corresponding `content_block_stop`. Copilot changed the Responses item ID between the added and done events. The direct Messages-to-Responses wrapper in the pinned LiteLLM revision keys block closure by item ID

The configured compatibility path uses `use_chat_completions_url_for_anthropic_messages: true` together with model mode `responses`. The final upstream call still goes to `/responses`, but LiteLLM's existing Responses-to-Chat iterator tracks tool calls by output index, and its Anthropic wrapper closes the blocks correctly. This is an existing supported conversion path, not a byte-rewriting middleware or a fake successful tool result

The focused regression test feeds changing item IDs through this conversion and checks complete, parseable tool arguments, matching block closure, and the final `tool_use` stop reason

Relevant source at the pinned revision:

https://github.com/BerriAI/litellm/blob/ee7c7e14f3dd7c4c3930a423440ec26427e2c554/litellm/llms/anthropic/experimental_pass_through/responses_adapters/streaming_iterator.py

https://github.com/BerriAI/litellm/blob/ee7c7e14f3dd7c4c3930a423440ec26427e2c554/litellm/completion_extras/litellm_responses_transformation/transformation.py

## Codex model metadata matters

The first Codex tool workflow completed, but it reported:

```text
Model metadata for `gpt-6-astra` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.
```

An explicit `model_catalog_json` resolved the diagnostic. It registers the observed input/context limits, reasoning levels, parallel tools, and the native freeform `apply_patch` interface. It uses the exact pinned release's official generic Codex prompt, downloaded without credentials and checked against the committed SHA-256, rather than an improvised replacement system prompt

The final Codex run read the marker, used native `apply_patch`, ran `cmp`, and returned the requested completion marker without a fallback-metadata diagnostic

Catalog contract and configuration schema:

https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/protocol/src/openai_models.rs

https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/core/config.schema.json

Pinned official generic prompt:

https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/models-manager/prompt.md

## Transport and dependency findings

The Copilot catalog advertised WebSocket support. A native WebSocket experiment reached an accepted connection on the local gateway but did not produce a usable generation after more than three minutes, and it was stopped. HTTP/SSE completed the same tool workflows. The committed configuration consequently sets `supports_websockets = false`; it does not assert that the upstream model lacks WebSocket capability

Installing only the repository's `proxy` extra let successful inference work, but an unauthenticated request returned HTTP 500 because the exception handler imported an absent `prisma` module. Including the locked `proxy-dev` group restored the correct HTTP 401. No database was configured, and the server does not inherit a `DATABASE_URL`

The pinned Claude Code npm package requires its postinstall step to replace a placeholder with the platform binary. Setup disables general npm lifecycle scripts, then runs that pinned package's inspected `install.cjs`. This installer links or copies the native optional dependency already verified by `package-lock.json`; it does not fetch an unpinned binary. Both client version outputs are checked after installation

## Real proof of operation

The curl commands in `README.md`, using the private header file, produced:

```text
Responses HTTP 200
model: gpt-6-astra
status: completed
text: COPILOT_GATEWAY_RESPONSES_OK

Messages HTTP 200
model: gpt-6-astra
stop_reason: end_turn
text: COPILOT_GATEWAY_MESSAGES_OK

Unauthenticated inference HTTP 401
```

The clients also completed real multi-turn file operations. In a disposable directory containing the README's marker file, the write checks were:

```bash
"$KIT/bootstrap.sh" codex -- exec --strict-config \
  --sandbox workspace-write --ephemeral --skip-git-repo-check --cd "$WORK" --json \
  'Read marker.txt using a tool. Use apply_patch to create codex-copy.txt with exactly the same contents. Do not use shell redirection to write files. Do not modify any other files or use the network. Finally reply COPILOT_CODEX_WRITE_OK.' \
  </dev/null

(
  cd "$WORK"
  "$KIT/bootstrap.sh" claude -- \
    --print 'Read marker.txt using the Read tool. Use the Edit tool to create claude-copy.txt with exactly the same contents. Do not modify any other files or use the network. Finally reply COPILOT_CLAUDE_WRITE_OK.' \
    --bare --no-session-persistence --output-format stream-json --verbose \
    --tools Read,Edit --allowedTools Read,Edit
)

cmp "$WORK/marker.txt" "$WORK/codex-copy.txt"
cmp "$WORK/marker.txt" "$WORK/claude-copy.txt"
```

Selected observed results:

```text
Codex:
  cat marker.txt: exit_code 0
  file_change: add codex-copy.txt, status completed
  cmp marker.txt codex-copy.txt: exit_code 0
  COPILOT_CODEX_WRITE_OK

Claude Code:
  model: gpt-6-astra
  Read: marker.txt
  Edit: claude-copy.txt, old_string ""
  COPILOT_CLAUDE_WRITE_OK
  is_error: false
  num_turns: 3

Both copied files matched the marker byte for byte.
```

This Claude Code build exposes `Edit`, not a separate `Write` tool, for the qualified file-creation workflow. Checking the actual emitted tool list caught that difference. A process exit code alone would not have detected the earlier run that correctly reported no available Write tool without creating a file

No approval-bypass flag was used. The read-only checks used read-only tool permissions, and the Codex write check was limited to its workspace-write sandbox

## Remaining boundaries

The observed catalog establishes the allowed context size; it is not a load test at one million tokens. Long-session compaction, image workflows, every reasoning level, and arbitrary MCP servers were not exhaustively exercised by these checks

Hosted WebSearch remains disabled. Deferred Tool Search remains disabled, so ordinary tools can be loaded up front without requiring Anthropic-specific deferred-tool semantics. Provider-native beta features and billing behavior are not assumed to survive cross-provider conversion unchanged

Anthropic does not officially support routing Claude Code to non-Claude models. This checkpoint is an empirically qualified LiteLLM integration, not an Anthropic support guarantee or a promise that Copilot's internal API will remain unchanged

## Operator references

Public prior art supports separating GitHub OAuth credentials from short-lived Copilot API credentials and treating the integration ID as part of model discovery. The following bridge research independently recorded a legacy catalog under the VS Code integration identity and newer models under the documented CLI/SDK default. These are version-specific observations, not a reason to bypass account policy or automatically switch identities after an error

https://github.com/hooyao/copilot-bridge/blob/main/docs/copilot-api-research.md

The same project's Codex protocol probes distinguish ordinary Responses from optional services such as compaction and report unsupported `service_tier` behavior. This kit accordingly omits service tiers, preserves stateless reasoning/tool replay, and qualifies complete client workflows instead of assuming every OpenAI feature exists

https://github.com/hooyao/copilot-bridge/blob/main/docs/codex-protocol-research.md

GitHub documents the CLI/SDK integration ID and supported credential types. The SDK is a supported agent-runtime integration, not automatically an OpenAI inference proxy. Its documentation does not turn this raw-CAPI gateway into a supported public API product

https://docs.github.com/en/copilot/how-tos/copilot-sdk/setup/multi-tenancy#integration-id

https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli

The official documentation favors explicit gateway credentials, correct API formats, preservation of streaming, and careful handling of provider-specific capabilities:

https://docs.litellm.ai/docs/providers/github_copilot

https://docs.litellm.ai/docs/tutorials/openai_codex

https://code.claude.com/docs/en/llm-gateway-connect

https://code.claude.com/docs/en/llm-gateway-protocol

https://code.claude.com/docs/en/model-config

https://code.claude.com/docs/en/agent-sdk/tool-search
