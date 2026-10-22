"""exp8: group SYSU-CD official val crops by their source 1024x1024 aerial image.

Each 256x256 crop was cut at a random position from one of the original image pairs and
randomly flipped/rotated.  Two crops of the same original share an overlap region whose
pixels are identical after undoing the flip/rotation.  We hash every PATCHxPATCH window of
each crop (t1 and t2 jointly), query a sparse grid of windows from every other crop under
all 8 dihedral transforms, and verify each hit by checking that the whole overlap is
pixel-identical in t1, t2 and the label.  Verified pairs are joined with union-find.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

PATCH = 16
GRID = 8          # query windows per side (GRID*GRID per crop and transform)
MIN_STD = 6.0     # skip near-uniform windows (water, shadow) as query anchors
MIN_OVERLAP = 32 * 32


def load(path):
    return np.asarray(Image.open(path).convert('RGB'), dtype=np.uint8)


def dihedral(a, k):
    """k in 0..7: rotate by 90*(k%4), then flip left-right when k>=4."""
    a = np.rot90(a, k % 4)
    return np.ascontiguousarray(a[:, ::-1] if k >= 4 else a)


def window_hash(code):
    """Polynomial hash of every PATCHxPATCH window; uint64 arithmetic wraps mod 2**64."""
    with np.errstate(over='ignore'):
        a, b = np.uint64(1000003), np.uint64(998244353)
        h, w = code.shape
        rows = np.zeros((h, w - PATCH + 1), np.uint64)
        p = np.uint64(1)
        for k in range(PATCH):
            rows += code[:, k:k + w - PATCH + 1] * p
            p *= a
        out = np.zeros((h - PATCH + 1, w - PATCH + 1), np.uint64)
        p = np.uint64(1)
        for k in range(PATCH):
            out += rows[k:k + h - PATCH + 1] * p
            p *= b
    return out


def pixel_code(t1, t2):
    c1 = (t1[..., 0].astype(np.uint64) << 16) | (t1[..., 1].astype(np.uint64) << 8) | t1[..., 2]
    c2 = (t2[..., 0].astype(np.uint64) << 16) | (t2[..., 1].astype(np.uint64) << 8) | t2[..., 2]
    return (c1 << 24) | c2


def overlap_equal(a, b, dy, dx):
    """a[y, x] == b[y - dy, x - dx] on the overlap; returns overlap area or 0."""
    h, w = a.shape[:2]
    ya, yb = max(0, dy), max(0, -dy)
    xa, xb = max(0, dx), max(0, -dx)
    hh, ww = h - abs(dy), w - abs(dx)
    if hh <= 0 or ww <= 0 or hh * ww < MIN_OVERLAP:
        return 0
    return hh * ww if np.array_equal(a[ya:ya + hh, xa:xa + ww], b[yb:yb + hh, xb:xb + ww]) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--t1', required=True)
    ap.add_argument('--t2', required=True)
    ap.add_argument('--label', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    names = sorted(p.name for p in Path(args.t1).iterdir() if p.suffix.lower() in ('.png', '.jpg', '.tif'))
    stacks, codes = [], []
    for n in names:
        t1, t2 = load(Path(args.t1) / n), load(Path(args.t2) / n)
        lab = np.asarray(Image.open(Path(args.label) / n).convert('L'))
        stacks.append(np.concatenate([t1, t2, lab[..., None]], axis=2))
        codes.append(pixel_code(t1, t2))
    n_img = len(names)
    side = codes[0].shape[0] - PATCH + 1

    # Index: every window of every crop in its stored orientation.
    keys = np.concatenate([window_hash(c).ravel() for c in codes])
    owner = np.repeat(np.arange(n_img, dtype=np.int32), side * side)
    pos = np.tile(np.arange(side * side, dtype=np.int32), n_img)
    order = np.argsort(keys, kind='stable')
    keys, owner, pos = keys[order], owner[order], pos[order]

    parent = list(range(n_img))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid = np.linspace(0, side - 1, GRID).astype(int)
    pairs, checked = [], set()
    for i in range(n_img):
        for k in range(8):
            s = dihedral(stacks[i], k)
            c = pixel_code(s[..., :3], s[..., 3:6])
            wh = window_hash(c)
            for qy in grid:
                for qx in grid:
                    if s[qy:qy + PATCH, qx:qx + PATCH, :6].std() < MIN_STD:
                        continue
                    h = wh[qy, qx]
                    lo, hi = np.searchsorted(keys, h, 'left'), np.searchsorted(keys, h, 'right')
                    for j, p in zip(owner[lo:hi], pos[lo:hi]):
                        j = int(j)
                        if j == i and k == 0:
                            continue
                        py, px = divmod(int(p), side)
                        dy, dx = int(py - qy), int(px - qx)   # stacks[j][y, x] == s[y - dy, x - dx]
                        key = (min(i, j), max(i, j), k if i < j else -k - 1, dy, dx)
                        if key in checked:
                            continue
                        checked.add(key)
                        area = overlap_equal(stacks[j], s, dy, dx)
                        if area and j != i:
                            pairs.append(dict(a=names[i], b=names[j], transform=k, dy=dy, dx=dx, overlap=area))
                            ri, rj = find(i), find(j)
                            if ri != rj:
                                parent[ri] = rj
        if (i + 1) % 200 == 0:
            print(f'{i + 1}/{n_img} crops, {len(pairs)} verified pairs', flush=True)

    groups = {}
    for i, n in enumerate(names):
        groups.setdefault(find(i), []).append(n)
    groups = sorted(groups.values(), key=lambda g: (-len(g), g[0]))
    sizes = [len(g) for g in groups]
    summary = dict(crops=n_img, verified_pairs=len(pairs), groups=len(groups),
                   size_hist={str(s): sizes.count(s) for s in sorted(set(sizes))},
                   groups_of_25=sizes.count(25), singletons=sizes.count(1),
                   params=dict(patch=PATCH, grid=GRID, min_std=MIN_STD, min_overlap=MIN_OVERLAP))
    (out / 'groups.json').write_text(json.dumps(dict(summary=summary, groups=groups), indent=1))
    with open(out / 'pairs.jsonl', 'w') as f:
        for p in pairs:
            f.write(json.dumps(p) + '\n')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
