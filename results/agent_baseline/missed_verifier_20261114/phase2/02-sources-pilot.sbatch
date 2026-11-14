#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=06:00:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-missed-verifier-20261114/logs/pilot-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-missed-verifier-20261114/logs/pilot-%j.err
set -euo pipefail
export PYTHONPATH=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/research-20261114/dev-v2/code:/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/code_w/ovcd_v0
export OVCD_CODE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/code_w/ovcd_v0
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
while IFS= read -r var; do if [[ ${var,,} == *proxy* ]]; then unset "$var"; fi; done < <(compgen -e)
PY=/guisongxia01/pangchao/wangyihan/conda_envs/exp6-v2-env/bin/python
"$PY" -B -m unittest -v agent_baseline.tests.test_missed_verifier_study agent_baseline.tests.test_find_missed agent_baseline.tests.test_missed_integration
BASE=(--run /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run --work /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch99_find_missed_pre/artifacts --out /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/missed_verifier_20261114/pilot)
"$PY" -B -m agent_baseline.analysis.missed_verifier_study online_selection "${BASE[@]}"
"$PY" -B -m agent_baseline.analysis.missed_verifier_study sources "${BASE[@]}"
"$PY" -B -m agent_baseline.analysis.missed_verifier_study prepare "${BASE[@]}" --images 10
for variant in four som structured; do
 "$PY" -B -m agent_baseline.run_with_key '/guisongxia01/pangchao/wangyihan/wyh/默认业务空间-apiKey-6214720.csv' "$PY" -B -m agent_baseline.analysis.missed_verifier_study verify "${BASE[@]}" --variant "$variant"
done
"$PY" -B -m agent_baseline.analysis.missed_verifier_study score "${BASE[@]}"
