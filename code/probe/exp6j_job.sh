#!/usr/bin/env bash
# sbatch supplies resources and log paths; this wrapper executes one stage and notifies on exit.
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch45
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  echo 'Submit this script using sbatch; computation is not allowed on the management node.' >&2
  exit 2
fi
engine="${1:?cpu, internvl, or qwen required}"
shift
case "$engine" in
  cpu) exp6j_python=/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python ;;
  internvl) exp6j_python=/guisongxia01/pangchao/wangyihan/conda_envs/qwen3vl-sft-env/bin/python ;;
  qwen) exp6j_python=/guisongxia01/pangchao/wangyihan/conda_envs/vlm-vs-onejev-env/bin/python ;;
  *) echo 'Unknown environment' >&2; exit 2 ;;
esac
started=$SECONDS
finish() {
  status=$?
  trap - EXIT
  QQ_SUB="exp6j ${SLURM_JOB_NAME:-stage} job ${SLURM_JOB_ID}" \
  QQ_BODY="exit=${status}; elapsed=$((SECONDS-started))s; command=$*; artifacts=$PWD/run; logs=$PWD/logs; check stderr for errors" \
    /guisongxia01/pangchao/wangyihan/.local/bin/wangyihan-send-qq || echo 'QQ notification failed' >&2
  exit "$status"
}
trap 'finish "$@"' EXIT
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$PWD/.tmp/${SLURM_JOB_ID}"
mkdir -p "$TMPDIR"
"$exp6j_python" -B "$@"
