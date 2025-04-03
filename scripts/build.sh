#!/bin/bash
set -euo pipefail

mkdir -p dist
rm -f dist/*
python -m build
