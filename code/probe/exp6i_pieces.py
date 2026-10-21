"""6i direct and grid offsets for ALL Pool rows, including pred and unlabeled pieces.

CPU; direct per-piece registration runtime and memory are unmeasured.
Run the two datasets in separate jobs. --merge-audit is a small CPU follow-up.
Grid-window intersections count overlapping support once per window, not once per
pixel; magnitude is the norm of the weighted mean vector, not mean vector length.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from exp6_pool_io import load_piece_masks
from exp6b_common import Pool, save_npz, write_json
from exp6i_common import (AUDIT, OUT, area_bucket, bucket, combined_record, direct_piece,
                         iter_grids, load_pair, piece_offset, pixel_fields, read_config,
                         reference_result)


def merge_audit(args, config):
    manifest = json.loads(args.samples.read_text())
    found = {}
    for dataset in ("second", "second_cv"):
        fragment = json.loads((args.out / dataset / "audit_samples_offset.json").read_text())
        if fragment["config"] != config:
            raise ValueError("Audit fragment configuration mismatch")
        for row in fragment["samples"]:
            if row["id"] in found:
                raise ValueError("Duplicate audit ID across fragments")
            found[row["id"]] = row
    if set(found) != {r["id"] for r in manifest["samples"]}:
        raise ValueError("Audit fragments do not cover the current sample IDs")
    rows = []
    for row in manifest["samples"]:
        merged = found[row["id"]]
        if {key: value for key, value in merged.items() if key != "offset"} != row:
            raise ValueError("Audit samples changed after piece association")
        rows.append(merged)
    write_json(args.out / "audit_samples_offset.json", {**manifest, "offset_config": config, "samples": rows})


def associate(args, config):
    started = time.monotonic()
    pool = Pool(args.dataset)
    n = len(pool)
    offsets = np.full((n, 6), np.nan, np.float32)
    grid_offsets = np.full((n, 6), np.nan, np.float32)
    quality_fields = ("raw_dx", "raw_dy", "error", "peak_ratio", "texture", "change_fraction")
    direct_quality = {key: np.full(n, np.nan, np.float32) for key in quality_fields}
    direct_bbox = np.zeros((n, 4), np.int32)
    area = np.zeros(n, np.int32)
    long_side = np.zeros(n, np.int32)
    manifest = json.loads(args.samples.read_text())
    samples = [r for r in manifest["samples"] if r["dataset"] == args.dataset]
    by_image = {}
    for row in samples:
        by_image.setdefault(row["image"], []).append(row)
    attached = []
    image_seen = np.zeros(len(pool.images), bool)
    for number, (name, grid) in enumerate(iter_grids(args.out, args.dataset, config), 1):
        sl = pool.slices[name]
        fields = pixel_fields(grid)
        pair = load_pair(args.dataset, name)
        masks = load_piece_masks(args.dataset, name)
        ids = pool.piece_id[sl]
        if set(masks) != set(map(int, ids)):
            raise ValueError(f"{args.dataset}/{name}: masks do not cover current Pool rows")
        needed = {row["piece_id"] for row in by_image.get(name, [])}
        if args.dataset == "second" and name == "test_857.png":
            needed.add(18)
        details_by_id = {}
        for i, piece_id in enumerate(ids, sl.start):
            bbox, mask = masks[int(piece_id)]
            x0, y0, x1, y1 = bbox
            area[i] = mask.sum()
            long_side[i] = max(x1 - x0, y1 - y0)
            grid_offsets[i] = piece_offset(fields, bbox, mask)
            offsets[i], details = direct_piece(pair, bbox, mask, config)
            direct_bbox[i] = details["window_bbox"]
            for key in quality_fields:
                direct_quality[key][i] = np.nan if details[key] is None else details[key]
            if int(piece_id) in needed:
                details_by_id[int(piece_id)] = details
        if args.dataset == "second" and name == "test_857.png":
            reference_details = details_by_id[18]
        image_seen[pool.image_index[name]] = True
        for row in by_image.get(name, []):
            local = int(np.searchsorted(ids, row["piece_id"]))
            if local >= len(ids) or int(ids[local]) != row["piece_id"]:
                raise ValueError(f"Audit piece missing: {args.dataset}/{name}/{row['piece_id']}")
            index = sl.start + local
            attached.append({**row, "offset": combined_record(
                offsets[index], grid_offsets[index], details_by_id[row["piece_id"]])})
        if number % 100 == 0:
            print(args.dataset, number, len(pool.images), f"elapsed={time.monotonic() - started:.1f}s", flush=True)
    if not image_seen.all() or np.any(area <= 0):
        raise ValueError("Incomplete image coverage or empty masks")
    if len(attached) != len(samples):
        raise ValueError("Not all audit samples were associated")
    grid_columns = ("dx", "dy", "magnitude", "interpolation_fraction", "relative", "interpolation_distance")
    save_npz(args.out / args.dataset / "pieces_offset.npz",
             dataset=args.dataset, config=json.dumps(config), image_names=np.asarray(pool.images),
             image_id=pool.image_id, piece_id=pool.piece_id, label=pool.label, source=pool.source,
             split=pool.meta[:, 11].astype(bool), area=area, long_side=long_side,
             dx=offsets[:, 0], dy=offsets[:, 1], magnitude=offsets[:, 2],
             interpolation_fraction=offsets[:, 3], relative=offsets[:, 4],
             primary_method="direct", direct_valid=np.isfinite(offsets[:, 2]),
             direct_window_bbox=direct_bbox,
             **{f"direct_{key}": values for key, values in direct_quality.items()},
             **{f"direct_{key}": offsets[:, i] for i, key in enumerate(grid_columns)},
             **{f"grid_{key}": grid_offsets[:, i] for i, key in enumerate(grid_columns)},
             grid_unestimable=~np.isfinite(grid_offsets[:, 2]),
             grid_offset_bucket=bucket(grid_offsets[:, 2]),
             grid_relative_bucket=bucket(grid_offsets[:, 4], True),
             uses_interpolation=offsets[:, 3] > 1e-7, unestimable=~np.isfinite(offsets[:, 2]),
             offset_bucket=bucket(offsets[:, 2]), relative_bucket=bucket(offsets[:, 4], True),
             area_bucket=area_bucket(area), elapsed_seconds=time.monotonic() - started)
    write_json(args.out / args.dataset / "audit_samples_offset.json",
               dict(config=config, samples=attached))
    if args.dataset == "second":
        sl = pool.slices["test_857.png"]
        ids = np.flatnonzero(pool.piece_id[sl] == 18) + sl.start
        if len(ids) != 1:
            raise ValueError("Reference piece missing from Pool")
        record = reference_result(offsets[ids[0]], grid_offsets[ids[0]], reference_details, config)
        previous = json.loads((args.out / "real_check.json").read_text())
        if any(record[key] != previous[key] for key in ("config", "direct", "grid", "direct_measurement", "proceed")):
            raise ValueError("Full piece reference differs from the preflight reference")
        write_json(args.out / "test_857_piece18_offset.json", record)
        if not record["proceed"]:
            raise ValueError("Full piece reference failed the documented reference check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dataset", choices=("second", "second_cv"))
    mode.add_argument("--merge-audit", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--samples", type=Path, default=AUDIT / "samples.json")
    args = parser.parse_args()
    config = read_config(args.config or args.out / "config.json")
    if args.merge_audit:
        merge_audit(args, config)
    else:
        associate(args, config)


if __name__ == "__main__":
    main()
