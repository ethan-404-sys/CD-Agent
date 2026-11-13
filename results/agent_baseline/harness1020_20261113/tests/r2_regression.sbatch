#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:10:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-harness1020-tests-20261113/logs/r2-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-harness1020-tests-20261113/logs/r2-%j.err
set -euo pipefail
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/p0r2/code
export OVCD_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code/ovcd_v0
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B -m unittest -v agent_baseline.tests.test_answer_state agent_baseline.tests.test_tools agent_baseline.tests.test_loop
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/p1r2/code
export OVCD_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code/ovcd_v0
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B -m unittest -v agent_baseline.tests.test_answer_state agent_baseline.tests.test_tools agent_baseline.tests.test_loop agent_baseline.tests.test_segment_pair
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/harness1020-20261113/p2r2/code
export OVCD_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code/ovcd_v0
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B -m unittest -v agent_baseline.tests.test_answer_state agent_baseline.tests.test_tools agent_baseline.tests.test_loop agent_baseline.tests.test_segment_pair agent_baseline.tests.test_progressive_tools
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B -m unittest -v agent_baseline.tests.test_harness1020_report
mkdir -p /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/harness1020_20261113/tests
/usr/bin/python3 -c 'import json; from pathlib import Path; Path("/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/harness1020_20261113/tests/metric.json").write_text(json.dumps(dict(stage="r2",passed=True,cost_cny=0)))'
