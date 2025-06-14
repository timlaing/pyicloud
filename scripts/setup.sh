#!/usr/bin/zsh
set -e

uv venv && uv pip install -r requirements_all.txt
cat <<EOF >>~/.zshrc
if [ -f "/workspaces/pyicloud/.venv/bin/activate" ]; then
  source /workspaces/pyicloud/.venv/bin/activate
fi
EOF
