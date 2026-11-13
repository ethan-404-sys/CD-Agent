#!/usr/bin/env bash
# Existing verifier UI, separate review DB; no experiment submission or monitoring.
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REVIEW_PYTHON=${REVIEW_PYTHON:-/persist_data/home/guisongxia/anaconda3/envs/wellbeing/bin/python}
REVIEW_STATE=${REVIEW_STATE:-/tmp/cd-task-review-20261113}
export TASK_DATA_DB=${TASK_DATA_DB:-$REVIEW_STATE/review.sqlite}
export VERIFIER_DB=$TASK_DATA_DB
export CONSTRUCT_REVIEW_BACKUP=${CONSTRUCT_REVIEW_BACKUP:-$REPO/.runtime/task-review-20261113/review-backup.sqlite}
export PYTHONPATH="$REPO/code:$REPO/code/agent_baseline/analysis${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$REVIEW_STATE"
if [[ ! -e "$TASK_DATA_DB" && -f "$CONSTRUCT_REVIEW_BACKUP" ]]; then
    cp "$CONSTRUCT_REVIEW_BACKUP" "$TASK_DATA_DB"
fi
cd "$REPO/audit/verifier_ui"
exec "$REVIEW_PYTHON" -B -m uvicorn app:app --host 127.0.0.1 --port "${REVIEW_PORT:-8876}"
