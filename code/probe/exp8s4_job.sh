#!/usr/bin/env bash
# 用 sbatch 指定分区、资源和本目录 logs 路径，参数原样传给渲染器。
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch44
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  printf '%s\n' '此脚本必须通过 sbatch 提交。' >&2
  exit 2
fi
started=$SECONDS
task_args="$*"
notify_finish() {
  status=$?
  trap - EXIT
  QQ_SUB="batch44 ${1:-阶段} 结束" \
  QQ_BODY="任务: ${task_args}; 状态码: ${status}; 耗时: $((SECONDS-started)) 秒; 工作目录: ${PWD}; 作业: ${SLURM_JOB_ID}; 结果或错误见本目录 logs/ 与 --out 目录。" \
  /guisongxia01/pangchao/wangyihan/.local/bin/wangyihan-send-qq || true
  exit "$status"
}
trap 'notify_finish' EXIT
export PYTHONDONTWRITEBYTECODE=1
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python exp8s4_render.py "$@"
