#!/usr/bin/env bash
set -euo pipefail
REPO=${DATA_REVISION_REPO:-/guisongxia01/pangchao/wangyihan/wyh/CD-Agent}
SOURCE=${DATA_REVISION_OUTPUT:-/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/data_revision_20261113/v2}
PYTHON=${DATA_REVISION_PYTHON:-/persist_data/home/guisongxia/anaconda3/envs/wellbeing/bin/python}
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
"$PYTHON" "$REPO/code/agent_baseline/analysis/revise_data_review_pages.py" --source "$SOURCE" --output "$SOURCE/review_v2_19" \
 --run /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run \
 --whu /guisongxia01/pangchao/haoxiangyu/rs_datasets/WHU-CD
