#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=01:00:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-t3-bridge-20261114/logs/smoke-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-t3-bridge-20261114/logs/smoke-%j.err
set -euo pipefail
export T3_CODE=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/t3-bridge-20261114/smoke-v1
bash /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/t3-bridge-20261114/smoke-v1/tools/run_t3_bridge_20261114.sh smoke
