#!/usr/bin/env bash
# Slurm worker only; all computation stays on the allocated node.
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch43
[[ -n "${SLURM_JOB_ID:-}" ]] || { echo 'Run this worker through sbatch' >&2; exit 2; }
stage=$1; dataset=$2; output=$3; limit=$4; rule=$5; shards=$6
shift 6
PY=/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$output/_matplotlib"
common=(--dataset "$dataset" --out "$output" --limit "$limit")
methods=(M0 D O M1 M1-s1021 M1-s1022 M2 M3 M1-25 M1-25-s1021 M1-25-s1022 M1-50 M1-50-s1021 M1-50-s1022 M1-ign3 M1-pF M1-p25)
case "$stage" in
    init) "$PY" -B exp8s3_units.py init "${common[@]}" --rule "$rule" ;;
    units) "$PY" -B exp8s3_units.py units "${common[@]}" --shards "$shards" --shard "${SLURM_ARRAY_TASK_ID:?}" ;;
    train) method=$1; shift; "$PY" -B exp8s3_train.py train "${common[@]}" --method "$method" "$@" ;;
    rank) shift; "$PY" -B exp8s3_train.py rank "${common[@]}" --method "${methods[${SLURM_ARRAY_TASK_ID:?}]}" "$@" ;;
    curves|points) "$PY" -B exp8s3_evaluate.py "$stage" "${common[@]}" --shards "$shards" --shard "${SLURM_ARRAY_TASK_ID:?}" ;;
    merge) "$PY" -B exp8s3_evaluate.py merge "${common[@]}" ;;
    report) "$PY" -B exp8s3_report.py "${common[@]}" ;;
    notify)
        ids=$(awk -v self="$SLURM_JOB_ID" '$2 != self {print $2}' "$output/logs/jobs.tsv" | paste -sd, -)
        sacct -j "$ids" --format=JobID,JobName%35,State,Elapsed,MaxRSS --parsable2 > "$output/logs/accounting.txt"
        "$PY" -B exp8s3_finish.py --out "$output"
        ;;
    *) echo "Unknown stage: $stage" >&2; exit 2 ;;
esac
