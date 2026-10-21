"""6i real-image offsets, CPU only; real-pair config and reference gate first.

Estimated <1 GB RAM, 5–25 min per 150–300 image shard on one CPU (unmeasured).
Use 8 test and 12 second_cv shards, one CPU each, for a target <1 h wall time.
At window=32, prefer 16/24 shards. No image pairs are retained between iterations.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from common import names
from exp6_pool_io import load_piece_masks
from exp6b_common import rows_by_image, save_npz, write_json
from exp6i_common import (GRID_FIELDS, OUT, direct_piece, estimate_image, load_pair,
                         piece_offset, pixel_fields, read_config, reference_result)
from exp6v2_data import Renderer, SIDE


def reference_check(args, config):
    name, piece_id = "test_857.png", 18
    grid = estimate_image("second", name, config)
    bbox, mask = load_piece_masks("second", name)[piece_id]
    values = piece_offset(pixel_fields(grid), bbox, mask)
    direct, details = direct_piece(load_pair("second", name), bbox, mask, config)
    result = reference_result(direct, values, details, config)
    result["valid_grids"] = int(grid["valid"].sum())
    out = args.out / "reference"
    save_npz(out / "grid.npz", **grid, image=name, config=json.dumps(config))
    row = next(row for image, rows in rows_by_image("second") if image == name
               for row in rows if row["piece_id"] == piece_id)
    pair = Image.new("RGB", (2 * SIDE, SIDE))
    for i, image in enumerate(Renderer().render("second", row)):
        pair.paste(image, (i * SIDE, 0))
    pair.save(out / "test_857_piece18.png")
    write_json(args.out / "real_check.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result["proceed"]:
        raise ValueError("Reference direct estimate failed 8–12 and the documented grid-disagreement case; review reference/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("second", "second_cv"), default="second")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", "0")))
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--check-reference", action="store_true")
    args = parser.parse_args()
    config = read_config(args.config or args.out / "config.json")
    if args.check_reference:
        reference_check(args, config)
        return
    images = names(args.dataset)
    if not 1 <= args.shards <= len(images) or not 0 <= args.shard < args.shards:
        parser.error("Require 1 <= shards <= image count and 0 <= shard < shards")
    check = json.loads((args.out / "real_check.json").read_text())
    if not check["proceed"] or check["config"] != config:
        raise ValueError("Run --check-reference successfully with this config before the full dataset")
    started = time.monotonic()
    selected = images[args.shard::args.shards]
    grids = []
    for i, name in enumerate(selected, 1):
        grids.append(estimate_image(args.dataset, name, config))
        if i % 20 == 0 or i == len(selected):
            print(args.dataset, args.shard, i, len(selected),
                  f"elapsed={time.monotonic() - started:.1f}s", flush=True)
    save_npz(args.out / args.dataset / f"shard_{args.shard}.npz",
             **{key: np.stack([grid[key] for grid in grids]) for key in GRID_FIELDS},
             image=np.asarray(selected), dataset=args.dataset, shard=args.shard, shards=args.shards,
             config=json.dumps(config), elapsed_seconds=time.monotonic() - started)


if __name__ == "__main__":
    main()
