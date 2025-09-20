#!/usr/bin/zsh
set -e

export UV_LINK_MODE=copy
uv venv --seed --clear && uv pip install -r requirements_all.txt
grep -qxF 'source /workspaces/pyicloud/.venv/bin/activate' ~/.zshrc || cat <<EOF >>~/.zshrc
if [ -f "/workspaces/pyicloud/.venv/bin/activate" ]; then
  source /workspaces/pyicloud/.venv/bin/activate
fi
EOF
