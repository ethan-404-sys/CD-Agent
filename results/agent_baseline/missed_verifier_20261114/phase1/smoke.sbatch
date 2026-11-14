#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --nodelist=gpu42
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-missed-verifier-20261114/logs/smoke-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-missed-verifier-20261114/logs/smoke-%j.err
set -euo pipefail
export BAILIAN_CSV='/guisongxia01/pangchao/wangyihan/wyh/默认业务空间-apiKey-6214720.csv'
bash /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/missed-verifier-20261114/smoke-v1/code/agent_baseline/analysis/run_missed_verifier_study.sbatch smoke
