#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=00:15:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-research-validation-20261114/logs/validate-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-research-validation-20261114/logs/validate-%j.err
set -euo pipefail
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/online-v1/code:/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/online-v1/code/ovcd_v0
export OVCD_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/online-v1/code/ovcd_v0
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export BAILIAN_CSV=/guisongxia01/pangchao/wangyihan/wyh/默认业务空间-apiKey-6214720.csv
PY=/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python
"$PY" -B -m unittest -v agent_baseline.tests.test_t3_semantic_bridge agent_baseline.tests.test_t3_exemplar_geometry_audit agent_baseline.tests.test_missed_online_runner
"$PY" -B -m agent_baseline.analysis.t3_bridge_preflight --repo /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/online-v1 --tasks /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch98_t3_routes/results/agent10_tasks.jsonl --output /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/research_validation_20261114/t3_online_preflight.json
"$PY" -B -m agent_baseline.analysis.t3_exemplar_geometry_audit --run /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run --semantic-root /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/t3_bridge_20261114/dev60 --output /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/research_validation_20261114/geometry_partial
"$PY" -c 'import json; from pathlib import Path; Path("/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/research_validation_20261114/metric.json").write_text(json.dumps(dict(stage="online_code_validation", cost_cny=0, completed=1, failed=0, result_path="/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/research_validation_20261114")))'
