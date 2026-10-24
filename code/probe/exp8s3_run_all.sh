#!/usr/bin/env bash
# Example Slurm dependency chain. Default prints commands; --submit actually submits.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
MODE=${1:---plan}
[[ "$MODE" == --plan || "$MODE" == --submit ]] || { echo 'usage: bash run_all.sh [--plan|--submit]' >&2; exit 2; }
PY=/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python
OUT=${OUT:-run}
RULE=${RULE:-../batch42/run/frozen_rule.json}
PARTITION=${PARTITION:-}
SHARDS=${SHARDS:-16}
CONCURRENT=${CONCURRENT:-4}
TRAIN_CONCURRENT=${TRAIN_CONCURRENT:-3}
LIMIT=${LIMIT:-0}
RULE_JOB_ID=${RULE_JOB_ID:-}
[[ "$SHARDS" =~ ^[1-9][0-9]*$ && "$CONCURRENT" =~ ^[1-9][0-9]*$ && "$TRAIN_CONCURRENT" =~ ^[1-9][0-9]*$ && "$LIMIT" =~ ^[0-9]+$ ]] || { echo 'Invalid numeric settings' >&2; exit 2; }
OUT=$("$PY" -B -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]).resolve(); h=pathlib.Path.cwd(); assert p != h and p.is_relative_to(h), "OUT must be within batch43"; print(p)' "$OUT")
if [[ "$MODE" == --submit ]]; then
    [[ -n "$PARTITION" ]] || { echo 'Set PARTITION to a checked available CPU partition' >&2; exit 2; }
    ping=$(scontrol ping 2>&1) || { printf '%s\n' "$ping" >&2; exit 1; }
    [[ "$ping" == *'is UP'* && "$ping" != *'is DOWN'* && "$ping" != *'Error creating slurm stream socket'* ]] || { printf '%s\n' "$ping" >&2; exit 1; }
    squeue -u guisongxia -o '%.18i %.24j %.8T %.12M %.20R' >&2
    sinfo -p "$PARTITION" -o "%P %a %l %D %t" >&2 || true
    [[ -f "$RULE" || -n "$RULE_JOB_ID" ]] || { echo 'Frozen rule missing; set RULE_JOB_ID to batch42 select job' >&2; exit 1; }
    if [[ -z "${RESUME_UNITS:-}" ]]; then
    [[ ! -e "$OUT/second/manifest.json" && ! -e "$OUT/sysu/manifest.json" ]] || { echo 'Output already initialized; use a new OUT to avoid duplicate jobs' >&2; exit 1; }
    [[ ! -s "$OUT/logs/jobs.tsv" ]] || { echo 'Jobs already submitted for this OUT; inspect logs/jobs.tsv' >&2; exit 1; }
    fi
    mkdir -p "$OUT/logs"
fi
methods=(M0 D O M1 M1-s1021 M1-s1022 M2 M3 M1-25 M1-25-s1021 M1-25-s1022 M1-50 M1-50-s1021 M1-50-s1022 M1-ign3 M1-pF M1-p25)
extra=()
if (( LIMIT > 0 )); then extra=(--epochs 2 --patience 1 --hgb-iterations 2 --bootstrap 20); fi
# REPLY carries job ID in this shell; dry-run IDs are symbolic placeholders.
submit() {
    local name=$1 dep=$2 memory=$3 wall=$4 array=$5
    shift 5
    local args=(--parsable --job-name="$name" --cpus-per-task=1 --mem="$memory" --time="$wall" --kill-on-invalid-dep=yes
                --chdir="$PWD" --exclude=gpu04,gpu06,gpu20,gpu37 --output="$OUT/logs/%x-%A_%a.out" --error="$OUT/logs/%x-%A_%a.err")
    [[ -z "$PARTITION" ]] || args+=(--partition="$PARTITION")
    [[ -z "$dep" ]] || args+=(--dependency="$dep")
    [[ -z "$array" ]] || args+=(--array="$array")
    if [[ "$MODE" == --submit ]]; then
        REPLY=$(sbatch "${args[@]}" "$PWD/exp8s3_job.sh" "$@")
        REPLY=${REPLY%%;*}
        printf '%s\t%s\n' "$name" "$REPLY" >> "$OUT/logs/jobs.tsv"
    else
        printf 'sbatch'; printf ' %q' "${args[@]}" "$PWD/exp8s3_job.sh" "$@"; printf '\n'
        REPLY="JOB_${name//-/_}"
    fi
}
all_jobs=()
for ds in ${DATASETS:-second sysu}; do
    dependency=''
    [[ -z "$RULE_JOB_ID" ]] || dependency="afterok:$RULE_JOB_ID"
    if [[ -n "${RESUME_UNITS:-}" ]]; then  # 续跑：init 已完成，只补未完成的 units 分片（例如 RESUME_UNITS=2-15）
        submit "b43-units-$ds" "$dependency" 4G 02:00:00 "$RESUME_UNITS%$CONCURRENT" units "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    else
    submit "b43-init-$ds" "$dependency" 2G 00:15:00 '' init "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    init=$REPLY; all_jobs+=("$REPLY")
    submit "b43-units-$ds" "afterok:$init" 4G 02:00:00 "0-$((SHARDS-1))%$CONCURRENT" units "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    fi
    units=$REPLY; all_jobs+=("$REPLY")
    trains=()
    for i in "${!methods[@]}"; do
        dep="afterok:$units"
        if (( i >= TRAIN_CONCURRENT )); then dep+=":${trains[i-TRAIN_CONCURRENT]}"; fi
        submit "b43-train-$ds-${methods[i]}" "$dep" 8G 08:00:00 '' train "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS" "${methods[i]}" "${extra[@]}"
        trains+=("$REPLY"); all_jobs+=("$REPLY")
    done
    train_dep="afterok:$(IFS=:; echo "${trains[*]}")"
    submit "b43-rank-$ds" "$train_dep" 6G 12:00:00 "0-16%2" rank "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS" all "${extra[@]}"
    ranks=$REPLY; all_jobs+=("$REPLY")
    submit "b43-curves-$ds" "$train_dep" 8G 03:00:00 "0-$((SHARDS-1))%$CONCURRENT" curves "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    curves=$REPLY; all_jobs+=("$REPLY")
    submit "b43-merge-$ds" "afterok:$curves" 12G 02:00:00 '' merge "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    merged=$REPLY; all_jobs+=("$REPLY")
    submit "b43-points-$ds" "afterok:$merged" 8G 03:00:00 "0-$((SHARDS-1))%$CONCURRENT" points "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    points=$REPLY; all_jobs+=("$REPLY")
    submit "b43-report-$ds" "afterok:$points:$ranks" 8G 01:00:00 '' report "$ds" "$OUT" "$LIMIT" "$RULE" "$SHARDS"
    all_jobs+=("$REPLY")
done
# afterany covers success/failure; invalid afterok dependents are cancelled by Slurm.
submit b43-notify "afterany:$(IFS=:; echo "${all_jobs[*]}")" 256M 00:05:00 '' notify all "$OUT" "$LIMIT" "$RULE" "$SHARDS"
