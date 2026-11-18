#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ "$1" = "--app" ] && [ ! -t 2 ]; then  # started from the app menu: nothing shows errors, so keep a log
  mkdir -p "$HOME/.local/state/bench-wizard"
  exec python3 -m bench_wizard "$@" </dev/null >>"$HOME/.local/state/bench-wizard/app.log" 2>&1
fi
exec python3 -m bench_wizard "$@"
