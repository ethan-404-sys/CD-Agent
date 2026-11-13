#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:20:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-harness1020-20261113/logs/archive-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-harness1020-20261113/logs/archive-%j.err
set -euo pipefail
/usr/bin/python3 -B /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/code/agent_baseline/analysis/archive_harness1020.py --output /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/results/agent_baseline/harness1020_20261113
