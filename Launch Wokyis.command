#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x "build/Wokyis Console.app/Contents/MacOS/Wokyis" ]]; then
  python3 scripts/build_app.py
fi
python3 scripts/run_app.py
