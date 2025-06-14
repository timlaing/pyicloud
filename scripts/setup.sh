#!/usr/bin/zsh
set -e

uv venv && uv pip install -r requirements_all.txt
grep -qxF 'source /workspaces/pyicloud/.venv/bin/activate' ~/.zshrc || cat <<EOF >>~/.zshrc
if [ -f "/workspaces/pyicloud/.venv/bin/activate" ]; then
  source /workspaces/pyicloud/.venv/bin/activate
fi
EOF
