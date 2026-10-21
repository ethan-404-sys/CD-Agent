"""Select demonstration images from completed batch14 row 5 (exp5, CPU)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import OUT, names
from exp6b_common import write_json

COMBO = "B+G0.05+H1"
V1 = OUT / "exp6/verifier/none"
DEFAULT_OUT = OUT / "pipeline2"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def completed_metrics(directory):
    directory = Path(directory)
    summary = read_json(directory / "summary.json")
    config = summary["config"]
    if config["dataset"] != "second" or config["images"] != 1694:
        raise ValueError("Requires complete SECOND test eval_metrics")
    calibration = read_json(V1 / "thresholds_second_cv.json")
    threshold = calibration["recall"][COMBO]["threshold"]
    if config["v1_threshold"] != threshold or threshold != 0.819681704044342:
        raise ValueError("Row 5 must use the exact frozen V1 none threshold")
    rows = {}
    with (directory / "per_image.jsonl").open() as handle:
        for line in handle:
            record = json.loads(line)
            if record["row"] == "5":
                if record["image"] in rows:
                    raise ValueError("Duplicate row 5 image")
                rows[record["image"]] = record
    if sorted(rows) != names("second"):
        raise ValueError("Row 5 is incomplete")
    return summary, rows


def selected_rows(selection):
    """Read the saved pool once; preserve IDs, crop geometry and unlabeled pieces."""
    from exp6b_common import rows_by_image
    wanted = {r["image"] for r in selection["examples"]}
    result = {}
    for name, rows in rows_by_image("second"):
        if name in wanted:
            result[name] = rows
            if len(result) == len(wanted):
                break
    if set(result) != wanted:
        raise ValueError("Selected images missing from pool")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, default=OUT / "eval_metrics")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT / "selection.json")
    parser.add_argument("--min-gt-pixels", type=int, default=1000)
    args = parser.parse_args()
    if args.min_gt_pixels < 1:
        parser.error("--min-gt-pixels must be positive")
    summary, rows = completed_metrics(args.metrics)
    eligible = [r for r in rows.values() if r["image"] != "test_987.png"
                and r["counts"]["tp"] + r["counts"]["fn"] >= args.min_gt_pixels]
    positive = sorted((r for r in eligible if r["delta_f1"] > 0),
                      key=lambda r: (-r["delta_f1"], r["image"]))
    negative = sorted((r for r in eligible if r["delta_f1"] < 0),
                      key=lambda r: (r["delta_f1"], r["image"]))
    if not positive or not negative:
        raise ValueError("No positive/negative demonstration meeting the stated minimum")
    examples = []
    for role, r in (("固定示例", rows["test_987.png"]), ("E1", positive[0]), ("E2", negative[0])):
        examples.append(dict(role=role, image=r["image"], delta_f1=r["delta_f1"],
                             delta_f1_pp=100 * r["delta_f1"], before_f1=r["before_f1"],
                             after_f1=r["metrics"]["pixel_f1"],
                             gt_pixels=r["counts"]["tp"] + r["counts"]["fn"]))
    rule = (f"保留 test_987；其余图要求真值变化像素 ≥ {args.min_gt_pixels}。"
            "E1 取行 5 的正 ΔF1 最大图，E2 取负 ΔF1 最小图；同分按文件名升序。"
            "测试结果仅用于挑选展示图，不参与模型、来源组合或阈值选择。")
    write_json(args.out, dict(examples=examples, rule=rule, eligible=len(eligible),
                             metrics_dir=str(args.metrics), row="5", combo=COMBO,
                             threshold=summary["config"]["v1_threshold"],
                             min_gt_pixels=args.min_gt_pixels))
    print(json.dumps(examples, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
