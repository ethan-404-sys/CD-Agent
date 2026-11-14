#!/usr/bin/env bash
# One arm/seed per exp-loop job; no nested submission or queue polling.
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=6
#SBATCH --mem=64G
#SBATCH --time=03:00:00
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through exp-loop/sbatch}"
arm=${1:?baseline or bridge}
seed=${2:?1016, 2027 or 3038}
[[ "$arm" == baseline || "$arm" == bridge ]]
[[ "$seed" == 1016 || "$seed" == 2027 || "$seed" == 3038 ]]
ROOT=/guisongxia01/pangchao/wangyihan
REPO=${T3_CODE:-$ROOT/wyh/cd-t3-bridge}
OUT=$ROOT/wyh/CD-Agent/results/agent_baseline/t3_bridge_20261114
TASKS=$ROOT/wyh/exp6_staging/batch98_t3_routes/results/agent10_tasks.jsonl
export AGENT_RUN=$OUT/online AGENT_CODE=$REPO/code/agent_baseline
export OVCD_CODE=$REPO/code/ovcd_v0
export PYTHONPATH=$REPO/code:$OVCD_CODE
export BAILIAN_CSV=$ROOT/wyh/默认业务空间-apiKey-6214720.csv
export AGENT_STOP_FILE=$OUT/online/STOP.json
export USE_ASK_WORKER=0 USE_LOCAL_WORKERS=1
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
PY=$ROOT/conda_envs/exp5-env/bin/python
report() {
    status=$?
    trap - EXIT
    "$PY" -B -m agent_baseline.analysis.t3_bridge_online_metrics --output "$OUT" --tasks "$TASKS" || true
    exit "$status"
}
trap report EXIT
"$PY" -B -m unittest agent_baseline.tests.test_t3_semantic_bridge
extra=()
if [[ "$arm" == bridge ]]; then extra+=(--t3-semantic-bridge); fi
bash "$REPO/code/agent_baseline/run_agent.sbatch" \
    --tasks "$TASKS" --split val --concurrency 1 --prompt-version v4 --seed-offset "$seed" \
    --batch t3_bridge_20261114 --planner-model qwen3.7-flash --ask-backend api --ask-model qwen3.7-flash \
    --ask-correspondence --pixel-thr 0.85 --status --verify-stage --gate3 \
    --tools-t13 all --vision-config "$REPO/code/agent_baseline/vision_t13.example.json" \
    --config "${arm}_s${seed}" "${extra[@]}"
