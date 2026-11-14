#!/usr/bin/env bash
# Submit only through the experiment loop, after parent review.
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=01:00:00
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through exp-loop/sbatch}"
ROOT=/guisongxia01/pangchao/wangyihan
REPO=${T3_CODE:-$ROOT/wyh/cd-t3-bridge}
OUT=$ROOT/wyh/CD-Agent/results/agent_baseline/t3_bridge_20261114
R=$ROOT/wyh/exp6_staging/agent_baseline_run
HIST=$ROOT/wyh/exp6_staging/batch98_t3_routes/results
PY=$ROOT/conda_envs/exp5-env/bin/python
stage=${1:-smoke}
limit=3
k=4
if [[ "$stage" == dev60 ]]; then limit=60; k=8; fi
mkdir -p "$OUT/$stage" "$OUT/logs"
export OVCD_CODE=$R/code_w/ovcd_v0
export PYTHONPATH=$REPO/code:$OVCD_CODE
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
while IFS= read -r var; do if [[ ${var,,} == *proxy* ]]; then unset "$var"; fi; done < <(compgen -e)
started=$SECONDS
finish() {
    status=$?
    trap - EXIT
    QQ_SUB="T3 bridge $stage $SLURM_JOB_ID" \
    QQ_BODY="status=$status; elapsed=$((SECONDS-started))s; output=$OUT/$stage; key result/error: see summary.json and logs" \
      "$ROOT/.local/bin/wangyihan-send-qq" >/dev/null 2>&1 || true
    exit "$status"
}
trap finish EXIT
cd "$REPO"
"$PY" -B -m unittest agent_baseline.tests.test_t3_semantic_bridge
"$PY" "$ROOT/Chang-Agent/tools/run_with_bailian_csv.py" \
  "$ROOT/wyh/默认业务空间-apiKey-6214720.csv" \
  "$PY" -B "$REPO/code/t3_routes_bootstrap.py" agent_baseline.analysis.t3_semantic_bridge \
  --run "$R" --historical "$HIST" --output "$OUT/$stage" --limit "$limit" --verify-k "$k" \
  --stage "$stage" --metric "$OUT/metric.json"
