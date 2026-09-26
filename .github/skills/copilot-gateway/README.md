# Copilot model gateway

This directory provisions a loopback-only LiteLLM server exposing the enabled models returned by your Copilot account. Codex receives a selectable catalog of conversation models, and Claude Code can use native Claude models or compatible translated models through the same server

The default checkpoint selects `gpt-6-astra` for both Codex and Claude Code, with Python 3.12.13, Codex 0.154.0, and Claude Code 2.1.266. Opus 5.5 was unavailable to the configured account when checked on 2026-09-26, so Claude Code remains on Astra. `checkpoint.json` pins the software and qualified defaults. The full account-specific model catalog is discovered at setup and retained only in private runtime state

`SKILL.md` is the agent workflow for provisioning and future upgrades. `bootstrap.sh` and the Python modules discover current availability while retaining the explicitly selected defaults and pinned software. `EXPERIENCE.md` records the actual compatibility findings and public references

## Fresh environment

Clone the branch containing this directory. For this fork:

```bash
git clone --branch litellm_copilot_gateway_kit git@github.com:Vectorxj/litellm.git
cd litellm
```

Have Python 3, Node.js 22 or newer, npm, Git, and uv available. The qualified uv version is 0.12.5. If uv is missing, install that exact package through your usual Python package manager, for example:

```bash
python3 -m pip install --user uv==0.12.5
```

No remote install script is piped into a shell. Bootstrap installs the pinned Python runtime and the repository's frozen dependency lock into a dedicated environment. It includes the `proxy-dev` group because this source revision needs Prisma's exception types even when no database is configured

Provide a Copilot GitHub OAuth token through a hidden prompt:

```bash
./.github/skills/copilot-gateway/bootstrap.sh setup \
  --prompt-token \
  --claude-model gpt-6-astra
```

Alternatively, provide a token file outside the checkout, owned by your user with mode `0600`, or name an environment variable populated by your secret manager:

```bash
./.github/skills/copilot-gateway/bootstrap.sh setup \
  --token-file /absolute/private/path/copilot-token \
  --claude-model gpt-6-astra

./.github/skills/copilot-gateway/bootstrap.sh setup \
  --token-env COPILOT_GITHUB_TOKEN \
  --claude-model gpt-6-astra
```

`--token-stdin` is available for an authorized credential helper. There is deliberately no `--token VALUE` argument, automatic credential-store search, or token embedded in the generated client settings

The token must authorize Copilot, not just ordinary GitHub repository access. This kit accepts `gho_` OAuth tokens, `ghu_` GitHub App user tokens, and `github_pat_` fine-grained PATs. GitHub documents personal-account-owned PATs with the Copilot Requests account permission. A temporary Copilot API token is rejected rather than treated as a permanent credential. The script checks the current authorized catalog and fails if the requested model or capabilities are unavailable. Credential expiry and replacement remain the operator's responsibility; the kit does not implement GitHub App refresh-token rotation

This checkpoint targets the standard `github.com` Copilot API host. Enterprise or data-residency hosts need separate qualification; the script deliberately does not accept an arbitrary destination for the token

The Claude Code model is required explicitly. `--codex-model` overrides the default Codex model. Both defaults must be enabled conversation models in the current authorized catalog. The gateway exposes the other enabled models too, rather than restricting requests to those two defaults. `--port` selects another unprivileged port if 4000 is occupied

Responses-capable models use Copilot's native `/responses` endpoint. Chat-only models use `/chat/completions`, with LiteLLM translating Codex's Responses requests when necessary. Models advertising `/v1/messages` use native Messages passthrough for Claude Code. Embedding models remain available through `/v1/embeddings` and are excluded from coding-client conversation catalogs

Legacy catalog entries without an endpoint list use their chat or embedding API according to the advertised capability type. Explicitly disabled models are excluded and reported. Malformed or unknown capability shapes fail setup instead of silently publishing an incomplete catalog

### Configure only Claude Code

Use `--claude-only` to leave Codex entirely unconfigured. This mode uses the qualified native Claude Code executable already on `PATH`, rather than installing npm clients. Both packaged and native version pins are recorded in `checkpoint.json`

```bash
claude install 2.1.266

./.github/skills/copilot-gateway/bootstrap.sh setup \
  --token-file /absolute/private/path/copilot-token \
  --claude-model gpt-6-astra \
  --claude-only
```

Start `bootstrap.sh serve` in another terminal, then run `bootstrap.sh configure-clients`. The saved selection makes that command back up and merge only Claude Code settings. It does not read or modify native Codex settings, generate an isolated Codex profile, download its prompt, or install either npm client

Subagents normally inherit the currently selected conversation model. Setup removes the previous forced-subagent and forced-startup-model overrides so selecting another model can work normally. Explicit agent definitions and managed policy still apply. A client upgrade requires requalifying its version

## Start the server and clients

Keep the server in its own terminal:

```bash
./.github/skills/copilot-gateway/bootstrap.sh serve
```

### Use the normal `codex` and `claude` commands

With the server running, close existing client sessions and configure their standard settings once:

```bash
./.github/skills/copilot-gateway/bootstrap.sh configure-clients
```

Then run `codex` or `claude` directly from the project you want to work on. No launcher or exported API key is needed

This opt-in command merges `~/.codex/config.toml` and `~/.claude/settings.json`, honoring `CODEX_HOME` or `CLAUDE_CONFIG_DIR` when set. It preserves previous Codex providers, comments, project trust, sandbox and approval preferences, and unrelated Claude settings. Existing Claude allow/deny rules are preserved, with unsupported WebSearch added to the deny list

The configurations contain key-helper commands, not tokens. Codex's provider `auth.command` and Claude's `apiKeyHelper` read the private local `proxy-key` file automatically. Existing Claude authentication environment overrides are cleared in the settings so they do not defeat the helper. Copilot credentials stay server-side, and neither client's saved login file is changed

Original files are copied byte for byte into a private `client-backups/native-*` directory under gateway state before modification. Their backup paths are printed without contents. Updated config files have mode `0600`. Repeating the command without changes does not create more backups. To undo a switch, close the clients and copy the matching backed-up file to its original location

Invalid, read-only, symlink-managed, or concurrently changed configurations are rejected instead of overwritten. An active Codex profile must be configured explicitly because it takes precedence over `config.toml`. Project settings, command-line options, and managed policy can still override user-level defaults

The selected native clients must already be on `PATH` at the qualified versions. This command does not replace global executables, and ordinary native launches do not have the isolated launcher's version guard. Requalify after upgrading clients

Native commands inherit your shell environment. If you use a forward proxy, include `127.0.0.1`, `localhost`, and `::1` in `NO_PROXY` so gateway requests stay local

### Select models and refresh the catalog

Use Codex's `/model` picker or `codex --model <catalog-id>` to select a conversation model. Change the top-level `model` setting in your native `config.toml` to set its default. Context windows, compaction headroom, reasoning choices, and input modalities come from each model's catalog entry, not a global Astra-sized context override

Claude Code's default is stored in its normal `model` setting. No `ANTHROPIC_MODEL` environment override forces it back on startup, so `/model` can save another default. A generated `modelPicker` lists the real IDs and names of all conversation models. Gateway discovery remains enabled for Claude models, but its built-in non-Claude filter is not relied upon to populate the complete picker

Refresh the authorized catalog without changing either client's chosen default:

```bash
./.github/skills/copilot-gateway/bootstrap.sh refresh-models
```

Restart the gateway and clients after refreshing. This updates gateway deployments, the private Codex model catalog, and Claude picker entries for profiles using this gateway. Native Claude picker updates are backed up, and saved model defaults, permissions, and other preferences are preserved. Profiles pointing at another gateway are not changed. An explicitly requested `configure-clients` operation still applies the setup selection. If the software checkpoint itself changed, run setup first

Model availability and feature support vary. Listing a model does not grant access to another account, make an embedding model a coding assistant, or guarantee identical hosted tools and context behavior across providers

### Keep personal configurations unchanged

The isolated launchers remain available without running `configure-clients`:

```bash
./.github/skills/copilot-gateway/bootstrap.sh codex
./.github/skills/copilot-gateway/bootstrap.sh claude
```

Arguments after `--` are forwarded to the selected client:

```bash
./.github/skills/copilot-gateway/bootstrap.sh codex -- exec \
  --sandbox read-only 'Explain this project without changing files'

./.github/skills/copilot-gateway/bootstrap.sh claude -- \
  --print 'Explain this project without changing files' --tools Read --allowedTools Read
```

Keep a Claude Code prompt before variadic flags such as `--tools` and `--allowedTools`, or use the client's positional argument separator. Otherwise the client can consume the prompt as another tool name

The isolated launcher preserves your current working directory. It does not bypass client approvals or sandboxes. Only the explicit `configure-clients` command edits standard client settings. Neither mode edits shell startup files or the Copilot login, or installs a system service

## Private state and isolation

State defaults to `${XDG_STATE_HOME:-$HOME/.local/state}/litellm-copilot-gateway`. Set `COPILOT_GATEWAY_HOME` to an absolute, dedicated directory outside the checkout to use another location. Use the same value for setup, server, and client commands

The state directory has mode `0700`, and generated credential and configuration files have mode `0600`. A separate random proxy key protects local inference. The upstream Copilot token is supplied only to the server process. Codex and Claude Code receive the local proxy key instead

Changing upstream credentials can invalidate provider-bound history in existing conversations. An old input item or encrypted reasoning block can return `401 input item does not belong to this connection` even when a fresh request with the new credential succeeds. LiteLLM can then cool down the deployment, making the next attempt appear as a `429 No deployments available` error

Keep the original session and old credential backup. Continuing exclusively with the new credential may require a fresh conversation with a plain-text handoff, or an explicitly approved migration copy that omits nonportable provider state. Do not silently discard encrypted reasoning, rewrite the original history, switch back to the old token, or treat this ownership error as an exhausted rate limit

Generated state includes `proxy.yaml`, `upstream-token`, `proxy-key`, `curl-headers`, `selection.json`, a private Python environment, pinned npm clients, and isolated client profiles. Setup can be rerun with the existing private token file. It preserves the proxy key and regenerates configuration rather than merging unrelated profiles

Client automatic updates and startup update prompts are disabled for this profile. The launcher checks client versions and rejects drift instead of silently running another checkpoint

The launcher strips inherited provider credentials, database settings, telemetry configuration, and other client profiles. It preserves ordinary shell and certificate settings and excludes loopback traffic from outbound proxies. LiteLLM runs in production mode so it does not load a repository `.env`

The Claude launcher uses its generated settings explicitly, including under `--bare`. Review explicit agent/skill model overrides and managed policy separately. Existing personal plugins, MCP servers, and hooks are not automatically imported into the isolated Claude profile. Configure such additions deliberately rather than copying another profile wholesale

`CODEX_HOME` isolates Codex configuration and state, not every discovery path. Codex can still discover shared skills under `$HOME/.agents/skills` and repository instructions. Review those separately when preparing a controlled environment

Generated profiles are not a security boundary against the same operating-system user or deliberate command-line overrides. Do not expose this single-user master-key setup on a public interface or reuse it as a multi-user gateway

## Compatibility choices

The gateway sends the supplied GitHub OAuth token directly to the Copilot API using the documented default CLI/SDK integration ID, `copilot-developer-cli`, and identifies itself honestly as `litellm-copilot-gateway/1`. It uses LiteLLM's OpenAI-compatible provider for the wire protocol, not OpenAI as the upstream service. It does not impersonate a VS Code installation or store an OAuth token as a fabricated, never-expiring Copilot session token. Raw Copilot API interoperability remains experimental, rather than a documented public inference API contract

Codex uses Responses over HTTP/SSE. The upstream catalog advertised WebSockets, but the tested native WebSocket path did not finish a generation, so the checkpoint does not enable it. This is a tested transport choice, not a claim that Copilot lacks WebSockets

Codex allows up to five stream-recovery attempts with `stream_max_retries = 5`, matching its built-in default. This is separate from `request_max_retries = 2`, which covers failed HTTP requests before streaming. The earlier value of zero disabled recovery and made a single upstream read failure end the turn with `stream closed before response.completed`

Stream recovery belongs to Codex, which tracks conversation and tool state. The gateway does not replay partial generations, invent a completion event, or hide persistent failures. Recovery can make another billable model request and is not an exactly-once guarantee for external tool effects. Persistent outages still fail after the bounded attempts

Existing profiles need the updated setting too. Run setup and `configure-clients` with qualified clients, or change only `[model_providers.copilot_gateway].stream_max_retries` from `0` to `5` in an existing native `config.toml`. Close and reopen Codex, or resume the conversation in a new process, to load it. No server restart is required for this client setting

Claude models advertising Messages use native Anthropic passthrough, preserving their native message and streaming format. When Claude Code selects a Responses-only model, LiteLLM's existing Messages-to-Chat-to-Responses compatibility path avoids the previously observed tool-block closure failure. Chat-only models use the Messages-to-Chat bridge. Ordinary streaming and tools remain enabled

Claude auto mode makes a separate, non-streaming safety-classifier request with `stop_sequences: ["</block>"]`. Responses does not accept the translated `stop` parameter. The gateway's registered callback emulates it only for models routed to native Responses, using the configured catalog. Native Claude Messages and chat-backed models retain their supported stop handling. The model's actual allow or block decision is preserved. Existing approval rules and auto mode remain unchanged

Streaming requests and requests declaring tools do not use this stop emulation and still reject unsupported stop parameters. The callback does not alter thinking blocks or invent successful tool results. Because truncation happens after generation, upstream usage can include text beyond the marker, and the original usage counts are preserved. This compatibility check is not a safety guarantee for running a non-Claude classifier model

The native model name `gpt-6-astra[1m]` works through Claude Code's own model normalization. Claude sends `gpt-6-astra` on the wire, so no fabricated upstream model or extra gateway alias is needed

Codex receives an explicit model catalog, including each model's observed limits and capabilities, so it does not silently use unknown-model metadata. Setup downloads the official generic Codex prompt from the pinned release and verifies its SHA-256 before using it. That upstream prompt is distributed under [Codex's Apache-2.0 license](https://github.com/openai/codex/blob/rust-v0.154.0/LICENSE)

The preferred reasoning effort is `high` when the selected model advertises it; non-reasoning models receive no invented reasoning capability. Compaction budgets reserve the model's output allowance within its own context window and respect any smaller advertised input limit

These per-model context budgets are part of the Codex catalog. Claude Code 2.1.266 applies its own conservative context limit to unrecognized non-Claude model names; the gateway does not make them look like a different Claude model. Its supported `[1m]` suffix can select an extended window where the actual upstream model supports it

Hosted WebSearch is disabled in both profiles. Claude Code's deferred Tool Search is also disabled, which loads tools up front rather than disabling ordinary MCP. The kit does not invent search results or configure an unrequested paid search provider. It does not globally drop unsupported parameters, disable thinking, or force agent-initiated billing headers

Copilot controls access, rate limits, and charging. LiteLLM's OpenAI price estimates are not authoritative Copilot billing. Respect your subscription terms and organization policies. An OpenAI-shaped endpoint does not imply that every hosted OpenAI service, Anthropic beta feature, or model checkpoint is available

## Check the running configuration

These commands use the private header file so the proxy key does not appear in command arguments:

```bash
STATE="${COPILOT_GATEWAY_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}/litellm-copilot-gateway}"

curl --fail-with-body --silent --show-error \
  --header @"$STATE/curl-headers" \
  http://127.0.0.1:4000/v1/responses \
  --data '{"model":"gpt-6-astra","input":"Reply exactly GATEWAY_OK","store":false,"max_output_tokens":256,"reasoning":{"effort":"low"}}'

curl --fail-with-body --silent --show-error \
  --header @"$STATE/curl-headers" \
  --header 'anthropic-version: 2023-06-01' \
  http://127.0.0.1:4000/v1/messages \
  --data '{"model":"gpt-6-astra","max_tokens":512,"messages":[{"role":"user","content":"Reply exactly GATEWAY_OK"}]}'
```

Adjust the port and model when using another qualified selection. Do not paste private header files, full debug logs, or tokens into an issue or chat

For a real client tool check, create a disposable directory containing a harmless `marker.txt`, keep the server running, and run:

```bash
KIT="$PWD/.github/skills/copilot-gateway"
WORK="$(mktemp -d)"
printf '%s\n' 'gateway-tool-check-9f43c20a' > "$WORK/marker.txt"

"$KIT/bootstrap.sh" codex -- exec --strict-config \
  --sandbox read-only --ephemeral --skip-git-repo-check --cd "$WORK" --json \
  'Read marker.txt using a tool, then reply with its exact contents only. Do not modify files or use the network.' \
  </dev/null

(
  cd "$WORK"
  "$KIT/bootstrap.sh" claude -- \
    --print 'Read marker.txt using the Read tool, then reply with its exact contents only.' \
    --bare --no-session-persistence --output-format stream-json --verbose \
    --tools Read --allowedTools Read
)

rm "$WORK/marker.txt"
rmdir "$WORK"
```

Confirm the trace contains a successful tool invocation and the marker value, not just a successful process exit. Do not use a working directory containing private data for a smoke check

## Agent handoff and upgrades

Copilot CLI can discover the skill under `.github/skills/copilot-gateway`. For another agent, explicitly ask it to read `.github/skills/copilot-gateway/SKILL.md`; no global skill installation is required

A suitable handoff is: "Read `.github/skills/copilot-gateway/SKILL.md`. Discover all enabled Copilot models using my private token file. Default Codex to `gpt-6-astra`. Check whether Claude Code can use Opus 5.5; if unavailable, keep it on Astra. Allow normal model selection, start the gateway, back up and merge my native client settings, and run real client tool checks"

To inspect the currently authorized catalog without changing the running server:

```bash
./.github/skills/copilot-gateway/bootstrap.sh catalog \
  --token-file /absolute/private/path/copilot-token
```

Do not commit a full account catalog. Catalog refresh discovers availability without silently changing user-selected defaults. Ask the skill to qualify new software or defaults, revise the checkpoint when needed, and update the evidence

Source drift is rejected against the checkpoint's LiteLLM revision and `uv.lock` hash. Use the recorded source revision or qualify a new checkpoint. A shallow clone might need its history fetched so the recorded source commit is available. Changing a model ID does not guarantee immutable provider-side weights, and credentials and upstream availability cannot be reproduced by a Git commit

## Development checks

From the repository root, after installing the repository's locked development dependencies:

```bash
LITELLM_MODE=PRODUCTION LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync pytest -q .github/skills/copilot-gateway

uv run --no-sync ruff check .github/skills/copilot-gateway
uv run --no-sync ruff format --check .github/skills/copilot-gateway
uv run --no-sync basedpyright \
  .github/skills/copilot-gateway/gateway.py \
  .github/skills/copilot-gateway/gateway_config.py \
  .github/skills/copilot-gateway/gateway_catalog.py \
  .github/skills/copilot-gateway/gateway_client_config.py \
  .github/skills/copilot-gateway/gateway_files.py \
  .github/skills/copilot-gateway/gateway_stop_sequences.py \
  .github/skills/copilot-gateway/native_config.py
bash -n .github/skills/copilot-gateway/bootstrap.sh
```

The focused tests cover private and idempotent provisioning, explicit model selection, capability checks, credential isolation, integrity verification, streamed tool-block compatibility, and backup-preserving native configuration with working key helpers. Real provider and client checks remain necessary when qualifying a new checkpoint
