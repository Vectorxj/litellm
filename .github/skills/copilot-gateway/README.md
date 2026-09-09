# Reproducible Copilot gateway

This directory provisions a loopback-only LiteLLM server for Codex and Claude Code. Both clients use models authorized by an explicitly supplied Copilot GitHub token. Claude Code can use an OpenAI model without an OpenAI API key

The committed checkpoint selects `gpt-6-astra`, Python 3.12.13, Codex 0.146.0, and Claude Code 2.1.220. The authenticated catalog advertised a 1,000,000-token context window, an 872,000-token input limit, and a 128,000-token output limit when this checkpoint was qualified. `checkpoint.json` is the source of truth

`SKILL.md` is the agent workflow for provisioning and future upgrades. `bootstrap.sh` and the Python modules reproduce the committed checkpoint without discovering a new default model. `EXPERIENCE.md` records the actual compatibility findings and public references

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

The token must authorize Copilot, not just ordinary GitHub repository access. This kit accepts `gho_` OAuth tokens or `github_pat_` fine-grained PATs. GitHub documents personal-account-owned PATs with the Copilot Requests account permission; this checkpoint's live qualification used a Copilot CLI OAuth token. Other token types supported by the CLI itself are not qualified here. A temporary Copilot API token is rejected rather than treated as a permanent credential. The script checks the current authorized catalog and fails if the requested model or capabilities are unavailable

This checkpoint targets the standard `github.com` Copilot API host. Enterprise or data-residency hosts need separate qualification; the script deliberately does not accept an arbitrary destination for the token

The Claude Code model is required explicitly. `--codex-model` overrides the pinned Codex default, but either model must already be qualified in `checkpoint.json`. `--port` selects another unprivileged port if 4000 is occupied

### Configure only Claude Code

Use `--claude-only` to leave Codex entirely unconfigured. This mode uses the native Claude Code executable already on `PATH`, rather than installing npm clients. The qualified native version is 2.1.266, recorded separately as `native_claude_version` in `checkpoint.json`. The npm-based two-client checkpoint remains on Claude Code 2.1.220

```bash
claude install 2.1.266

./.github/skills/copilot-gateway/bootstrap.sh setup \
  --token-file /absolute/private/path/copilot-token \
  --claude-model gpt-6-astra \
  --claude-only
```

Start `bootstrap.sh serve` in another terminal, then run `bootstrap.sh configure-clients`. The saved selection makes that command back up and merge only Claude Code settings. It does not read or modify native Codex settings, generate an isolated Codex profile, download its prompt, or install either npm client

The native Claude configuration also sets `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, supported since 2.1.257, so explicit subagent model overrides cannot escape the selected backend. Automatic client updates stay disabled after setup to preserve the qualified version. A later `claude update` requires requalifying the native version

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

Generated state includes `proxy.yaml`, `upstream-token`, `proxy-key`, `curl-headers`, `selection.json`, a private Python environment, pinned npm clients, and isolated client profiles. Setup can be rerun with the existing private token file. It preserves the proxy key and regenerates configuration rather than merging unrelated profiles

Client automatic updates and startup update prompts are disabled for this profile. The launcher checks client versions and rejects drift instead of silently running another checkpoint

The launcher strips inherited provider credentials, database settings, telemetry configuration, and other client profiles. It preserves ordinary shell and certificate settings and excludes loopback traffic from outbound proxies. LiteLLM runs in production mode so it does not load a repository `.env`

The Claude launcher uses its generated settings explicitly, including under `--bare`, and sets Sonnet, Opus, Haiku, Fable, and the default subagent model to the selected model. Review explicit agent/skill model overrides and managed policy separately. Existing personal plugins, MCP servers, and hooks are not automatically imported into the Claude profile. Configure such additions deliberately rather than copying another profile wholesale

`CODEX_HOME` isolates Codex configuration and state, not every discovery path. Codex can still discover shared skills under `$HOME/.agents/skills` and repository instructions. Review those separately when preparing a controlled environment

Generated profiles are not a security boundary against the same operating-system user or deliberate command-line overrides. Do not expose this single-user master-key setup on a public interface or reuse it as a multi-user gateway

## Compatibility choices

The gateway sends the supplied GitHub OAuth token directly to the Copilot API using the documented default CLI/SDK integration ID, `copilot-developer-cli`, and identifies itself honestly as `litellm-copilot-gateway/1`. It uses LiteLLM's OpenAI-compatible provider for the wire protocol, not OpenAI as the upstream service. It does not impersonate a VS Code installation or store an OAuth token as a fabricated, never-expiring Copilot session token. Raw Copilot API interoperability remains experimental, rather than a documented public inference API contract

Codex uses Responses over HTTP/SSE. The upstream catalog advertised WebSockets, but the tested native WebSocket path did not finish a generation, so the checkpoint does not enable it. This is a tested transport choice, not a claim that Copilot lacks WebSockets

Claude Code uses LiteLLM's existing Messages-to-Chat-to-Responses compatibility path. The final upstream request is still Responses. This avoids the observed tool-stream failure in the direct Messages-to-Responses path when Copilot changes item IDs between stream events. Ordinary streaming and tools remain enabled

Claude auto mode makes a separate, non-streaming safety-classifier request with `stop_sequences: ["</block>"]`. Responses does not accept the translated `stop` parameter. The gateway's registered callback handles stop sequences locally for non-streaming GPT Messages requests without tools: it omits only the upstream stop parameter, retains the original sequences, and truncates returned text at the first matching sequence with the correct `stop_reason` and `stop_sequence`. The model's actual allow or block decision is preserved. Existing approval rules and auto mode remain unchanged

Streaming requests and requests declaring tools do not use this stop emulation and still reject unsupported stop parameters. The callback does not alter thinking blocks or invent successful tool results. Because truncation happens after generation, upstream usage can include text beyond the marker, and the original usage counts are preserved. This compatibility check is not a safety guarantee for running a non-Claude classifier model

The native model name `gpt-6-astra[1m]` works through Claude Code's own model normalization. Claude sends `gpt-6-astra` on the wire, so no fabricated upstream model or extra gateway alias is needed

Codex receives an explicit model catalog, including the observed context limits and reasoning levels, so it does not silently use unknown-model metadata. Setup downloads the official generic Codex prompt from the pinned release and verifies its SHA-256 before using it. It does not replace the Codex prompt with handwritten gateway instructions. That upstream prompt is distributed under [Codex's Apache-2.0 license](https://github.com/openai/codex/blob/rust-v0.146.0/LICENSE)

The default reasoning effort is `high`. Codex compacts at 90% of the advertised input limit rather than 90% of the larger total context window. This leaves room for the next tool result and respects Copilot's input/output split

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

A suitable handoff is: "Read `.github/skills/copilot-gateway/SKILL.md`. Configure the committed checkpoint using the private token file I provide. Route Claude Code to `gpt-6-astra`, start the server, back up and merge the standard client settings so I can run `codex` and `claude` directly, and run the real client tool checks"

To inspect currently authorized GPT models without changing the checkpoint:

```bash
./.github/skills/copilot-gateway/bootstrap.sh catalog \
  --token-file /absolute/private/path/copilot-token
```

Do not commit a full account catalog. The deterministic script does not switch to a newer model automatically. Ask the skill to qualify a new model, revise the pins and scripts if necessary, and update the evidence

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
  .github/skills/copilot-gateway/gateway_files.py \
  .github/skills/copilot-gateway/gateway_stop_sequences.py \
  .github/skills/copilot-gateway/native_config.py
bash -n .github/skills/copilot-gateway/bootstrap.sh
```

The focused tests cover private and idempotent provisioning, explicit model selection, capability checks, credential isolation, integrity verification, streamed tool-block compatibility, and backup-preserving native configuration with working key helpers. Real provider and client checks remain necessary when qualifying a new checkpoint
