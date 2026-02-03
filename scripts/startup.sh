#!/usr/bin/zsh
set -ex

cd "$(realpath "$(dirname "$0")/..")"

export UV_LINK_MODE=copy

echo "Installing development dependencies..."
uv pip install \
  -e . \
  -r requirements_all.txt \
  --upgrade \
  --config-settings editable_mode=compat
