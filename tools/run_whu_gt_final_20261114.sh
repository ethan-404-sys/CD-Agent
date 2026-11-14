#!/usr/bin/env bash
set -euo pipefail
REPO=${WHU_REPAIR_REPO:-/guisongxia01/pangchao/wangyihan/wyh/CD-Agent}
PYTHON=${WHU_REPAIR_PYTHON:-/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python}
export PYTHONPATH="$REPO/code:$REPO/code/ovcd_v0"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
cd "$REPO"
if [[ "${1:-}" != --cleanup ]]; then
 "$PYTHON" -B -m unittest -v agent_baseline.tests.test_whu_gt_final agent_baseline.tests.test_summary agent_baseline.tests.test_generalize agent_baseline.tests.test_second agent_baseline.tests.test_pyes_band agent_baseline.tests.test_loop
fi
"$PYTHON" -B -m agent_baseline.analysis.finalize_whu_gt \
 --repo "$REPO" --dataset /guisongxia01/pangchao/haoxiangyu/rs_datasets/WHU-CD \
 --run /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run \
 --output "$REPO/results/agent_baseline/whu_gt_final_20261114" "$@"
