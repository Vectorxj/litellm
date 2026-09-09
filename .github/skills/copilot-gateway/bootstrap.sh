#!/usr/bin/env bash
set -euo pipefail
umask 077

kit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$kit_dir/../../.." && pwd)"
state_dir="${COPILOT_GATEWAY_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}/litellm-copilot-gateway}"
python_version="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["python_version"])' "$kit_dir/checkpoint.json")"

python3 - "$state_dir" "$repo_dir" <<'PY'
import os
import sys
from pathlib import Path

state = Path(sys.argv[1])
repo = Path(sys.argv[2]).resolve()
resolved = state.resolve()
if not state.is_absolute() or state.is_symlink() or resolved in (Path("/"), Path.home(), repo) or repo in resolved.parents:
    sys.exit("Use an absolute, dedicated private state directory outside the checkout and home root.")
if state.exists() and (not state.is_dir() or state.stat().st_uid != os.getuid()):
    sys.exit("Private state must be a directory owned by the current user.")
PY

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'uv is required. See this directory README for the pinned installation command.' >&2
    exit 1
fi

export UV_PROJECT_ENVIRONMENT="$state_dir/venv"
export LITELLM_MODE=PRODUCTION
export LITELLM_LOCAL_MODEL_COST_MAP=True

if [[ "${1:-}" == "setup" || ! -x "$UV_PROJECT_ENVIRONMENT/bin/python" ]]; then
    uv sync --quiet --project "$repo_dir" --frozen --no-dev --extra proxy --group proxy-dev --python "$python_version"
fi

exec "$UV_PROJECT_ENVIRONMENT/bin/python" "$kit_dir/gateway.py" "$@"
