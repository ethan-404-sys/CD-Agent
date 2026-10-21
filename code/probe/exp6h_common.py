"""6h-1 shared code; exp5-env (PyTorch 2.5.1), no backbone inference.

Measured cache files: second_cv 7,659,099,012 bytes, second 4,876,173,528.
Only f1/f2 are resident: 3.46/2.21 GiB fp16. Three training seeds: request
one 12+ GiB GPU and 16 GiB host RAM; peak estimates await Slurm measurement.
"""
from __future__ import annotations

import fcntl
import json
import time
import uuid
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from exp6b_common import Features, classifier, write_json


def setting_name(mode, weight):
    return "g1_0" if weight == 0 else f"g1_{mode}_l{weight:g}"


def feature_shards(pool, expected=None):
    base = Features(pool, "dinov3", expected)
    cfg = base.config
    if cfg["dim"] != 256 or cfg["layout"] != "f1,f2,c1,c2":
        raise ValueError("6h-1 requires the existing 256-dimensional f1,f2,c1,c2 cache")
    seen = np.zeros(len(pool), bool)
    for shard, path in enumerate(base.paths):
        with np.load(path, allow_pickle=False) as z:
            if (json.loads(str(z["config"])) != cfg or int(z["shard"]) != shard
                    or int(z["shards"]) != len(base.paths) or str(z["dataset"]) != pool.dataset):
                raise ValueError(f"{path}: feature metadata mismatch")
            idx = pool.feature_indices(z["image"], z["piece_id"])
            vectors = z["features"]
        if vectors.shape != (len(idx), 4, 256) or seen[idx].any():
            raise ValueError(f"{path}: unexpected shape or repeated pieces")
        # Do not retain a view onto the four-vector shard.
        pair = np.ascontiguousarray(vectors[:, :2], dtype=np.float16)
        del vectors
        if not np.isfinite(pair).all():
            raise ValueError(f"{path}: non-finite f1/f2")
        seen[idx] = True
        yield idx, pair
    if not seen.all():
        raise ValueError("Incomplete feature coverage")


def load_cache(pool, device, budget, expected=None):
    config = Features(pool, "dinov3", expected).config
    pair = torch.empty((len(pool), 2, 256), dtype=torch.float16, device=device)
    for idx, values in feature_shards(pool, expected):
        budget.check()
        pair[torch.as_tensor(idx, device=device)] = torch.from_numpy(values).to(device)
    # Exactly the existing none features: 4 probability statistics, log area, 6 sources.
    meta = torch.from_numpy(pool.meta[:, :11].copy()).to(device)
    return pair, meta, config


@torch.no_grad()
def fit_normalization(pair, meta, train, chunk=65536):
    """Fit on labeled training recall pieces; pool both dates for a shared g scale."""
    device = pair.device
    total = torch.zeros(256, dtype=torch.float64, device=device)
    square = torch.zeros_like(total)
    mt = torch.zeros(11, dtype=torch.float64, device=device)
    ms = torch.zeros_like(mt)
    for ids in train.split(chunk):
        x = pair[ids].double().reshape(-1, 256)
        m = meta[ids].double()
        total += x.sum(0)
        square += x.square().sum(0)
        mt += m.sum(0)
        ms += m.square().sum(0)
    def moments(s, ss, n):
        mean = s / n
        std = (ss / n - mean.square()).clamp_min(0).sqrt()
        std[std < 1e-6] = 1
        return mean.float(), std.float()
    f_mean, f_std = moments(total, square, 2 * len(train))
    m_mean, m_std = moments(mt, ms, len(train))
    return dict(f_mean=f_mean, f_std=f_std, m_mean=m_mean, m_std=m_std)


class SeedDropout(nn.Dropout):
    """A private generator keeps each seed independent of other seeds' early stops."""

    def __init__(self, p, generator):
        super().__init__(p)
        self.generator = generator

    def forward(self, x):
        if not self.training or self.p == 0:
            return x
        keep = torch.rand(x.shape, device=x.device, generator=self.generator) >= self.p
        return x * keep / (1 - self.p)


class TemporalMLP(nn.Module):
    def __init__(self, mode, dropout, norm, generator=None):
        super().__init__()
        self.mode = mode
        dim = 8 if mode == "sm" else 64
        self.g = nn.Sequential(nn.Linear(256, 256), nn.ReLU(),
                               nn.Linear(256, dim), nn.LayerNorm(dim))
        self.head = classifier(3 * dim + 1 + 11, dropout)
        if generator is not None:
            self.head[2] = SeedDropout(dropout, generator)
            self.head[5] = SeedDropout(dropout, generator)
        for name, value in norm.items():
            self.register_buffer(name, value.clone())

    def forward(self, pair, meta):
        x = (pair.float() - self.f_mean) / self.f_std
        z = self.g(x)
        z1, z2 = z[:, 0], z[:, 1]
        # Softmax belongs to the similarity branch; classifier also sees z1/z2.
        sim = (z.float() / 3).softmax(-1) if self.mode == "sm" else z.float()
        cosine = F.cosine_similarity(sim[:, 0], sim[:, 1], dim=-1).clamp(-1, 1)
        m = (meta.float() - self.m_mean) / self.m_std
        values = torch.cat((z1, z2, (z1 - z2).abs(), cosine[:, None], m), dim=1)
        return self.head(values).squeeze(-1).float(), cosine


@torch.inference_mode()
def predict(model, pair, meta, indices, batch_size, budget):
    model.eval()
    scores = np.empty(len(indices), np.float32)
    cosines = np.empty_like(scores)
    for start in range(0, len(indices), batch_size):
        budget.check()
        idx = indices[start:start + batch_size]
        with torch.autocast("cuda", dtype=torch.float16):
            logits, cosine = model(pair[idx], meta[idx])
        scores[start:start + len(idx)] = logits.sigmoid().cpu().numpy()
        cosines[start:start + len(idx)] = cosine.cpu().numpy()
    if not np.isfinite(scores).all() or not np.isfinite(cosines).all():
        raise ValueError("Non-finite predictions")
    return scores, cosines


def load_cos(directory, pool, run_id, selected):
    directory = Path(directory)
    with np.load(directory / f"scores_{pool.dataset}.npz", allow_pickle=False) as z:
        idx = pool.feature_indices(z["image"], z["piece_id"])
        if (str(z["run_id"]) != run_id or str(z["dataset"]) != pool.dataset
                or not np.array_equal(idx, np.arange(len(pool)))):
            raise ValueError("Cosine sidecar does not match the current run/pool")
    values = np.load(directory / f"scores_{pool.dataset}.npy", allow_pickle=False)
    if (values.shape != (len(pool),) or not np.isfinite(values[selected]).all()
            or np.any(np.abs(values[selected]) > 1.000001)):
        raise ValueError("Missing or invalid raw cosine scores")
    return values


class Budget:
    """One single-GPU job at a time; persistent cumulative wall time, at most 4 h."""

    def __init__(self, path, hours):
        self.path = Path(path)
        self.limit = min(hours, 4) * 3600
        self.job_id = str(uuid.uuid4())

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(".lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"jobs": []}
        if any(j["status"] == "running" for j in self.state["jobs"]):
            raise RuntimeError("Previous job did not finalize its budget; reconcile with sacct before continuing")
        self.prior = sum(j["seconds"] for j in self.state["jobs"])
        if self.prior >= self.limit:
            self.lock.close()
            raise RuntimeError("6h-1 cumulative GPU budget exhausted; stop and report")
        self.start = time.monotonic()
        self.last_write = self.start
        self.job = dict(job_id=self.job_id, seconds=0.0, status="running")
        self.state["jobs"].append(self.job)
        write_json(self.path, self.state)
        return self

    def check(self, force=False):
        elapsed = time.monotonic() - self.start
        self.job["seconds"] = elapsed
        if force or time.monotonic() - self.last_write > 30:
            write_json(self.path, self.state)
            self.last_write = time.monotonic()
        if self.prior + elapsed >= self.limit:
            raise RuntimeError("6h-1 cumulative GPU budget exhausted; stop and report")

    def __exit__(self, kind, value, traceback):
        self.job.update(seconds=time.monotonic() - self.start,
                        status="complete" if kind is None else "failed")
        write_json(self.path, self.state)
        self.lock.close()
