"""6f feature adapters and score I/O; exp5-env, CPU or single GPU caller.

Estimate: metadata/context 1–3 GB; image shards add 2–8 GB. PCA covariance is
257 by 257 at the existing dim=256. Runtime depends on caller (see train/score).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.stats import rankdata
from torch import nn

from exp6b_common import SOURCES, Features, save_npz, verifier_split
from exp6f_context import COLUMNS, CONFIG, GROUPS

META_NAMES = ["prob_mean", "prob_max", "prob_min", "prob_p90", "log_area",
              *[f"source_{s}" for s in SOURCES]]


def load_scores(directory, pool, expected_run=None):
    """Accept full Pool indices; second_cv may have NaN outside validation."""
    directory = Path(directory)
    path = directory / f"scores_{pool.dataset}.npy"
    indexed = directory / f"scores_{pool.dataset}.npz" if path.exists() else directory / pool.dataset / "scores.npz"
    with np.load(indexed, allow_pickle=False) as z:
        idx = pool.feature_indices(z["image"], z["piece_id"])
        if len(idx) != len(pool) or str(z["dataset"]) != pool.dataset:
            raise ValueError(f"{indexed}: incomplete pool or wrong dataset")
        if expected_run is not None and str(z["run_id"]) != expected_run:
            raise ValueError(f"{indexed}: different run")
        values = np.load(path, allow_pickle=False) if path.exists() else z["score"]
        if path.exists() and not np.array_equal(idx, np.arange(len(pool))):
            raise ValueError("NPY score sidecar must have exact Pool row order")
        if (values.shape != (len(pool),) or np.isinf(values).any()
                or np.any((values < 0) | (values > 1))):
            raise ValueError(f"{indexed}: invalid probabilities")
        scores = np.empty(len(pool), np.float32)
        scores[idx] = values
        selected = (pool.image_mask(verifier_split()[1]) if pool.dataset == "second_cv"
                    else np.ones(len(pool), bool))
        if not np.isfinite(scores[selected]).all():
            raise ValueError(f"{indexed}: incomplete evaluation probabilities")
    return scores


def save_scores(directory, pool, scores, run_id):
    directory = Path(directory)
    path = directory / f"scores_{pool.dataset}.npy"
    if path.exists():
        raise FileExistsError(path)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.npy")
    np.save(temporary, np.asarray(scores, np.float32))
    temporary.replace(path)
    save_npz(path.with_suffix(".npz"), image=np.asarray(pool.images)[pool.image_id],
             piece_id=pool.piece_id, dataset=pool.dataset, run_id=run_id)


def ranked_meta(pool):
    result = pool.meta.copy()
    for sl in pool.slices.values():
        recall = np.flatnonzero(pool.source[sl] != SOURCES.index("pred")) + sl.start
        for col in range(4):
            values = pool.meta[recall, col]
            if len(values) <= 1:
                result[recall, col] = 0.5
            else:
                result[recall, col] = (rankdata(values, method="average") - 1) / (len(values) - 1)
        # Pred is not in the recall reference population. Map it to the same empirical
        # scale by midrank insertion; clip outside the observed recall range.
        pred = np.flatnonzero(pool.source[sl] == SOURCES.index("pred")) + sl.start
        for col in range(4):
            reference = np.sort(pool.meta[recall, col])
            if len(reference) <= 1:
                result[pred, col] = 0.5
            else:
                v = pool.meta[pred, col]
                left = np.searchsorted(reference, v, side="left")
                right = np.searchsorted(reference, v, side="right")
                result[pred, col] = np.clip((left + right - 1) / (2 * (len(reference) - 1)), 0, 1)
    return result


def load_context(pool, directory):
    base = Path(directory) / pool.dataset
    paths = sorted(base.glob("shard_*.npz"), key=lambda p: int(p.stem[6:]))
    if not paths:
        raise FileNotFoundError(base)
    with np.load(paths[0], allow_pickle=False) as z:
        shards = int(z["shards"])
    if [p.name for p in paths] != [f"shard_{i}.npz" for i in range(shards)]:
        raise ValueError(f"{base}: incomplete shards")
    data = np.empty((len(pool), len(COLUMNS)), np.float32)
    seen = np.zeros(len(pool), bool)
    for shard, path in enumerate(paths):
        with np.load(path, allow_pickle=False) as z:
            if (json.loads(str(z["config"])) != CONFIG or int(z["shards"]) != shards
                    or int(z["shard"]) != shard or str(z["dataset"]) != pool.dataset):
                raise ValueError(f"{path}: context configuration mismatch")
            idx = pool.feature_indices(z["image"], z["piece_id"])
            if seen[idx].any() or z["features"].shape != (len(idx), len(COLUMNS)):
                raise ValueError(f"{path}: duplicate rows or bad shape")
            data[idx] = z["features"]
            seen[idx] = True
    if not seen.all() or not np.isfinite(data).all():
        raise ValueError("Incomplete or non-finite context")
    return data


def image_difference(x, dim):
    # Existing Features layout: f1, f2, abs(f1-f2), c1, c2, abs(c1-c2), cos, meta.
    return np.concatenate((x[:, 2 * dim:3 * dim], x[:, 6 * dim:6 * dim + 1]), axis=1)


def fit_pca(pool, backbone, selected, components, batch_size, path):
    base = Features(pool, backbone)
    dim = base.config["dim"]
    total = np.zeros(dim + 1, np.float64)
    cross = np.zeros((dim + 1, dim + 1), np.float64)
    count = 0
    for _, x in base.batches(selected, batch_size):
        values = image_difference(x, dim).astype(np.float64)
        total += values.sum(0)
        cross += values.T @ values
        count += len(values)
    if count <= components or components > dim + 1:
        raise ValueError("Insufficient training rows or feature dimensions for PCA")
    mean = total / count
    covariance = (cross - count * np.outer(mean, mean)) / (count - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1][:components]
    projection = eigenvectors[:, order]
    for j in range(components):
        if projection[np.abs(projection[:, j]).argmax(), j] < 0:
            projection[:, j] *= -1
    save_npz(path, mean=mean.astype(np.float32), projection=projection.astype(np.float32),
             explained_variance=eigenvalues[order], samples=count,
             feature_config=json.dumps(base.config, sort_keys=True))


class VariantFeatures:
    def __init__(self, pool, config, directory, training=False):
        self.pool, self.options = pool, config
        self.mode = config["mode"]
        self.base = Features(pool, config["backbone"])
        self.meta = ranked_meta(pool) if config["rank"] or self.mode == "a1" else pool.meta
        self.offset = None
        self.groups = {}
        if self.mode == "context":
            context = load_context(pool, config["context_dir"])
            self.table = np.concatenate((self.meta[:, :5], self.meta[:, 11:12], context), axis=1)
            self.names = META_NAMES[:5] + ["split"] + COLUMNS
            self.groups = {"probability": list(range(4)), "geometry": [4, 5],
                           **{k: [i + 6 for i in v] for k, v in GROUPS.items()}}
        elif self.mode == "residual":
            base_dir = Path(config["baseline_dir"])
            report = json.loads((base_dir / "val_metrics.json").read_text())
            if report["run_id"] != config["baseline_run_id"]:
                raise ValueError("Residual baseline run changed")
            scores = load_scores(base_dir, pool, report["run_id"])
            if training:
                manifest = json.loads((Path(config["oof_dir"]) / "crossfit.json").read_text())
                train_images, val_images = verifier_split()
                if (manifest["baseline_run_id"] != report["run_id"]
                        or manifest["train_images"] != train_images or manifest["val_images"] != val_images):
                    raise ValueError("Cross-fitting does not match baseline or verifier split")
                with np.load(Path(config["oof_dir"]) / "oof_second_cv.npz", allow_pickle=False) as z:
                    idx = pool.feature_indices(z["image"], z["piece_id"])
                    train = np.flatnonzero(pool.image_mask(train_images))
                    if not np.array_equal(np.sort(idx), train) or not np.isfinite(z["score"]).all():
                        raise ValueError("Incomplete out-of-fold training predictions")
                    scores[idx] = z["score"]
            scores = np.clip(scores, 1e-7, 1 - 1e-7)
            self.offset = np.log(scores / (1 - scores)).astype(np.float32)
            self.names = [f"image_{i}" for i in range(self.base.input_dim - 12)] + ["baseline_logit"]
        elif self.mode == "pca":
            with np.load(Path(directory) / "pca.npz", allow_pickle=False) as z:
                if json.loads(str(z["feature_config"])) != self.base.config:
                    raise ValueError("PCA backbone configuration changed")
                self.pca_mean, self.projection = z["mean"], z["projection"]
            self.names = [f"pc_{i + 1}" for i in range(self.projection.shape[1])] + META_NAMES + ["split"]
        else:
            self.names = (META_NAMES if config["backbone"] == "none" else
                          [f"image_{i}" for i in range(self.base.input_dim - 12)] + META_NAMES + ["split"])
        drop = config.get("drop_group")
        if drop and drop not in self.groups:
            raise ValueError(f"Unknown context feature group: {drop}")
        self.columns = [i for i in range(len(self.names)) if i not in self.groups.get(drop, [])]
        self.names = [self.names[i] for i in self.columns]
        self.input_dim = len(self.names)
        self.config = dict(mode=self.mode, backbone=self.base.config,
                           rank=config["rank"] or self.mode == "a1", columns=self.names,
                           context=CONFIG if self.mode == "context" else None)

    def batches(self, selected, batch_size, rng=None):
        for idx, x in self.base.batches(selected, batch_size, rng):
            if self.mode == "context":
                values = self.table[idx]
            elif self.mode == "residual":
                values = np.concatenate((x[:, :-12], self.offset[idx, None]), axis=1)
            elif self.mode == "pca":
                reduced = (image_difference(x, self.base.config["dim"]) - self.pca_mean) @ self.projection
                values = np.concatenate((reduced, self.meta[idx]), axis=1)
            elif self.options["backbone"] == "none":
                values = self.meta[idx, :11]
            else:
                values = np.concatenate((x[:, :-12], self.meta[idx]), axis=1)
            yield idx, np.ascontiguousarray(values[:, self.columns], dtype=np.float32)


class ResidualMLP(nn.Module):
    def __init__(self, input_dim, width, dropout):
        super().__init__()
        self.correction = nn.Sequential(nn.Linear(input_dim - 1, width), nn.ReLU(),
                                        nn.Dropout(dropout), nn.Linear(width, 1))
        nn.init.zeros_(self.correction[-1].weight)
        nn.init.zeros_(self.correction[-1].bias)

    def forward(self, x):
        return x[:, -1:] + self.correction(x[:, :-1])
