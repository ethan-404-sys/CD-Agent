#!/usr/bin/env bash
# Host cron fallback only: repair hooks, retry finalization. Never advance rounds.
set -uo pipefail
ROOT="${EXP_LOOP_ROOT:?Set the installed exp-loop directory}"
ENGINE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/loop.py"
mkdir -p "$ROOT/logs"
for exp in "$ROOT"/experiments/*/; do
    name="$(basename "$exp")"
    case "$name" in _*|.*) continue ;; esac
    [[ -f "$exp/state.json" ]] || continue
    /usr/bin/python3 "$ENGINE" tick "$exp" >> "$ROOT/logs/watchdog.log" 2>&1
    /usr/bin/python3 "$ENGINE" finalize "$exp" >> "$ROOT/logs/watchdog.log" 2>&1
done
