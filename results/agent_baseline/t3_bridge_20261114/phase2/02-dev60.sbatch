#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=06:00:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-t3-bridge-20261114/logs/dev60-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-t3-bridge-20261114/logs/dev60-%j.err
set -euo pipefail
export T3_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/dev-v2
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/dev-v2/code:/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/code_w/ovcd_v0
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B -m unittest -v agent_baseline.tests.test_loop agent_baseline.tests.test_tools_t13 agent_baseline.tests.test_missed_integration agent_baseline.tests.test_find_missed
bash /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/dev-v2/tools/run_t3_bridge_20261114.sh dev60
