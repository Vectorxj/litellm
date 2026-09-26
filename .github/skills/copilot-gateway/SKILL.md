---
name: copilot-gateway
description: Configure or refresh a local LiteLLM server exposing the authorized Copilot model catalog to Codex and Claude Code, with selectable models, native Claude Messages, and reproducible software/default-model pins
---

# Copilot gateway

Read `README.md`, `EXPERIENCE.md`, and `checkpoint.json` in this directory before changing the setup

## Required inputs

Require an explicit credential source and an explicit model for Claude Code. Accept a private token file, a named environment variable, standard input, or a hidden terminal prompt. Never ask for a token in chat, put one in a command argument, print credentials, or commit generated state

For a fresh environment, ask the user to supply a supported Copilot GitHub OAuth token, GitHub App user token, or fine-grained PAT. A short-lived Copilot API token is not a durable substitute. Do not silently read another application's credential store. If the user explicitly authorizes reusing an existing Copilot login, use the documented storage or credential interface for that installed CLI version and transfer the credential only through process memory, private files, or standard input

When switching credentials, preserve the old token privately and do not use it as a fallback unless authorized. Existing provider-bound input IDs or encrypted reasoning may fail under the new credential. Diagnose the first upstream error before a later router cooldown: a 401 ownership error can become a misleading 429. Preserve the original conversation and get explicit approval before lossy migration or removing opaque reasoning state

Confirm the requested default models. The checkpoint defaults both clients to `gpt-6-astra`; setup still requires `--claude-model` explicitly. When asked to prefer Opus 5.5 for Claude Code, verify it against the current account and client version, and use Astra only when that fallback is authorized. Discover every enabled model, not only the defaults. Keep the full account-specific catalog in private runtime state, never in Git

Keep normal model selection usable. Do not force `ANTHROPIC_MODEL` or a fixed subagent model over the user's saved choice. Subagents should normally inherit the currently selected main model, while explicit agent definitions and managed policy retain their documented precedence. Do not introduce a lightweight-model split unless requested

## Reproduce an existing checkpoint

Run the commands from `README.md` using the supplied credential source. Keep the private state directory outside the checkout

When only Claude Code is requested, use `setup --claude-only` with the explicitly selected model and credential source. This uses the already-installed native Claude version pinned as `native_claude_version`, skips npm client installation and all Codex configuration, and makes `configure-clients` change only Claude Code settings

When the user asks to run normal `codex` and `claude` commands, use the standard configuration workflow: start the server, then run `configure-clients` to back up and merge their standard settings. This is an explicit opt-in because it changes the default backend. Do not edit saved login files or put tokens in TOML, JSON, or shell profiles. The command uses the clients' supported key helpers instead

Otherwise, use the isolated launchers and leave personal client configurations untouched. Do not automatically import personal hooks, MCP servers, or plugins into the isolated profiles

Review shared skills and managed policies separately. A custom `CODEX_HOME` does not relocate `$HOME/.agents/skills`, and neither client profile bypasses organization policy

Use the committed software checkpoint and package lock without replacing version pins with `latest`. Discover and validate the authorized model capabilities. Use native Responses where advertised, Chat Completions bridging for chat-only models, native Messages for compatible Claude models, and embeddings for embedding models. Reject unavailable selected defaults, malformed capabilities, and source drift rather than silently changing models

Use `refresh-models` to update deployments and client model pickers without changing saved defaults or permissions. Native Claude picker changes are backed up and apply only to profiles using this gateway. Explain that the gateway and clients must restart to read refreshed catalogs. Keep model-specific context budgets and reasoning capabilities in the catalog rather than pinning every model to one global window

Start the server on loopback only. Preserve permission checks and sandboxing. Never use approval bypass flags merely to make a smoke check pass

After startup, check the actual HTTP response from the proxy, including refusal of unauthenticated inference requests. Then run the selected clients' smoke checks in a disposable directory containing only a harmless marker file. Check that each selected client actually reads the marker with a tool and returns its value. Do not run Codex when only Claude Code was requested. Do not substitute a mocked provider or a successful `/health` response for working inference

For standard configuration, validate the actual native `codex` and `claude` commands without wrapper-provided credentials. Do not use Claude's `--bare` mode for that check because it skips normal settings loading. Keep the originals in the private backup directory and confirm that unrelated settings and previous providers survived

Keep Codex's bounded stream recovery enabled with `stream_max_retries = 5`. HTTP request retries do not cover a stream that closes before `response.completed`. Correlate client and server timestamps before changing transport or timeouts. Do not retry partial generations inside the gateway or synthesize successful completion events; leave conversation-aware recovery to Codex and surface exhausted retries

For Claude auto mode, exercise a harmless command that requires classification, such as a Python calculation, without pre-approving Bash. Read and allowlisted tools do not exercise the classifier. Native Claude handles its own stop sequences; the compatibility callback is restricted to models using native Responses. Do not replace this with disabled auto mode, unconditional approval, or globally dropped parameters

Use a foreground server under the agent's process manager while testing. Do not silently install a system service or leave a detached daemon. Explain how the user should run the foreground server afterward

## Qualify a new model or environment

Query the user's authorized model catalog with `catalog`. Treat current model names, endpoint support, token limits, and client capabilities as facts to discover, not facts to recall from training. Read current official client documentation and the upstream implementation linked in `EXPERIENCE.md`

Confirm the endpoint and tool capabilities for each selected default. Test streaming text, a tool call with a returned result, and a subsequent turn. Check reasoning replay rather than only the first response. For Claude Code, exercise native Messages when advertised and the compatibility bridge for other models

Do not introduce global `drop_params`, fabricated thinking signatures, fixed `X-Initiator: agent`, or identity headers intended to alter billing or bypass access restrictions. Do not claim that a GitHub Models credential, a Copilot subscription, and an OpenAI API key are interchangeable

Only enable hosted web search, deferred tool search, WebSocket transport, or provider-specific context features after checking the actual API behavior. Disabling Tool Search must leave ordinary MCP tools usable. Keep unsupported hosted services explicit rather than returning invented successful results

Update `checkpoint.json` with qualified public default model IDs, software versions, repository source revision, and lock hash. Store the full discovered catalog only in private state. Update `package.json` and regenerate `package-lock.json` when client pins change. If an API or client schema changed, update the scripts and meaningful regression tests in this same directory

Native and packaged client releases can differ. Qualify their pins separately and do not silently downgrade installed clients. Verify the actual model picker and persisted default behavior after a client upgrade, not just a successful request with an explicit `--model` flag

Record the date, source links, exact non-secret commands, observed outputs, and remaining limitations in `EXPERIENCE.md`. A model ID pin does not make provider-side model weights immutable. Explain that distinction

Run the focused tests and type checks from `README.md`. Repeat the real proxy and client checks. Keep all committed files and commit messages in English. Never commit token files, local profiles, full account catalogs, generated session transcripts, or private environment paths
