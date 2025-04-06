#!/bin/bash
set -euo pipefail

mkdir -p dist
rm -rf dist/*
python -m build
