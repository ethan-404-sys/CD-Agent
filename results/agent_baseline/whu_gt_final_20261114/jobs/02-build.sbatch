#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114/logs/build-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114/logs/build-%j.err
set -euo pipefail
bash /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/tools/run_whu_gt_final_20261114.sh
