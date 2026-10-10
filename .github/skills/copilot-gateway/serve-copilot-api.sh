#!/usr/bin/env bash
set -euo pipefail
umask 077

export COPILOT_API_HOME="${COPILOT_API_HOME:-$HOME/.local/state/copilot-api-gateway}"
export COPILOT_API_OAUTH_APP=copilot-cli
unset COPILOT_API_GITHUB_TOKEN COPILOT_API_ENTERPRISE_URL

cd "$COPILOT_API_HOME/runtime/node_modules/@jeffreycao/copilot-api"
printf '%s\n' \
  '2b9022940f00485dd32fb4b7caf00f5c038fb3f1405498b327a55fe57258e52f  dist/token-kSGCOmTG.js' \
  '4b2dd00b0dacfc0178524576670c2c0e8ec16e8504608b53f73082a9a5840912  dist/copilot-runtime-Dg3ywZkl.js' \
  | sha256sum --check --status

exec node dist/main.js start --host 127.0.0.1 --port 4000
