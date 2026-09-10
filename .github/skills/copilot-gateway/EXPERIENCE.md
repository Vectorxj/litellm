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

### Recovering a prematurely closed Codex stream

On 2026-09-10 at 08:51:19 UTC, a native Codex session reported:

```text
stream disconnected before completion: stream closed before response.completed
```

At the same timestamp, the gateway logged `httpx.ReadError: Connection closed` while reading its upstream Responses stream. No completed response was available. The client still used HTTP/SSE with a 600,000 ms idle timeout, so there was no evidence to justify switching transports or increasing that timeout

The kit had explicitly set `stream_max_retries = 0`. This disabled Codex's own recovery for a generation interrupted after the HTTP response began. `request_max_retries = 2` does not cover that case. The fix restores `stream_max_retries = 5`, the documented source default in both the pinned Codex 0.146.0 and the installed native Codex 0.154.0. The native and isolated profiles were updated without changing models, credentials, client versions, or the running server

Recovery was checked through a temporary loopback relay to the real gateway. On the first generation, the relay forwarded genuine Copilot SSE events through the first text delta and then closed the stream without forwarding `response.completed`. Subsequent requests were forwarded normally. The relay did not invent model output, completion events, or a successful tool result

| Client | Stream retries | Requests observed | Real completion events delivered | Outcome |
|---|---:|---:|---:|---|
| Native Codex 0.154.0 | 0 | 1 | 0 | Original error reproduced, exit code 1 |
| Native Codex 0.154.0 | 5 | 2 | 1 | Automatically recovered, exit code 0 |
| Pinned Codex 0.146.0 | 5 | 2 | 1 | Automatically recovered, exit code 0 |

The successful runs returned the requested `STREAM_RECOVERY_OK` text and a Codex `turn.completed` event. This verifies recovery from the observed class of failure, not a guarantee that Copilot or network connections never fail. A persistent outage still exhausts the bounded attempts. Codex owns the retry and its conversation/tool state; no server-side replay or fabricated `response.completed` was added

Current source for the bounded default and conversation-aware retry loop:

https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/model-provider-info/src/lib.rs

https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/session/turn.rs

The source checkpoint for the packaged client has the same retry default:

https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/model-provider-info/src/lib.rs

#### Recurrence after retries were enabled

A later failure on 2026-09-10 did occur in a newly started Codex 0.154.0 process with all five recovery attempts active. Between 10:04:35 and 10:05:37 UTC, six requests ended without a terminal Responses event. The client logged five retries and then failed. This rules out stale configuration for that recurrence; bounded retries mitigate transient failures but do not fix every upstream or protocol failure

The user reported the matching public issue:

https://github.com/caozhiyuan/copilot-api/issues/298

That issue distinguishes client WebSocket settings from the other proxy's server-side `useResponsesApiWebSocket` setting. Several commenters reported improvement after disabling the server-side transport. This kit already uses HTTP/SSE on both hops: Codex has `supports_websockets = false`, and LiteLLM's ordinary Responses handler calls its async HTTP client's `post` method. Adding a setting belonging to another proxy would not change that code path

Some comments also suggest a `codex/` model mapping. In that implementation the Codex service uses `chatgpt.com/backend-api` and separate ChatGPT credentials. It is not an equivalent Copilot route and must not be introduced as a silent fallback

The linked request-metadata issue was also checked:

https://github.com/caozhiyuan/copilot-api/issues/308

The inspected request did not contain its unsupported `internal_chat_message_metadata_passthrough` field. Removing unrelated metadata without evidence would not address the observed failure

At investigation time, a synthetic 320,027-token request completed both through this gateway and directly through Copilot. Synthetic tool histories with 602 and 902 input items also completed. A prepared request reconstructed from a private copy of the affected history subsequently completed in both streaming and non-streaming modes, including a streamed generation with its original output-limit behavior. Returned tool calls were not executed, the original session was not modified, and private request captures were not committed

These checks did not reproduce the remaining failure reliably and do not establish its root cause. Do not describe it as permanently fixed, disable reasoning, reduce the advertised context window, change providers, fabricate completion events, or keep increasing retries on this evidence alone. A recurrence needs request-correlated upstream event types, terminal/error metadata, and transport information, without logging prompt content or credentials

### Runtime dependencies

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

## Standard native client configuration

The `configure-clients` mode was qualified against the same running server using the installed native binaries, without a launcher, exported API key, explicit model override, or Claude `--bare` mode

Codex 0.146.0 supports command-backed provider authentication through `model_providers.<name>.auth`. Its auth implementation captures stdout and trims it as the bearer token. Claude Code's `apiKeyHelper` supports the corresponding private-file workflow. Both generated configurations use `/bin/cat` to read the gateway's private proxy key, so no token needs to be copied into TOML or JSON

https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/login/src/auth/external_bearer.rs

https://code.claude.com/docs/en/llm-gateway-connect#rotate-credentials-with-apikeyhelper

The original standard configuration files were backed up before switching the default backend. Previous Codex provider definitions, project trust entries, sandbox/approval preferences, unrelated Claude settings, and existing permission rules survived. Native clients can update their own project history while running; that is separate from the configuration merge

The native tool checks used these commands from a disposable workspace containing `marker.txt`:

```bash
codex exec --strict-config --sandbox workspace-write --ephemeral \
  --skip-git-repo-check --cd "$WORK" --json \
  'Read marker.txt using a tool. Use apply_patch to create native-codex.txt containing exactly the same contents. Do not modify other files or use the network. Reply NATIVE_CODEX_OK when done.' \
  </dev/null

(
  cd "$WORK"
  claude --print \
    'Read marker.txt using Read. Use Edit to create native-claude.txt containing exactly the same contents. Do not modify other files or use the network. Reply NATIVE_CLAUDE_OK when done.' \
    --no-session-persistence --output-format stream-json --verbose \
    --tools Read,Edit --allowedTools Read,Edit \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}'
)
```

Both files matched the marker byte for byte. Codex emitted a native `file_change` event and `NATIVE_CODEX_OK`; Claude Code used Read/Edit and returned `NATIVE_CLAUDE_OK` with `is_error: false`. The running gateway recorded four Responses requests and four Messages requests during these checks

The empty MCP configuration scoped the smoke check to local file tools; it is not part of normal client configuration. A second `configure-clients` run produced no additional backup or setting change

## Native Claude-only setup

Qualified on 2026-09-09 with the native Claude Code 2.1.266 updater release and the same authorized `gpt-6-astra` catalog limits. The installed client was updated with `claude update`. At qualification time, npm advertised 2.1.258 and returned HTTP 404 for the exact 2.1.266 package, so replacing the package lock with that native version would not reproduce a working installation

The checkpoint records 2.1.266 separately as `native_claude_version`. The existing npm client pins and lock remain unchanged. `setup --claude-only` checks the native executable, generates only the server and Claude profile, and does not download a Codex prompt or install npm clients. The saved selection makes `configure-clients` inspect and merge only Claude settings

The native mode sets both `CLAUDE_CODE_SUBAGENT_MODEL=gpt-6-astra` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`. Since 2.1.251, the default variable alone no longer overrides explicit subagent model definitions. The force variable is documented starting in 2.1.257:

https://code.claude.com/docs/en/sub-agents#run-every-subagent-on-one-model

With the foreground server running, an authenticated Messages request returned HTTP 200 and `COPILOT_GATEWAY_MESSAGES_OK`. An unauthenticated Messages request returned HTTP 401

From a disposable directory containing only `marker.txt`, the native checks were:

```bash
claude --print \
  'Read marker.txt using the Read tool. Create native-copy.txt with exactly the same contents using an available file editing tool, then read it back. Do not change other files or use the network. Finally reply exactly NATIVE_CLAUDE_OK.' \
  --no-session-persistence --output-format stream-json --verbose \
  --tools Read,Write,Edit --allowedTools Read,Write,Edit \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}'

cmp marker.txt native-copy.txt

claude --print \
  'Use the Agent tool to delegate to the gateway-checker subagent. Ask it to read marker.txt with the Read tool and report its exact contents. Do not read the marker yourself. Wait for its result and reply with the marker value only. Do not write files or use the network.' \
  --no-session-persistence --output-format stream-json --verbose \
  --tools Agent,Read --allowedTools Agent,Read \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --agents '{"gateway-checker":{"description":"Reads the gateway marker using a tool","prompt":"Read marker.txt using Read and report its exact contents. Do not modify files or use the network.","model":"claude-gateway-override-probe","tools":["Read"]}}'
```

The first run emitted `Read`, `Write`, and `Read`, returned `NATIVE_CLAUDE_OK`, and produced a byte-identical copy. The second emitted an `Agent` call for `gateway-checker` and a child `Read` event linked by `parent_tool_use_id`. That child used `gpt-6-astra` despite its deliberately conflicting model definition, and the final response matched the marker. Both results had `is_error: false` and reported only `gpt-6-astra` in model usage

These were normal native launches, without `--bare`, an explicit model argument, or wrapper-provided credentials. Existing Claude effort settings survived, its original settings were backed up, and a repeated native configuration run made no additional changes. The native Codex configuration remained byte-identical, and no isolated Codex profile or npm client directory was created

Claude Code 2.1.266 also printed the following nonfatal diagnostic for the custom model:

```text
[claude-code:unrecognized_model] {"model":"gpt-6-astra","query_source":"sdk"}
```

The diagnostic was not suppressed or replaced with fabricated model metadata. The real tool workflows still completed through the gateway. This remains a cross-provider compatibility setup, not official support for a non-Claude backend. A pinned model ID does not freeze the provider's model weights

## Auto-mode classifier stop sequences

On 2026-09-10, an interactive Claude Code session reported `gpt-6-astra[1m] is temporarily unavailable` while other requests succeeded. The gateway recorded an `UnsupportedParamsError` for `stop`, followed by HTTP 400

A normal native request with `--model 'gpt-6-astra[1m]'` succeeded. A metadata-only local relay showed that Claude normalized the wire model to `gpt-6-astra`. The failing request was a separate auto-mode classifier call: non-streaming, no tools, `max_tokens: 2112`, and `stop_sequences: ["</block>"]`. The ordinary generation request was streaming and did not carry stop sequences

The previous Read/Edit checks and a harmless `printf` command did not exercise this classifier. A Python calculation in auto mode reproduced the failure without an allowlist:

```bash
claude --print \
  "Use Bash once to run exactly: python3 -c 'print(731921 + 284637)'. Do not run a different command, read or write files, access the network, or retry a blocked action. Report the numeric result only." \
  --model 'gpt-6-astra[1m]' --permission-mode auto --max-turns 2 \
  --tools Bash --no-session-persistence --output-format stream-json --verbose \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}'
```

Before the fix, the Bash tool returned an error saying auto mode could not determine the action's safety. Its command did not run. The process nevertheless exited successfully, reinforcing why inspecting actual tool results matters

The fix registers a LiteLLM callback for non-streaming GPT Messages requests without tools. It removes only the unsupported upstream stop parameter and enforces the requested stop sequences on returned text, including matches across text blocks. It retains the real model decision, thinking content before the match, and original usage accounting. Neither auto mode nor permission checks are disabled

After the fix, the same native command on the normal port 4000 gateway reported:

```text
permissionMode: auto
model: gpt-6-astra[1m]
Bash tool result: 1016558
is_error: false
final result: 1016558
```

A separate real Messages request asked the model to return `<block>DENIED</block>SHOULD_NOT_APPEAR` with `stop_sequences: ["</block>"]`. The response retained `<block>DENIED`, excluded the marker and suffix, and returned `stop_reason: "stop_sequence"` and `stop_sequence: "</block>"`. Regression cases also cover cross-block markers, the earliest match, a marker at the start, unchanged reasoning text, and untouched streaming or tool-bearing requests

Gateway-side truncation cannot prevent the upstream from generating tokens after the stop marker, so usage remains the provider's actual total. Streaming stop emulation and tool-bearing stop requests are not qualified. A successful classifier workflow is not a guarantee of a non-Claude model's safety judgments

https://code.claude.com/docs/en/permission-modes#eliminate-prompts-with-auto-mode

https://code.claude.com/docs/en/errors#auto-mode-cannot-determine-the-safety-of-an-action

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
