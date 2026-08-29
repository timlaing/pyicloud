#!/usr/bin/zsh
set -ex

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

if ! [ -x "$(command -v opencode)" ]; then
  echo 'export PATH=/home/vscode/.opencode/bin:$PATH' >> ~/.zshrc
fi

curl -fsSL --proto "=https" https://opencode.ai/install | bash

if ! [ -e /opt/sonarqube-mcp/sonarqube-mcp-server.jar ]; then
  sudo mkdir -p /opt/sonarqube-mcp
  sudo curl -L -o /opt/sonarqube-mcp/sonarqube-mcp-server.jar "https://binaries.sonarsource.com/Distribution/sonarqube-mcp-server/sonarqube-mcp-server-1.25.0.3221.jar"
fi
