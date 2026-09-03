#!/usr/bin/zsh
set -e
set -o pipefail

if [ -n "${PYICLOUD_TRACE:-}" ]; then
  set -x
fi

cd "$(dirname "$0")/.."

export UV_LINK_MODE=copy

if [ ! -n "$VIRTUAL_ENV" ]; then
  source .venv/bin/activate
fi

echo "Installing development dependencies..."
uv pip install \
  -e . \
  -r requirements_all.txt \
  --upgrade \
  --config-settings editable_mode=compat

npm install
