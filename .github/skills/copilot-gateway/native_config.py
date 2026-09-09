import json
import os
import shlex
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import starmap
from pathlib import Path
from typing import Final, Literal

import tomlkit
from gateway_config import Checkpoint, Paths, Problem, Selection, claude_environment, codex_configuration, selected_pins
from gateway_files import private_directory, write_private, write_private_bytes
from pydantic import JsonValue, TypeAdapter, ValidationError
from tomlkit.exceptions import ParseError
from tomlkit.items import InlineTable, Table

JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
CODEX_ROUTE_FIELDS: Final = (
    "model",
    "model_provider",
    "model_catalog_json",
    "model_reasoning_effort",
    "model_context_window",
    "model_auto_compact_token_limit",
    "web_search",
)


@dataclass(frozen=True, slots=True)
class ClientUpdate:
    client: Literal["codex", "claude"]
    target: Path
    original: bytes | None = field(repr=False)
    rendered: str = field(repr=False)


def native_codex_config(original: str, generated: str, key_path: Path) -> str | Problem:
    try:
        document: Final = tomlkit.parse(original)
        gateway: Final = tomlkit.parse(generated)
    except ParseError:
        return Problem("Codex configuration is not valid TOML. No client settings were modified.")
    if "profile" in document:
        return Problem("An active Codex profile overrides config.toml. Configure that profile explicitly instead.")
    providers: Final = document.item("model_providers") if "model_providers" in document else tomlkit.table()
    if not isinstance(providers, (Table, InlineTable)):
        return Problem("Codex model_providers must be a table. No client settings were modified.")
    generated_values: Final = JSON_OBJECT.validate_python(gateway.unwrap())
    generated_providers: Final = generated_values.get("model_providers")
    if not isinstance(generated_providers, dict) or not isinstance(
        generated_provider := generated_providers.get("copilot_gateway"), dict
    ):
        return Problem("The generated Codex provider is invalid. Rerun setup.")
    provider_values: Final[dict[str, JsonValue]] = {
        **{key: value for key, value in generated_provider.items() if key != "env_key"},
        "auth": {"command": "/bin/cat", "args": [str(key_path)]},
    }
    for key in CODEX_ROUTE_FIELDS:
        document[key] = generated_values[key]
    providers["copilot_gateway"] = provider_values
    document["model_providers"] = providers
    return document.as_string()


def native_claude_config(original: str | None, selection: Selection, key_path: Path) -> str | Problem:
    try:
        existing: Final = JSON_OBJECT.validate_json(original) if original is not None else {}
    except ValidationError:
        return Problem("Claude Code settings are not a valid JSON object. No client settings were modified.")
    environment: Final = existing.get("env", {})
    permissions: Final = existing.get("permissions", {})
    if not isinstance(environment, dict) or not isinstance(permissions, dict):
        return Problem("Claude Code env and permissions must be objects. No client settings were modified.")
    denied: Final = permissions.get("deny", [])
    if not isinstance(denied, list) or not all(isinstance(item, str) for item in denied):
        return Problem("Claude Code permissions.deny must be a list of strings. No client settings were modified.")
    merged: Final[dict[str, JsonValue]] = {
        **existing,
        "model": selection.claude_model,
        "apiKeyHelper": shlex.join(("/bin/cat", str(key_path))),
        "env": {
            **environment,
            **claude_environment(selection),
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_API_KEY": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_USE_BEDROCK": "",
            "CLAUDE_CODE_USE_VERTEX": "",
            "CLAUDE_CODE_USE_FOUNDRY": "",
            "CLAUDE_CODE_USE_MANTLE": "",
        },
        "permissions": {**permissions, "deny": denied if "WebSearch" in denied else [*denied, "WebSearch"]},
    }
    return json.dumps(merged, indent=2) + "\n"


def configuration_bytes(path: Path, repo: Path) -> bytes | None | Problem:
    if not path.is_absolute() or path.resolve() == repo or repo in path.resolve().parents:
        return Problem("Native client settings must be outside the checkout.")
    if path.is_symlink() or path.parent.is_symlink():
        return Problem(f"Refusing a symlinked native configuration: {path}")
    if path.parent.exists() and (not path.parent.is_dir() or path.parent.stat().st_uid != os.getuid()):
        return Problem(f"The native configuration directory must be owned by you: {path.parent}")
    if not path.exists():
        return None
    metadata: Final = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or not metadata.st_mode & stat.S_IWUSR:
        return Problem(f"Native configuration must be an owned, writable regular file: {path}")
    return path.read_bytes()


def plan_native_clients(
    paths: Paths,
    checkpoint: Checkpoint,
    selection: Selection,
    environ: Mapping[str, str],
    home: Path,
) -> tuple[ClientUpdate, ...] | Problem:
    pins: Final = selected_pins(checkpoint, selection)
    if isinstance(pins, Problem):
        return pins
    claude_target: Final = (
        Path(environ.get("CLAUDE_CONFIG_DIR") or str(home / ".claude")).expanduser() / "settings.json"
    )
    claude_original: Final = configuration_bytes(claude_target, paths.repo)
    if isinstance(claude_original, Problem):
        return claude_original
    claude_rendered: Final = native_claude_config(
        claude_original.decode("utf-8") if claude_original is not None else None, selection, paths.key
    )
    if isinstance(claude_rendered, Problem):
        return claude_rendered
    claude_update: Final = ClientUpdate("claude", claude_target, claude_original, claude_rendered)
    if selection.claude_only:
        return (claude_update,)
    codex_target: Final = Path(environ.get("CODEX_HOME") or str(home / ".codex")).expanduser() / "config.toml"
    codex_original: Final = configuration_bytes(codex_target, paths.repo)
    if isinstance(codex_original, Problem):
        return codex_original
    codex_pin: Final = next(pin for pin in pins if pin.id == selection.codex_model)
    generated: Final = codex_configuration(checkpoint, selection, codex_pin, paths.state / "codex/model-catalog.json")
    codex_rendered: Final = native_codex_config(
        codex_original.decode("utf-8") if codex_original is not None else "", generated, paths.key
    )
    if isinstance(codex_rendered, Problem):
        return codex_rendered
    return (
        ClientUpdate("codex", codex_target, codex_original, codex_rendered),
        claude_update,
    )


def configure_native_clients(paths: Paths, updates: tuple[ClientUpdate, ...]) -> Path | Problem | None:
    current: Final = tuple(configuration_bytes(update.target, paths.repo) for update in updates)
    error: Final = next((value for value in current if isinstance(value, Problem)), None)
    if error is not None:
        return error
    if any(value != update.original for value, update in zip(current, updates)):
        return Problem(
            "A client configuration changed during setup. Nothing was modified; retry after closing clients."
        )
    changed: Final = tuple(
        update
        for update in updates
        if update.original != update.rendered.encode("utf-8")
        or (update.target.exists() and stat.S_IMODE(update.target.stat().st_mode) != 0o600)
    )
    if not changed:
        return None
    backup_root: Final = paths.state / "client-backups"
    directory_problem: Final = private_directory(backup_root)
    if directory_problem is not None:
        return directory_problem
    backup: Final = Path(tempfile.mkdtemp(prefix="native-", dir=backup_root))
    backups: Final = tuple(
        (backup / f"{update.client}-{update.target.name}", update.original)
        for update in changed
        if update.original is not None
    )
    backup_problem: Final = next(filter(None, starmap(write_private_bytes, backups)), None)
    if backup_problem is not None:
        return backup_problem
    for update in changed:
        update.target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (write_problem := write_private(update.target, update.rendered)) is not None:
            return Problem(f"{write_problem.message} Original files are backed up in {backup}.")
    return backup
