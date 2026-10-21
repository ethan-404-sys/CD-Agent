"""6h-1 all-source adapter for installed 6f evaluation; exp5-env, CPU.

Request 4 CPUs / 24 GiB RAM; ~0.5–4 h per run/pool, unmeasured.
Uses the existing 6b/6f threshold, pixel-union and target-recall definitions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from exp6b_common import Pool, verifier_split, write_json
from exp6b_eval import COMBINATIONS, evaluate_scores
from exp6f_common import load_scores
from exp6f_recall import plot_reports, recall_curves


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+")
    parser.add_argument("--selection", type=Path, help="Frozen validation-only selection from exp6h_report")
    parser.add_argument("--dataset", choices=("second_cv", "second"), required=True)
    parser.add_argument("--kind", choices=("pixels", "recall"), default="pixels")
    args = parser.parse_args()
    if args.dataset == "second" and args.selection is None:
        parser.error("Test evaluation requires --selection")
    selected = json.loads(args.selection.read_text()) if args.selection else None
    runs = args.runs or ([Path(r["directory"]) for r in selected["selected_runs"]] if selected else [])
    if not runs:
        parser.error("Supply --runs or --selection")
    if selected:
        allowed = {str(Path(r["directory"]).resolve()): r["run_id"] for r in selected["selected_runs"]}
        for run in runs:
            report = json.loads((run / "val_metrics.json").read_text())
            if allowed.get(str(run.resolve())) != report["run_id"]:
                raise ValueError(f"{run}: not a frozen selected run")
    # Both imported evaluation modules reference this same dictionary.
    # The process-local restriction avoids changing any installed source files.
    all_sources = COMBINATIONS["all"]
    COMBINATIONS.clear()
    COMBINATIONS["all"] = all_sources
    pool = Pool(args.dataset)
    images = verifier_split()[1] if args.dataset == "second_cv" else pool.images
    for run in runs:
        training = json.loads((run / "val_metrics.json").read_text())
        run_id = training["run_id"]
        out = run / ("evaluation" if args.kind == "pixels" else "recall")
        path = (out / f"eval_{args.dataset}.json" if args.kind == "pixels"
                else out / f"{run.name}_{args.dataset}.json")
        if path.exists():
            completed = json.loads(path.read_text())
            if completed["run_id"] != run_id:
                raise ValueError(f"{path}: another run")
            print(f"reuse completed evaluation: {path}", flush=True)
            continue
        if args.dataset == "second" and args.kind == "recall":
            calibration = json.loads((run / "evaluation/thresholds_second_cv.json").read_text())
            if calibration["run_id"] != run_id:
                raise ValueError("Freeze the current run's validation threshold first")
        scores = load_scores(run, pool, run_id)
        out.mkdir(parents=True, exist_ok=True)
        if args.kind == "pixels":
            evaluate_scores(pool, scores, out, run_id, run.name,
                            feature_config=dict(scores_dir=str(run.resolve())), rule="threshold")
        else:
            # Same complete diagnostic curve as exp6f_recall, including area buckets.
            report = recall_curves(pool, scores, images, out, run.name)
            report["run_id"] = run_id
            write_json(path, report)
            plot_reports([report], out)
        print(f"saved {path}", flush=True)


if __name__ == "__main__":
    main()
