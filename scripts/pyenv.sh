#!/usr/bin/env zsh
# Install pyenv
curl -fsSL https://pyenv.run | bash
export PATH="$HOME/.pyenv/bin:$PATH"
export PYENV_ROOT="$HOME/.pyenv"
echo 'eval "$(pyenv init -)"' >> ~/.zshrc
eval "$(pyenv init -)"

pyenv install 3.9 3.10 3.11 3.12 3.13
