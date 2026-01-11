#!/usr/bin/zsh
set -e

export UV_LINK_MODE=copy
uv venv --seed --clear && uv pip install -r requirements_all.txt
grep -qx "source \"${WORKSPACE_DIRECTORY}/.venv/bin/activate\"" ~/.zshrc || cat <<EOF >>~/.zshrc
if [ -f "${WORKSPACE_DIRECTORY}/.venv/bin/activate" ]; then
  source "${WORKSPACE_DIRECTORY}/.venv/bin/activate"
fi
EOF
