"""6h-2 sequential GPU launcher; exp5-env, 1 GPU / 4 CPUs / 12 GiB host.

Train estimate 15–45 min/model, validation scoring 1–5 min/model, selected
SECOND scoring 5–20 min/model. Shared 12 GPU-hour ledger includes failures.
Every model has its own seed-dependent initialization, shuffle and augmentation.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from exp6b_common import ROOT, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("smoke", "train", "validation", "test"), required=True)
    parser.add_argument("--variants", type=Path, default=ROOT / "variants")
    parser.add_argument("--cache", type=Path, default=ROOT / "variants/c/cache_fit_raw")
    parser.add_argument("--gt_cache", type=Path, default=ROOT / "variants/h2_gtcache")
    parser.add_argument("--test_cache", type=Path, default=ROOT / "variants/c/cache_all_raw/second")
    parser.add_argument("--baseline", type=Path, default=ROOT / "variants/c/siamese_raw")
    parser.add_argument("--selection", type=Path, default=ROOT / "variants/h2_selection.json")
    parser.add_argument("--budget_file", type=Path, default=ROOT / "variants/h2_gpu_budget.json")
    parser.add_argument("--lambdas", type=float, nargs="+", choices=(0, 0.1, 0.3, 1), default=[0, 0.1, 0.3, 1])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1020, 1021, 1022])
    parser.add_argument("--smoke_out", type=Path)
    parser.add_argument("--max_gpu_hours", type=float, default=12)
    args = parser.parse_args()
    if not 0 < args.max_gpu_hours <= 12:
        parser.error("Cumulative GPU budget must be in (0, 12]")
    if args.phase == "smoke" and args.smoke_out is None:
        parser.error("Smoke requires an empty --smoke_out diagnostics directory")
    commands = []
    if args.phase == "test":
        frozen = json.loads(args.selection.read_text())
        for item in frozen["selected_runs"]:
            run = Path(item["directory"])
            report = json.loads((run / "val_metrics.json").read_text())
            if report["run_id"] != item["run_id"]:
                raise ValueError(f"{run}: frozen run changed")
            if report["seed"] not in args.seeds or report["temporal_lambda"] not in args.lambdas:
                continue
            commands.append(["exp6f_c_score.py", "--run", str(run), "--dataset", "second",
                             "--cache", str(args.test_cache), "--calibration_dir", str(run / "evaluation")])
    else:
        settings = [(0.3, 1020)] if args.phase == "smoke" else [(v, s) for v in args.lambdas for s in args.seeds]
        for weight, seed in settings:
            run = args.smoke_out if args.phase == "smoke" else args.variants / f"h2_l{weight:g}_s{seed}"
            if args.phase in ("train", "smoke"):
                command = ["exp6h2_train.py", "--cache", str(args.cache), "--gt_cache", str(args.gt_cache),
                           "--baseline", str(args.baseline), "--out", str(run),
                           "--temporal_lambda", str(weight), "--seed", str(seed)]
                if args.phase == "smoke":
                    command += ["--max_steps", "2", "--limit_images", "8"]
            else:
                command = ["exp6f_c_score.py", "--run", str(run), "--dataset", "second_cv", "--cache", str(args.cache)]
            commands.append(command)
    if not commands:
        parser.error("No runs match the requested phase/lambdas/seeds")
    args.budget_file.parent.mkdir(parents=True, exist_ok=True)
    with args.budget_file.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(args.budget_file.read_text()) if args.budget_file.exists() else {"jobs": []}
        if any(j["status"] == "running" for j in state["jobs"]):
            raise RuntimeError("Reconcile interrupted job seconds with sacct before continuing")
        prior = sum(j["seconds"] for j in state["jobs"])
        remaining = args.max_gpu_hours * 3600 - prior
        if remaining <= 0:
            raise RuntimeError("6h-2 cumulative GPU budget exhausted; stop and report")
        started = time.monotonic()
        job = dict(slurm_job_id=os.environ.get("SLURM_JOB_ID"), phase=args.phase,
                   seconds=0.0, status="running", commands=commands, completed=0)
        state["jobs"].append(job)
        write_json(args.budget_file, state)
        try:
            for command in commands:
                remaining = args.max_gpu_hours * 3600 - prior - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("6h-2 cumulative GPU budget exhausted; stop and report")
                subprocess.run([sys.executable, str(Path(__file__).parent / command[0]), *command[1:]],
                               check=True, timeout=remaining)
                job["completed"] += 1
                job["seconds"] = time.monotonic() - started
                write_json(args.budget_file, state)
            job["status"] = "complete"
        finally:
            if job["status"] == "running":
                job["status"] = "failed"
            job["seconds"] = time.monotonic() - started
            write_json(args.budget_file, state)


if __name__ == "__main__":
    main()
