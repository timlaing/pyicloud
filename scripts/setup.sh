#!/usr/bin/zsh
set -e
set -o pipefail

if [ -n "${PYICLOUD_TRACE:-}" ]; then
  set -x
fi

cd "$(dirname "$0")/.."

export UV_LINK_MODE=copy

if [ ! -n "$VIRTUAL_ENV" ]; then
  rm -rf .venv || true
  if [ -x "$(command -v uv)" ]; then
    uv venv .venv
  else
    python3 -m venv .venv
  fi
  source .venv/bin/activate
fi

if ! [ -x "$(command -v uv)" ]; then
  python3 -m pip install uv
fi

scripts/startup.sh

prek install -f

# Optional maintainer tooling (opencode CLI + SonarQube MCP server). Opt-in so a
# plain contributor bootstrap never installs remote tools via root or edits the
# user's shell rc file as a side effect.
if [ "${PYICLOUD_INSTALL_DEV_TOOLS:-0}" = "1" ]; then
  if ! [ -x "$(command -v opencode)" ]; then
    if [ -f "${HOME}/.zshrc" ] && ! grep -q "${HOME}/.opencode/bin" "${HOME}/.zshrc"; then
      echo 'export PATH="$HOME/.opencode/bin:$PATH"' >> "${HOME}/.zshrc"
    fi
  fi

  curl -fsSL --proto "=https" https://opencode.ai/install | bash

  if ! [ -e /opt/sonarqube-mcp/sonarqube-mcp-server.jar ]; then
    sudo mkdir -p /opt/sonarqube-mcp
    sudo curl -L -o /opt/sonarqube-mcp/sonarqube-mcp-server.jar "https://binaries.sonarsource.com/Distribution/sonarqube-mcp-server/sonarqube-mcp-server-1.25.0.3221.jar"
  fi
fi
