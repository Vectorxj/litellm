import hashlib
import json
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Final

import pytest
import tomllib
from gateway_config import Checkpoint, Paths, Problem, Selection
from native_config import configure_native_clients, native_codex_config, plan_native_clients

KIT: Final = Path(__file__).resolve().parent
CHECKPOINT_BYTES: Final = (KIT / "checkpoint.json").read_bytes()
CHECKPOINT: Final = Checkpoint.model_validate_json(CHECKPOINT_BYTES)
SELECTION: Final = Selection(
    codex_model="gpt-6-astra",
    claude_model="gpt-6-astra",
    port=14000,
    checkpoint=CHECKPOINT.checkpoint,
    checkpoint_sha256=hashlib.sha256(CHECKPOINT_BYTES).hexdigest(),
)
ORIGINAL_CODEX: Final = (
    (
        "# Keep this personal comment\n"
        'model_provider = "previous"\n'
        'model = "previous-model" # Keep this inline comment\n'
        'approval_policy = "on-request"\n'
        'sandbox_mode = "read-only"\n'
        "[model_providers.previous]\n"
        'name = "Previous backend"\n'
        'base_url = "https://previous.invalid/v1"\n'
        'env_key = "PREVIOUS_KEY"\n'
        "[features]\n"
        "shell_tool = true\n"
        '[projects."/example"]\n'
        'trust_level = "trusted"\n'
    )
    .replace("\n", "\r\n")
    .encode()
)
ORIGINAL_CLAUDE: Final = json.dumps(
    {
        "env": {"ANTHROPIC_AUTH_TOKEN": "previous-token-fixture", "KEEP_ME": "unchanged"},
        "permissions": {"allow": ["Read"], "deny": ["Bash(rm *)"], "defaultMode": "default"},
        "theme": "dark",
        "apiKeyHelper": "previous-helper",
    },
    indent=4,
).encode()


def write_originals(home: Path) -> None:
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir()
    (home / ".codex/config.toml").write_bytes(ORIGINAL_CODEX)
    (home / ".claude/settings.json").write_bytes(ORIGINAL_CLAUDE)


def test_native_settings_preserve_preferences_and_use_working_secret_helpers(tmp_path: Path) -> None:
    home: Final = tmp_path / "home"
    paths: Final = Paths(kit=KIT, state=tmp_path / "state with ' quote")
    write_originals(home)
    paths.state.mkdir(mode=0o700)
    paths.key.write_text("proxy-key-fixture\n")
    paths.key.chmod(0o600)
    plan: Final = plan_native_clients(paths, CHECKPOINT, SELECTION, {}, home)
    assert not isinstance(plan, Problem)
    assert (home / ".codex/config.toml").read_bytes() == ORIGINAL_CODEX
    backup: Final = configure_native_clients(paths, plan)
    assert isinstance(backup, Path)
    assert (backup / "codex-config.toml").read_bytes() == ORIGINAL_CODEX
    assert (backup / "claude-settings.json").read_bytes() == ORIGINAL_CLAUDE
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in backup.iterdir())

    codex_text: Final = (home / ".codex/config.toml").read_text()
    codex: Final = tomllib.loads(codex_text)
    original_codex: Final = tomllib.loads(ORIGINAL_CODEX.decode())
    assert "# Keep this personal comment" in codex_text
    assert "# Keep this inline comment" in codex_text
    assert codex["model"] == "gpt-6-astra"
    assert codex["model_provider"] == "copilot_gateway"
    assert codex["model_catalog_json"] == str(paths.state / "codex/model-catalog.json")
    assert codex["approval_policy"] == original_codex["approval_policy"]
    assert codex["sandbox_mode"] == original_codex["sandbox_mode"]
    assert codex["features"] == original_codex["features"]
    assert codex["projects"] == original_codex["projects"]
    assert codex["model_providers"]["previous"] == original_codex["model_providers"]["previous"]
    provider: Final = codex["model_providers"]["copilot_gateway"]
    assert provider["base_url"] == "http://127.0.0.1:14000/v1"
    assert "env_key" not in provider
    assert provider["auth"] == {"command": "/bin/cat", "args": [str(paths.key)]}
    auth_result: Final = subprocess.run(
        (provider["auth"]["command"], *provider["auth"]["args"]), capture_output=True, text=True, check=True
    )
    assert auth_result.stdout == "proxy-key-fixture\n"

    claude_text: Final = (home / ".claude/settings.json").read_text()
    claude: Final = json.loads(claude_text)
    assert claude["model"] == "gpt-6-astra"
    assert claude["theme"] == "dark"
    assert claude["env"]["KEEP_ME"] == "unchanged"
    assert claude["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:14000"
    assert claude["env"]["ANTHROPIC_AUTH_TOKEN"] == ""
    assert claude["permissions"] == {
        "allow": ["Read"],
        "deny": ["Bash(rm *)", "WebSearch"],
        "defaultMode": "default",
    }
    helper_result: Final = subprocess.run(
        shlex.split(claude["apiKeyHelper"]), capture_output=True, text=True, check=True
    )
    assert helper_result.stdout == "proxy-key-fixture\n"
    assert "proxy-key-fixture" not in codex_text + claude_text
    assert all(stat.S_IMODE(update.target.stat().st_mode) == 0o600 for update in plan)

    again: Final = plan_native_clients(paths, CHECKPOINT, SELECTION, {}, home)
    assert not isinstance(again, Problem)
    assert configure_native_clients(paths, again) is None
    assert tuple((paths.state / "client-backups").iterdir()) == (backup,)


@pytest.mark.parametrize(
    ("target", "invalid"),
    (
        (".codex/config.toml", b"[invalid"),
        (".codex/config.toml", b'model_providers = "not-a-table"\n'),
        (".claude/settings.json", b'{"env": null}'),
        (".claude/settings.json", b'{"permissions": {"deny": "not-a-list"}}'),
        (".claude/settings.json", b"invalid JSON"),
        (".claude/settings.json", b""),
    ),
)
def test_invalid_config_prevents_changes_to_either_client(tmp_path: Path, target: str, invalid: bytes) -> None:
    home: Final = tmp_path / "home"
    paths: Final = Paths(kit=KIT, state=tmp_path / "state")
    write_originals(home)
    (home / target).write_bytes(invalid)
    before: Final = tuple((home / name).read_bytes() for name in (".codex/config.toml", ".claude/settings.json"))
    result: Final = plan_native_clients(paths, CHECKPOINT, SELECTION, {}, home)
    assert isinstance(result, Problem)
    assert tuple((home / name).read_bytes() for name in (".codex/config.toml", ".claude/settings.json")) == before
    assert not paths.state.exists()


def test_concurrent_config_edit_is_not_overwritten(tmp_path: Path) -> None:
    home: Final = tmp_path / "home"
    paths: Final = Paths(kit=KIT, state=tmp_path / "state")
    write_originals(home)
    plan: Final = plan_native_clients(paths, CHECKPOINT, SELECTION, {}, home)
    assert not isinstance(plan, Problem)
    (home / ".codex/config.toml").write_bytes(ORIGINAL_CODEX + b"\n# New user edit\n")
    result: Final = configure_native_clients(paths, plan)
    assert isinstance(result, Problem)
    assert "changed during setup" in result.message
    assert (home / ".codex/config.toml").read_bytes().endswith(b"# New user edit\n")
    assert (home / ".claude/settings.json").read_bytes() == ORIGINAL_CLAUDE
    assert not paths.state.exists()


def test_symlinked_native_config_is_not_replaced(tmp_path: Path) -> None:
    home: Final = tmp_path / "home"
    paths: Final = Paths(kit=KIT, state=tmp_path / "state")
    (home / ".codex").mkdir(parents=True)
    original: Final = tmp_path / "managed.toml"
    original.write_bytes(ORIGINAL_CODEX)
    (home / ".codex/config.toml").symlink_to(original)
    result: Final = plan_native_clients(paths, CHECKPOINT, SELECTION, {}, home)
    assert isinstance(result, Problem)
    assert "symlinked" in result.message
    assert original.read_bytes() == ORIGINAL_CODEX
    assert (home / ".codex/config.toml").is_symlink()


def test_active_codex_profile_requires_explicit_configuration() -> None:
    result: Final = native_codex_config('profile = "work"\n', "", Path("/private/proxy-key"))
    assert isinstance(result, Problem)
    assert "active Codex profile" in result.message


def test_native_config_homes_follow_client_environment(tmp_path: Path) -> None:
    paths: Final = Paths(kit=KIT, state=tmp_path / "state")
    codex_home: Final = tmp_path / "custom-codex"
    claude_home: Final = tmp_path / "custom-claude"
    result: Final = plan_native_clients(
        paths,
        CHECKPOINT,
        SELECTION,
        {"CODEX_HOME": str(codex_home), "CLAUDE_CONFIG_DIR": str(claude_home)},
        tmp_path / "unused-home",
    )
    assert not isinstance(result, Problem)
    assert tuple(update.target for update in result) == (codex_home / "config.toml", claude_home / "settings.json")
