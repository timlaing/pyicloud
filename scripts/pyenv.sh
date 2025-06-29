#!/usr/bin/env zsh
# Install pyenv
if ! command -v pyenv &> /dev/null; then
  echo "pyenv not found, installing..."
    curl -fsSL https://pyenv.run | bash
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$HOME/.pyenv/bin:$PATH"
    eval "$($HOME/.pyenv/bin/pyenv init -)"

    grep -qxF 'eval "$($HOME/.pyenv/bin/pyenv init -)"' ~/.zshrc || cat <<EOF >>~/.zshrc
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$HOME/.pyenv/bin:$PATH"
eval "$($HOME/.pyenv/bin/pyenv init -)"
EOF
  echo "pyenv installed successfully."
else
  echo "pyenv already installed."
fi

pyenv install -sv 3.10 3.11 3.12 3.13
pyenv local 3.10 3.11 3.12 3.13
