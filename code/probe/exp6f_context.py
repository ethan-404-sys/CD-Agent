"""Cache inference-only context in exp5-env (CPU, no GPU).

Estimate: 4–12 GB per process, 1–4 h per SECOND shard at eight shards; unmeasured.
Recreates 6a SLIC from RGB with identical geometry; never reads GT labels/classes.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage.segmentation import slic

from common import load_pred, load_prob, load_rgb
from exp6a_pieces import geometry
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import ROOT, SOURCES, rows_by_image, save_npz, source_of

COLUMNS = ["source_count", *[f"covers_{s}" for s in SOURCES[:-1]],
           "prediction_distance", "prediction_adjacent", "candidate_log_area",
           "candidate_piece_count", "other_piece_probability", "neighbor_candidate_fraction",
           "neighbor_probability", "image_prediction_fraction", "image_recall_piece_count"]
GROUPS = {"sources": list(range(6)), "distance": [6, 7], "candidate": [8, 9, 10],
          "adjacency": [11, 12], "image": [13, 14]}
CONFIG = dict(version=1, columns=COLUMNS, groups=GROUPS,
              coverage="any_overlap_with_retained_recall_masks_per_source",
              candidate="retained_piece_count; unweighted_other_piece_mean; singleton_uses_own_mean",
              adjacency="4_connected_SLIC_neighbors_excluding_own_segments; any_recall_overlap",
              distance="minimum_euclidean_distance_over_piece_divided_by_image_diagonal; empty_base=1",
              empty_neighbors="candidate_fraction=0; probability=own_piece_mean")


def context_for_image(dataset, name, rows):
    base = load_pred(dataset, DETECTOR[dataset], name)
    prob = load_prob(dataset, DETECTOR[dataset], name)
    masks = load_piece_masks(dataset, name)
    coverage = np.zeros((5, *base.shape), bool)
    candidates = defaultdict(list)
    for i, row in enumerate(rows):
        candidates[row["cand_id"]].append(i)
        if row["kind"] == "recall":
            (x0, y0, x1, y1), local = masks[row["piece_id"]]
            coverage[SOURCES.index(source_of(row)), y0:y1, x0:x1] |= local
    candidate_pixels = coverage.any(0)
    median, _ = geometry(dataset)
    stack = np.concatenate([load_rgb(dataset, name, v) for v in ("t1", "t2")], axis=2)
    segments = slic(stack, n_segments=max(4, int(prob.size / median)),
                    compactness=10, channel_axis=-1, start_label=1)
    size = int(segments.max()) + 1
    neighbors = [set() for _ in range(size)]
    for a, b in ((segments[:-1], segments[1:]), (segments[:, :-1], segments[:, 1:])):
        edge = a != b
        pairs = np.unique(np.stack((a[edge], b[edge]), axis=1), axis=0)
        for left, right in pairs:
            neighbors[left].add(int(right))
            neighbors[right].add(int(left))
    area = np.bincount(segments.ravel(), minlength=size)
    segment_prob = np.bincount(segments.ravel(), weights=prob.ravel(), minlength=size)
    segment_prob /= np.maximum(area, 1)
    segment_candidate = np.bincount(segments[candidate_pixels], minlength=size) > 0
    diagonal = float(np.hypot(*base.shape))
    distance = ndimage.distance_transform_edt(~base) / diagonal if base.any() else np.ones(base.shape)
    adjacent = ndimage.binary_dilation(base, structure=np.ones((3, 3), bool))
    means = np.asarray([r["det_prob"]["mean"] for r in rows])
    image_features = [float(base.mean()), sum(r["kind"] == "recall" for r in rows)]
    values = np.empty((len(rows), len(COLUMNS)), np.float32)
    for i, row in enumerate(rows):
        (x0, y0, x1, y1), local = masks[row["piece_id"]]
        region = np.s_[y0:y1, x0:x1]
        covered = coverage[:, y0:y1, x0:x1][:, local].any(1)
        siblings = candidates[row["cand_id"]]
        other = ((means[siblings].sum() - means[i]) / (len(siblings) - 1)
                 if len(siblings) > 1 else means[i])
        own = set(map(int, np.unique(segments[region][local])))
        nearby = sorted(set().union(*(neighbors[s] for s in own)) - own)
        fraction = float(segment_candidate[nearby].mean()) if nearby else 0.0
        neighbor_prob = float(segment_prob[nearby].mean()) if nearby else means[i]
        values[i] = [int(covered.sum()), *covered, float(distance[region][local].min()),
                     bool(adjacent[region][local].any()), np.log(row["cand_area"]), len(siblings),
                     other, fraction, neighbor_prob, *image_features]
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(DETECTOR), required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "variants/context")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=8)
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error("Require 0 <= shard < shards")
    path = args.out / args.dataset / f"shard_{args.shard}.npz"
    if path.exists():
        raise FileExistsError(path)
    start = time.monotonic()
    images, ids, values = [], [], []
    # Same sorted image numbering as Pool, including images without pieces.
    from common import names
    assigned = set(names(args.dataset)[args.shard::args.shards])
    for name, rows in rows_by_image(args.dataset):
        if name not in assigned:
            continue
        values.append(context_for_image(args.dataset, name, rows))
        images.extend([name] * len(rows))
        ids.extend(r["piece_id"] for r in rows)
        if len(values) % 25 == 0:
            print(args.dataset, args.shard, len(values), "images", flush=True)
    save_npz(path, image=np.asarray(images, dtype=str), piece_id=np.asarray(ids, np.int32),
             features=np.concatenate(values) if values else np.empty((0, len(COLUMNS)), np.float32),
             config=json.dumps(CONFIG, sort_keys=True), dataset=args.dataset,
             shard=args.shard, shards=args.shards, seconds=time.monotonic() - start)
    print(f"saved {path}", flush=True)


if __name__ == "__main__":
    main()
