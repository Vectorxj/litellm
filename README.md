# Set up Claude Code and Codex on a new machine

Copy the prompt below into an agent on the new machine. It uses this fork's existing Copilot gateway skill and scripts to configure the normal `codex` and `claude` commands

```text
Configure this machine so I can run codex and claude directly against one local LiteLLM server using my GitHub Copilot access

Repository: git@github.com:Vectorxj/litellm.git
Setup branch: litellm_copilot_gateway_kit

Use an existing checkout or clone the repository if needed. Preserve local changes and update the setup branch safely. Read .github/skills/copilot-gateway/SKILL.md, its README.md, EXPERIENCE.md, and checkpoint.json before acting

Use the committed checkpoint and its pinned dependencies and client versions. Route both Codex and Claude Code to checkpoint.json's default_codex_model, passing that model explicitly as --claude-model. Preserve the checked-in auxiliary/subagent routing. Do not silently switch models, add a small-model split, or replace pins with latest

I authorize you to reuse my existing Copilot CLI login on this machine through its documented credential interface. If a usable credential is unavailable, ask me to provide a private token file, a named environment variable, or a hidden terminal prompt. Never ask me to paste a token into chat, print it, put it in a command argument, or commit it

Install missing prerequisites and the qualified native Codex and Claude Code clients on PATH. Explain any version conflict before replacing an existing installation. Reuse .github/skills/copilot-gateway/bootstrap.sh setup rather than rewriting the gateway, and keep credentials and generated state outside the checkout

Start or reuse the matching server on 127.0.0.1:4000, or report a port conflict without stopping another process. Once it is ready, run .github/skills/copilot-gateway/bootstrap.sh configure-clients to back up and merge my standard Codex config.toml and Claude Code settings.json. Preserve previous providers, unrelated settings, permissions, and saved logins. Use the existing key helpers so neither client requires a wrapper or an exported API key

Verify the actual native codex and claude commands using harmless read/edit tasks in a disposable directory. Check that both use the intended model through this server, that written files match the requested contents, and that unauthenticated inference is rejected. Do not use mocked providers, wrapper-provided credentials, Claude's --bare mode, or approval-bypass flags to make this pass

If credentials, model access, source pins, or managed settings prevent completion, report the blocker rather than choosing an unapproved fallback. Do not modify application code or expand the scope unnecessarily

Finish with the selected models, server address, config and backup locations, and the exact server start/stop instructions. State whether the server will survive your agent session. If it will not, give me the foreground command to run in a separate terminal; do not silently install a service or claim that a temporary server is persistent
```

Detailed instructions: [gateway README](.github/skills/copilot-gateway/README.md), [agent skill](.github/skills/copilot-gateway/SKILL.md), and [qualification notes](.github/skills/copilot-gateway/EXPERIENCE.md)

The upstream project documentation is preserved in [README.original.md](README.original.md)
