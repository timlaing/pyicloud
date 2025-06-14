#!/usr/bin/zsh
# Install pyenv
curl -fsSL https://pyenv.run | bash
export PATH="$HOME/.pyenv/bin:$PATH"
export PYENV_ROOT="$HOME/.pyenv"
~/.pyenv/bin/pyenv init >> ~/.zshrc 2>&1
eval "$(pyenv init - zsh)"

pyenv install 3.9 3.10 3.11 3.12 3.13
