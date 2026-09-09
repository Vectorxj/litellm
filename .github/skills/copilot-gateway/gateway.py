import argparse
import getpass
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from itertools import starmap
from pathlib import Path
from typing import Final, Literal

import httpx
from gateway_config import (
    API_BASE,
    PROXY_KEY_ENV,
    UPSTREAM_TOKEN_ENV,
    Checkpoint,
    ModelPin,
    Paths,
    Problem,
    Selection,
    claude_configuration,
    claude_environment,
    clean_environment,
    codex_catalog,
    codex_configuration,
    proxy_configuration,
    selected_pins,
)
from gateway_files import private_directory, read_secret, write_private
from native_config import configure_native_clients, plan_native_clients
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class Options(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    command: Literal["catalog", "setup", "serve", "codex", "claude", "configure-clients"]
    token_env: str | None = None
    token_file: Path | None = None
    token_stdin: bool = False
    prompt_token: bool = False
    claude_model: str | None = None
    claude_only: bool = False
    codex_model: str | None = None
    port: int = Field(default=4000, ge=1024, le=65535)
    client_args: tuple[str, ...] = ()


class ModelLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    max_prompt_tokens: int | None = None
    max_output_tokens: int | None = None
    max_context_window_tokens: int | None = None


class ModelSupport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tool_calls: bool = False
    parallel_tool_calls: bool = False
    streaming: bool = False
    vision: bool = False
    reasoning_effort: tuple[str, ...] = ()


class Capabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    limits: ModelLimits = ModelLimits()
    supports: ModelSupport = ModelSupport()


class CatalogModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    supported_endpoints: tuple[str, ...] = ()
    capabilities: Capabilities = Capabilities()


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    data: tuple[CatalogModel, ...]


def add_token_options(parser: argparse.ArgumentParser) -> None:
    sources: Final = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--token-env", metavar="NAME")
    sources.add_argument("--token-file", type=Path)
    sources.add_argument("--token-stdin", action="store_true")
    sources.add_argument("--prompt-token", action="store_true")


def parse_options(argv: Sequence[str]) -> Options:
    parser: Final = argparse.ArgumentParser(description="Reproduce the pinned local Copilot gateway.")
    commands: Final = parser.add_subparsers(dest="command", required=True)
    catalog: Final = commands.add_parser("catalog")
    add_token_options(catalog)
    setup: Final = commands.add_parser("setup")
    add_token_options(setup)
    setup.add_argument("--claude-model", required=True)
    setup.add_argument(
        "--claude-only",
        action="store_true",
        help="Use the qualified native Claude Code client without configuring Codex.",
    )
    setup.add_argument("--codex-model")
    setup.add_argument("--port", type=int, default=4000)
    commands.add_parser("serve")
    commands.add_parser("configure-clients", help="Back up and merge the selected native client settings.")
    codex: Final = commands.add_parser("codex")
    codex.add_argument("client_args", nargs=argparse.REMAINDER)
    claude: Final = commands.add_parser("claude")
    claude.add_argument("client_args", nargs=argparse.REMAINDER)
    return Options.model_validate(vars(parser.parse_args(argv)))


def paths_from_environment(environ: Mapping[str, str]) -> Paths:
    kit: Final = Path(__file__).resolve().parent
    default_state: Final = Path(environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    state: Final = Path(
        environ.get("COPILOT_GATEWAY_HOME", str(default_state / "litellm-copilot-gateway"))
    ).expanduser()
    return Paths(kit=kit, state=state.absolute())


def provided_token(options: Options, environ: Mapping[str, str]) -> SecretStr | Problem:
    if options.token_file is not None:
        from_file: Final = read_secret(options.token_file)
        return from_file if isinstance(from_file, Problem) else validate_token(from_file)
    if options.token_env is not None:
        raw: Final = environ.get(options.token_env, "").strip()
    elif options.token_stdin:
        raw_stdin: Final = sys.stdin.read().strip()
        return validate_token(SecretStr(raw_stdin))
    elif options.prompt_token:
        if not sys.stdin.isatty():
            return Problem(
                "The hidden token prompt requires a terminal. Use --token-file, --token-env, or --token-stdin."
            )
        entered: Final = getpass.getpass("Copilot GitHub OAuth token (not echoed): ").strip()
        return validate_token(SecretStr(entered))
    else:
        return Problem("An explicit token source is required.")
    return validate_token(SecretStr(raw))


def validate_token(token: SecretStr) -> SecretStr | Problem:
    value: Final = token.get_secret_value()
    if not value.startswith(("gho_", "github_pat_")) or any(character.isspace() for character in value):
        return Problem(
            "Provide a Copilot GitHub OAuth token or supported fine-grained PAT. "
            "A short-lived Copilot API token is not a reproducible credential source."
        )
    return token


def fetch_catalog(
    client: httpx.Client, token: SecretStr, headers: Mapping[str, str]
) -> tuple[CatalogModel, ...] | Problem:
    response: Final = client.get(
        f"{API_BASE}/models",
        headers={**headers, "Authorization": f"Bearer {token.get_secret_value()}", "Accept": "application/json"},
        follow_redirects=False,
        timeout=30,
    )
    if response.status_code != 200:
        return Problem(
            f"Copilot model discovery returned HTTP {response.status_code}. "
            "Check token validity, Copilot access, and network policy. No credentials or response body were logged."
        )
    try:
        catalog: Final = Catalog.model_validate_json(response.content)
    except ValidationError:
        return Problem(
            "Copilot returned an unrecognized model catalog. Update the skill instead of guessing model IDs."
        )
    return catalog.data


def fetch_codex_prompt(client: httpx.Client, paths: Paths, checkpoint: Checkpoint) -> str | Problem:
    cached: Final = paths.state / "codex/prompt.md"
    cached_bytes: Final = cached.read_bytes() if cached.is_file() and not cached.is_symlink() else b""
    if hashlib.sha256(cached_bytes).hexdigest() == checkpoint.codex_prompt_sha256:
        return cached_bytes.decode("utf-8")
    response: Final = client.get(checkpoint.codex_prompt_url, follow_redirects=False, timeout=30)
    if response.status_code != 200:
        return Problem(f"The pinned Codex prompt download returned HTTP {response.status_code}.")
    if hashlib.sha256(response.content).hexdigest() != checkpoint.codex_prompt_sha256:
        return Problem("The Codex prompt checksum does not match the checkpoint. No replacement prompt was used.")
    return response.content.decode("utf-8")


def validate_model(pin: ModelPin, catalog: tuple[CatalogModel, ...]) -> Problem | None:
    model: Final = next((entry for entry in catalog if entry.id == pin.id), None)
    if model is None:
        return Problem(f"Copilot did not advertise the pinned model {pin.id}. No fallback was selected.")
    if not frozenset(model.supported_endpoints).intersection(("/responses", "/v1/responses")):
        return Problem(f"Copilot did not advertise Responses support for {pin.id}.")
    limits: Final = model.capabilities.limits
    if limits.max_prompt_tokens is None or limits.max_output_tokens is None or limits.max_context_window_tokens is None:
        return Problem(f"Copilot did not advertise token limits for {pin.id}.")
    if (
        pin.max_input_tokens > limits.max_prompt_tokens
        or pin.max_output_tokens > limits.max_output_tokens
        or pin.context_window_tokens > limits.max_context_window_tokens
    ):
        return Problem(f"The pinned token limits exceed the current Copilot limits for {pin.id}.")
    support: Final = model.capabilities.supports
    if not all((support.tool_calls, support.parallel_tool_calls, support.streaming, support.vision)):
        return Problem(
            f"The advertised tools, streaming, or vision capabilities do not match this checkpoint: {pin.id}."
        )
    if not frozenset(pin.reasoning_efforts).issubset(support.reasoning_effort):
        return Problem(f"The advertised reasoning levels do not match this checkpoint: {pin.id}.")
    return None


def validate_catalog(pins: tuple[ModelPin, ...], catalog: tuple[CatalogModel, ...]) -> Problem | None:
    return next(filter(None, (validate_model(pin, catalog) for pin in pins)), None)


def verify_source(paths: Paths, checkpoint: Checkpoint) -> Problem | None:
    if hashlib.sha256((paths.repo / "uv.lock").read_bytes()).hexdigest() != checkpoint.uv_lock_sha256:
        return Problem("uv.lock differs from the checkpoint. Use the pinned checkout or qualify a new checkpoint.")
    source_diff: Final = subprocess.run(
        (
            "git",
            "-C",
            str(paths.repo),
            "diff",
            "--quiet",
            checkpoint.source_commit,
            "--",
            "litellm",
            "enterprise",
            "litellm-proxy-extras",
            "pyproject.toml",
            "uv.lock",
            "model_prices_and_context_window.json",
        ),
        check=False,
        capture_output=True,
    )
    if source_diff.returncode != 0:
        return Problem("LiteLLM source differs from the pinned checkpoint, or its commit is unavailable. See README.")
    return None


def configure(
    paths: Paths,
    checkpoint: Checkpoint,
    selection: Selection,
    token: SecretStr,
    catalog: tuple[CatalogModel, ...],
    codex_prompt: str,
) -> Problem | None:
    pins: Final = selected_pins(checkpoint, selection)
    if isinstance(pins, Problem):
        return pins
    catalog_problem: Final = validate_catalog(pins, catalog)
    if catalog_problem is not None:
        return catalog_problem
    directories: Final = (paths.state, paths.state / "claude") + (
        () if selection.claude_only else (paths.state / "codex", paths.state / "clients")
    )
    directory_problem: Final = next(filter(None, map(private_directory, directories)), None)
    if directory_problem is not None:
        return directory_problem
    key: Final = (
        read_secret(paths.key)
        if paths.key.exists() or paths.key.is_symlink()
        else SecretStr("sk-" + secrets.token_urlsafe(32))
    )
    if isinstance(key, Problem):
        return key
    packaged_files: Final = tuple(
        entry
        for pin in pins
        if not selection.claude_only and pin.id == selection.codex_model
        for entry in (
            (
                paths.state / "codex/config.toml",
                codex_configuration(checkpoint, selection, pin, paths.state / "codex/model-catalog.json"),
            ),
            (
                paths.state / "codex/model-catalog.json",
                json.dumps(codex_catalog(checkpoint, pin, codex_prompt), indent=2) + "\n",
            ),
            (paths.state / "codex/prompt.md", codex_prompt),
            (paths.state / "clients/package.json", (paths.kit / "package.json").read_text(encoding="utf-8")),
            (paths.state / "clients/package-lock.json", (paths.kit / "package-lock.json").read_text(encoding="utf-8")),
        )
    )
    files: Final = (
        (paths.token, token.get_secret_value() + "\n"),
        (paths.key, key.get_secret_value() + "\n"),
        (paths.proxy_config, json.dumps(proxy_configuration(checkpoint, pins), indent=2) + "\n"),
        *packaged_files,
        (paths.state / "claude/settings.json", json.dumps(claude_configuration(selection), indent=2) + "\n"),
        (
            paths.state / "curl-headers",
            f"Authorization: Bearer {key.get_secret_value()}\nContent-Type: application/json\n",
        ),
        (paths.state / "selection.json", selection.model_dump_json(indent=2) + "\n"),
    )
    return next(filter(None, starmap(write_private, files)), None)


def verify_client(executable: Path, expected: str, environ: Mapping[str, str]) -> Problem | None:
    result: Final = subprocess.run(
        (str(executable), "--version"), env=environ, capture_output=True, text=True, check=False, timeout=30
    )
    if result.returncode != 0 or result.stdout.strip() != expected:
        return Problem(f"The pinned client did not report {expected}. Rerun setup and check the supported platform.")
    return None


def install_clients(
    paths: Paths, checkpoint: Checkpoint, selection: Selection, environ: Mapping[str, str]
) -> Problem | None:
    environment: Final = clean_environment(environ)
    if selection.claude_only:
        native: Final = shutil.which("claude", path=environ.get("PATH"))
        if native is None:
            return Problem(f"Install native Claude Code {checkpoint.native_claude_version} before using --claude-only.")
        return verify_client(Path(native), f"{checkpoint.native_claude_version} (Claude Code)", environment)
    install: Final = subprocess.run(
        ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefix", str(paths.state / "clients")),
        env=environment,
        check=False,
    )
    if install.returncode:
        return Problem("Pinned client installation failed. Configuration is saved; rerun setup to retry.")
    claude_installer: Final = paths.state / "clients/node_modules/@anthropic-ai/claude-code/install.cjs"
    native_install: Final = subprocess.run(("node", str(claude_installer)), env=environment, check=False)
    if native_install.returncode:
        return Problem("The pinned Claude Code native-binary installation failed.")
    executables: Final = (
        (paths.state / "clients/node_modules/.bin/codex", f"codex-cli {checkpoint.codex_version}"),
        (paths.state / "clients/node_modules/.bin/claude", f"{checkpoint.claude_version} (Claude Code)"),
    )
    return next(filter(None, (verify_client(path, version, environment) for path, version in executables)), None)


def configure_standard_clients(
    paths: Paths, checkpoint: Checkpoint, selection: Selection, environ: Mapping[str, str]
) -> Problem | None:
    updates: Final = plan_native_clients(paths, checkpoint, selection, environ, Path.home())
    if isinstance(updates, Problem):
        return updates
    claude_version: Final = checkpoint.native_claude_version if selection.claude_only else checkpoint.claude_version
    versions: Final = tuple(
        (
            update.client,
            f"codex-cli {checkpoint.codex_version}" if update.client == "codex" else f"{claude_version} (Claude Code)",
        )
        for update in updates
    )
    for name, version in versions:
        if (executable := shutil.which(name, path=environ.get("PATH"))) is None:
            return Problem(f"Install the qualified native {name} client before configuring its standard settings.")
        if (version_problem := verify_client(Path(executable), version, clean_environment(environ))) is not None:
            return version_problem
    key: Final = read_secret(paths.key)
    if isinstance(key, Problem):
        return key
    try:
        with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as client:
            response: Final = client.get(
                f"http://127.0.0.1:{selection.port}/v1/models",
                headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            )
    except httpx.RequestError:
        return Problem("Cannot reach the local gateway. Start bootstrap.sh serve before configuring native clients.")
    if response.status_code != 200:
        return Problem(f"The local server returned HTTP {response.status_code}. Start the matching gateway first.")
    advertised: Final = frozenset(model.id for model in Catalog.model_validate_json(response.content).data)
    pins: Final = selected_pins(checkpoint, selection)
    if isinstance(pins, Problem):
        return pins
    if not frozenset(pin.id for pin in pins).issubset(advertised):
        return Problem("The running server does not advertise the selected client models.")
    result: Final = configure_native_clients(paths, updates)
    if isinstance(result, Problem):
        return result
    if result is not None:
        sys.stdout.write(f"Client configuration backup directory: {result}\n")
    for update in updates:
        sys.stdout.write(f"Configured native {update.client}: {update.target}\n")
    clients: Final = " or ".join(update.client for update in updates)
    sys.stdout.write(f"Run {clients} directly. Keep the gateway server running.\n")
    return None


def launch(
    paths: Paths, checkpoint: Checkpoint, selection: Selection, options: Options, environ: Mapping[str, str]
) -> Problem:
    key: Final = read_secret(paths.key)
    if isinstance(key, Problem):
        return key
    base_env: Final = clean_environment(environ)
    if options.command == "serve":
        token: Final = read_secret(paths.token)
        if isinstance(token, Problem):
            return token
        os.execve(
            paths.state / "venv/bin/litellm",
            (
                "litellm",
                "--config",
                str(paths.proxy_config),
                "--host",
                "127.0.0.1",
                "--port",
                str(selection.port),
                "--telemetry",
                "False",
                "--num_workers",
                "1",
            ),
            {
                **base_env,
                "LITELLM_MODE": "PRODUCTION",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                UPSTREAM_TOKEN_ENV: token.get_secret_value(),
                PROXY_KEY_ENV: key.get_secret_value(),
            },
        )
    if selection.claude_only and options.command == "codex":
        return Problem("Codex is not configured in this Claude-only gateway.")
    native: Final = shutil.which("claude", path=environ.get("PATH")) if selection.claude_only else None
    if selection.claude_only and native is None:
        return Problem(f"Install native Claude Code {checkpoint.native_claude_version} before launching Claude.")
    executable: Final = (
        Path(native) if native is not None else paths.state / "clients/node_modules/.bin" / options.command
    )
    if not executable.is_file():
        return Problem("Pinned clients are missing. Run setup again to install them from package-lock.json.")
    expected_version: Final = (
        f"codex-cli {checkpoint.codex_version}"
        if options.command == "codex"
        else f"{checkpoint.native_claude_version if selection.claude_only else checkpoint.claude_version} (Claude Code)"
    )
    version_problem: Final = verify_client(executable, expected_version, base_env)
    if version_problem is not None:
        return version_problem
    extra_args: Final = options.client_args[1:] if options.client_args[:1] == ("--",) else options.client_args
    if options.command == "codex":
        os.execve(
            executable,
            (str(executable), *extra_args),
            {**base_env, "CODEX_HOME": str(paths.state / "codex"), "LITELLM_API_KEY": key.get_secret_value()},
        )
    os.execve(
        executable,
        (
            str(executable),
            "--setting-sources",
            "",
            "--settings",
            str(paths.state / "claude/settings.json"),
            "--effort",
            checkpoint.reasoning_effort,
            *extra_args,
        ),
        {
            **base_env,
            **claude_environment(selection),
            "CLAUDE_CONFIG_DIR": str(paths.state / "claude"),
            "ANTHROPIC_AUTH_TOKEN": key.get_secret_value(),
        },
    )
    return Problem("Client execution unexpectedly returned.")


def run(options: Options, paths: Paths, environ: Mapping[str, str]) -> Problem | None:
    resolved_state: Final = paths.state.resolve()
    if (
        not paths.state.is_absolute()
        or resolved_state in (Path("/"), Path.home(), paths.repo)
        or paths.repo in resolved_state.parents
    ):
        return Problem("Private state must be an absolute, dedicated directory outside the checkout and home root.")
    checkpoint_bytes: Final = (paths.kit / "checkpoint.json").read_bytes()
    checkpoint: Final = Checkpoint.model_validate_json(checkpoint_bytes)
    if platform.python_version() != checkpoint.python_version:
        return Problem("The gateway Python version differs from the checkpoint. Use bootstrap.sh setup to restore it.")
    source_problem: Final = verify_source(paths, checkpoint)
    if source_problem is not None:
        return source_problem
    checkpoint_sha256: Final = hashlib.sha256(checkpoint_bytes).hexdigest()
    if options.command in ("catalog", "setup"):
        token: Final = provided_token(options, environ)
        if isinstance(token, Problem):
            return token
        with httpx.Client() as client:
            catalog: Final = fetch_catalog(client, token, checkpoint.upstream_headers)
        if isinstance(catalog, Problem):
            return catalog
        if options.command == "catalog":
            sys.stdout.write(
                json.dumps(
                    [entry.model_dump(mode="json") for entry in catalog if entry.id.startswith("gpt-")], indent=2
                )
                + "\n"
            )
            return None
        with httpx.Client() as public_client:
            codex_prompt: Final = "" if options.claude_only else fetch_codex_prompt(public_client, paths, checkpoint)
        if isinstance(codex_prompt, Problem):
            return codex_prompt
        if options.claude_model is None:
            return Problem("--claude-model is required; the gateway never chooses your Claude Code backend silently.")
        selection: Final = Selection(
            codex_model=options.codex_model or checkpoint.default_codex_model,
            claude_model=options.claude_model,
            claude_only=options.claude_only,
            port=options.port,
            checkpoint=checkpoint.checkpoint,
            checkpoint_sha256=checkpoint_sha256,
        )
        configure_problem: Final = configure(paths, checkpoint, selection, token, catalog, codex_prompt)
        if configure_problem is not None:
            return configure_problem
        install_problem: Final = install_clients(paths, checkpoint, selection, environ)
        if install_problem is not None:
            return install_problem
        codex_status: Final = "" if selection.claude_only else f"Codex model: {selection.codex_model}; "
        sys.stdout.write(
            f"Configured {checkpoint.checkpoint} in {paths.state}\n"
            f"{codex_status}Claude Code model: {selection.claude_model}\n"
            "Credentials were saved with private permissions, not printed. Start the server with bootstrap.sh serve.\n"
        )
        return None
    selection_saved: Final = Selection.model_validate_json((paths.state / "selection.json").read_bytes())
    if selection_saved.checkpoint_sha256 != checkpoint_sha256:
        return Problem("The checkpoint changed since setup. Rerun setup before starting the server or clients.")
    if options.command == "configure-clients":
        return configure_standard_clients(paths, checkpoint, selection_saved, environ)
    return launch(paths, checkpoint, selection_saved, options, environ)


def main(argv: Sequence[str]) -> int:
    try:
        options: Final = parse_options(argv)
        outcome: Final = run(options, paths_from_environment(os.environ), os.environ)
    except OSError as error:
        sys.stderr.write(f"Gateway filesystem/process error: {error.strerror or type(error).__name__}.\n")
        return 1
    except (httpx.HTTPError, ValidationError, UnicodeError, subprocess.TimeoutExpired) as error:
        sys.stderr.write(f"Gateway setup failed ({type(error).__name__}). No credential or response body was logged.\n")
        return 1
    if isinstance(outcome, Problem):
        sys.stderr.write(outcome.message + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
