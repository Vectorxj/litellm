---
name: copilot-gateway
description: Configure or update a reproducible local LiteLLM server that sends Codex and Claude Code requests to models authorized by a supplied GitHub Copilot token. Use for fresh-clone provisioning, explicit Claude Code-to-GPT model mapping, checkpoint upgrades, or diagnosing this gateway
---

# Copilot gateway

Read `README.md`, `EXPERIENCE.md`, and `checkpoint.json` in this directory before changing the setup

## Required inputs

Require an explicit credential source and an explicit model for Claude Code. Accept a private token file, a named environment variable, standard input, or a hidden terminal prompt. Never ask for a token in chat, put one in a command argument, print credentials, or commit generated state

For a fresh environment, ask the user to supply a Copilot GitHub OAuth token or a supported fine-grained PAT. A short-lived Copilot API token is not a durable substitute. Do not silently read another application's credential store. If the user explicitly authorizes reusing an existing Copilot login, use the documented storage or credential interface for that installed CLI version and transfer the credential only through process memory, private files, or standard input

Confirm which OpenAI model Claude Code should use, including its auxiliary and subagent requests. Do not assume that selecting the main model also configures those other requests. The deterministic checkpoint currently defaults Codex to `gpt-6-astra` but still requires `--claude-model`

Recheck subagent model precedence when upgrading the client. Claude Code 2.1.257 and later document `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` for forcing an explicitly chosen model over agent-definition overrides. Do not assume an environment default alone forces every subagent, and test the intended behavior before changing the pinned client

## Reproduce an existing checkpoint

Run the commands from `README.md` using the supplied credential source. Keep the private state directory outside the checkout. Do not edit the user's normal `~/.codex` or `~/.claude` configuration

Review shared skills and managed policies separately. A custom `CODEX_HOME` does not relocate `$HOME/.agents/skills`, and neither client profile bypasses organization policy

Use the committed checkpoint and package lock without replacing version pins with `latest`. The script must reject unavailable models, unsupported Responses endpoints, source drift, and limits above the authorized catalog. Do not bypass those checks or silently choose an older model

Start the server on loopback only. Use the isolated launch commands for Codex and Claude Code. Preserve permission checks and sandboxing. Never use approval bypass flags merely to make a smoke check pass

After startup, check the actual HTTP response from the proxy, including refusal of unauthenticated inference requests. Then run both client smoke checks in a disposable directory containing only a harmless marker file. Check that each client actually reads the marker with a tool and returns its value. Do not substitute a mocked provider or a successful `/health` response for working inference

Use a foreground server under the agent's process manager while testing. Do not silently install a system service or leave a detached daemon. Explain how the user should run the foreground server afterward

## Qualify a new model or environment

Query the user's authorized model catalog with `catalog`. Treat current model names, endpoint support, token limits, and client capabilities as facts to discover, not facts to recall from training. Read current official client documentation and the upstream implementation linked in `EXPERIENCE.md`

Confirm the selected model supports Responses and ordinary function calls. Test streaming text, a tool call with a returned result, and a subsequent turn. Check reasoning replay rather than only the first response. For Claude Code, also exercise the Anthropic Messages translation and its streaming tool arguments

Do not introduce global `drop_params`, fabricated thinking signatures, fixed `X-Initiator: agent`, or identity headers intended to alter billing or bypass access restrictions. Do not claim that a GitHub Models credential, a Copilot subscription, and an OpenAI API key are interchangeable

Only enable hosted web search, deferred tool search, WebSocket transport, or provider-specific context features after checking the actual API behavior. Disabling Tool Search must leave ordinary MCP tools usable. Keep unsupported hosted services explicit rather than returning invented successful results

Update `checkpoint.json` with the qualified model IDs, authorized token limits, exact client/runtime versions, repository source revision, and lock hash. Update `package.json` and regenerate `package-lock.json` with npm when client pins change. If an API or client schema changed, update the scripts and meaningful regression tests in this same directory

Record the date, source links, exact non-secret commands, observed outputs, and remaining limitations in `EXPERIENCE.md`. A model ID pin does not make provider-side model weights immutable. Explain that distinction

Run the focused tests and type checks from `README.md`. Repeat the real proxy and client checks. Keep all committed files and commit messages in English. Never commit token files, local profiles, full account catalogs, generated session transcripts, or private environment paths
