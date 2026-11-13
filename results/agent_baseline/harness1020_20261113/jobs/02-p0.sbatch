#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=6
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-harness1020-20261113/logs/p0-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-harness1020-20261113/logs/p0-%j.err
set -euo pipefail
export AGENT_RUN=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/online
export AGENT_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/p0r2/code/agent_baseline
export OVCD_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code/ovcd_v0
export AGENT_STOP_FILE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/online/STOP.json
export BAILIAN_CSV='/guisongxia01/pangchao/wangyihan/wyh/默认业务空间-apiKey-6214720.csv'
export USE_ASK_WORKER=0 USE_LOCAL_WORKERS=1
report_results() {
    status=$?
    trap - EXIT
    set +e
    PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code /guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B -m agent_baseline.analysis.harness1020_report --run /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/online --selection /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/baseline_runtime/manifests/selection.json --output /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/harness1020_20261113 --config p0
    reported=$?
    if [[ "$status" == 0 ]]; then status=$reported; fi
    exit "$status"
}
trap report_results EXIT
bash /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/p0r2/code/agent_baseline/run_agent.sbatch --tasks /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/baseline_runtime/manifests/pilot.jsonl --split val --concurrency 2 --prompt-version v4 --seed-offset 1 --batch harness1020 --planner-model qwen3.7-flash --ask-backend api --ask-model qwen3.7-flash --ask-correspondence --pixel-thr 0.85 --status --verify-stage --gate3 --answer-state --config p0
