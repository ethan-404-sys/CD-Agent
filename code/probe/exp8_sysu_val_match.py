"""exp8: find the official SYSU-CD val crops inside the local 16,000-image "train" folder.

Every image is decoded and its pixels digested; an official val pair matches a local pair
when t1, t2 and the label are all pixel-identical.  If an exact t1 match is missing, the
8 flips/rotations are tried as well.  Digests are used only in memory and never written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image


def pixels(path, mode):
    return np.asarray(Image.open(path).convert(mode))


def digest(a):
    return hashlib.blake2b(np.ascontiguousarray(a).tobytes(), digest_size=16).digest()


def dihedral(a, k):
    a = np.rot90(a, k % 4)
    return a[:, ::-1] if k >= 4 else a


def binary(lab):
    return (lab > 0).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--val-t1', required=True)
    ap.add_argument('--val-t2', required=True)
    ap.add_argument('--val-label', required=True)
    ap.add_argument('--local-root', required=True, help='SYSU-CD root with img1/img2/label_unch0chi255')
    ap.add_argument('--prefix', default='train_')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    root = Path(args.local_root)

    local = sorted(p.name for p in (root / 'img1').iterdir() if p.name.startswith(args.prefix))
    by_t1 = defaultdict(list)
    for i, n in enumerate(local):
        by_t1[digest(pixels(root / 'img1' / n, 'RGB'))].append(n)
        if (i + 1) % 2000 == 0:
            print(f'indexed {i + 1}/{len(local)}', flush=True)

    val = sorted(p.name for p in Path(args.val_t1).iterdir() if p.suffix.lower() in ('.png', '.jpg', '.tif'))
    rows, stats = [], defaultdict(int)
    for n in val:
        t1 = pixels(Path(args.val_t1) / n, 'RGB')
        t2 = pixels(Path(args.val_t2) / n, 'RGB')
        lab = binary(pixels(Path(args.val_label) / n, 'L'))
        hit, how = None, 'none'
        for k in range(8):
            for m in by_t1.get(digest(dihedral(t1, k)), []):
                lt2 = pixels(root / 'img2' / m, 'RGB')
                llab = binary(pixels(root / 'label_unch0chi255' / m, 'L'))
                if np.array_equal(dihedral(t2, k), lt2) and np.array_equal(dihedral(lab, k), llab):
                    hit, how = m, 'exact' if k == 0 else f'transform{k}'
                    break
                how = 't1_only'
            if hit:
                break
        stats[how] += 1
        rows.append(dict(val=n, local=hit, match=how))

    matched = [r['local'] for r in rows if r['local']]
    summary = dict(val_images=len(val), local_images=len(local), matched=len(matched),
                   unique_local=len(set(matched)), by_kind=dict(stats),
                   local_t1_duplicates=sum(len(v) - 1 for v in by_t1.values() if len(v) > 1))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'match_summary.json').write_text(json.dumps(summary, indent=1))
    with open(out / 'val_to_local.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
