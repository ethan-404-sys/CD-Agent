#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:10:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114/logs/score-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114/logs/score-%j.err
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/wyh/CD-Agent
export PYTHONPATH="$PWD/code:$PWD/code/ovcd_v0" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
bash tools/run_whu_gt_final_20261114.sh --cleanup
/usr/bin/python3 - <<'ARCHIVE'
import json,re,shutil
from pathlib import Path
exp=Path('/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114')
out=Path('results/agent_baseline/whu_gt_final_20261114')
state=json.loads((exp/'state.json').read_text());last=state['history'][-1]
assert json.loads((out/'summary.json').read_text())['cleanup_complete']
assert not Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/whu/gt').exists()
log=(exp/'logs'/('final-'+last['job_id']+'.err')).read_text()
assert re.search(r'Ran 80 tests',log) and '\nOK\n' in log
for folder in ('logs','events','jobs'):
    shutil.copytree(exp/folder,out/folder,dirs_exist_ok=True)
shutil.copy2(exp/'metric.py',out/'metric_reader.py')
(out/'test_summary.json').write_text(json.dumps(dict(tests=80,passed=True,job_id=last['job_id'],log='logs/final-'+last['job_id']+'.err',api_calls=0),indent=2)+'\n')
p=out/'metric.json';m=json.loads(p.read_text());m.update(stage='final_archive',regression_passed=True,tests=80);p.write_text(json.dumps(m)+'\n')
print(json.dumps(dict(archived=True,tests=80,cost_cny=0)))
ARCHIVE
