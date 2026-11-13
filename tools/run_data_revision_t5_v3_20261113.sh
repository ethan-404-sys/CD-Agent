#!/usr/bin/env bash
set -euo pipefail
REPO=${DATA_REVISION_REPO:-/guisongxia01/pangchao/wangyihan/wyh/CD-Agent}
OUTPUT=${DATA_REVISION_OUTPUT:-/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/data_revision_20261113/v2}
PYTHON=${DATA_REVISION_PYTHON:-/persist_data/home/guisongxia/anaconda3/envs/wellbeing/bin/python}
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
"$PYTHON" "$REPO/code/agent_baseline/analysis/test_data_revision_v3.py"
"$PYTHON" "$REPO/code/agent_baseline/analysis/revise_t5_review_v3.py" \
 --run /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run --output "$OUTPUT" \
 --review /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/trace_behavior_review_20261113 \
 --whu /guisongxia01/pangchao/haoxiangyu/rs_datasets/WHU-CD
