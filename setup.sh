#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [ ! -d ../f1-race-replay ] && [ ! -d .engine/f1-race-replay ]; then
  git clone https://github.com/IAmTomShaw/f1-race-replay.git .engine/f1-race-replay
  git -C .engine/f1-race-replay checkout efdb8a31900d64e3f269d66601ab9726b7dc5923
fi
if [ -d ../f1-race-replay ]; then
  engine=../f1-race-replay
else
  engine=.engine/f1-race-replay
fi
if [ ! -x "$engine/.venv/bin/python" ]; then
  python3 -m venv "$engine/.venv"
  "$engine/.venv/bin/python" -m pip install -r "$engine/requirements.txt"
fi
printf 'Ready. Run: .venv/bin/python launch.py\n'
