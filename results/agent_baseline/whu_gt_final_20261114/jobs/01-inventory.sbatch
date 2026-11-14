#!/usr/bin/env bash
#SBATCH --partition=gre
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:10:00
#SBATCH --output=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114/logs/inventory-%j.out
#SBATCH --error=/guisongxia01/pangchao/wangyihan/wyh/exp-loop/experiments/cd-whu-final-20261114/logs/inventory-%j.err
set -euo pipefail
/usr/bin/python3 -B /guisongxia01/pangchao/wangyihan/wyh/CD-Agent/.runtime/whu-final-20261114/inventory.py
