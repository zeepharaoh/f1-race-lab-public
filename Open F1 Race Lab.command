#!/bin/zsh
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Run sh setup.sh once to install the application.'
  read '?Press Enter to close.'
  exit 1
fi
.venv/bin/python launch.py
