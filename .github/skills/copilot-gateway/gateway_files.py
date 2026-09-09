import os
import stat
import tempfile
from pathlib import Path
from typing import Final

from gateway_config import Problem
from pydantic import SecretStr


def private_directory(path: Path) -> Problem | None:
    if path.is_symlink():
        return Problem(f"Refusing a symlink for private state: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        return Problem(f"Private state must be owned by the current user: {path}")
    path.chmod(0o700)
    return None


def write_private_bytes(path: Path, content: bytes) -> Problem | None:
    if path.is_symlink():
        return Problem(f"Refusing to replace a symlink: {path}")
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as output:
        temporary: Final = Path(output.name)
        try:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return None


def write_private(path: Path, content: str) -> Problem | None:
    return write_private_bytes(path, content.encode("utf-8"))


def read_secret(path: Path) -> SecretStr | Problem:
    if path.is_symlink() or not path.is_file():
        return Problem(f"Expected a private regular credential file: {path}")
    metadata: Final = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        return Problem(f"Credential file must be owned by you and have mode 0600 or 0400: {path}")
    value: Final = path.read_text(encoding="utf-8").strip()
    if not value or any(character.isspace() for character in value):
        return Problem("Credential must be one nonempty token with no whitespace.")
    return SecretStr(value)
