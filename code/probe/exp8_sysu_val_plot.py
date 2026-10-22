"""exp8: SYSU official val grouping -- block assignment, 80/20 group split, spot-check figures.

Official val crops are 12000.png ... 15999.png.  Pixel-overlap clustering (exp8_sysu_val_group.py)
shows every verified component lies inside one block of 25 consecutive numbers, so the source
aerial image of crop n is taken as block (n - 12000) // 25.  The verifier train/val split is
drawn over these 160 blocks, never splitting a block.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

FIRST, BLOCK = 12000, 25


def num(name):
    return int(Path(name).stem)


def dihedral(a, k):
    a = np.rot90(a, k % 4)
    return np.ascontiguousarray(a[:, ::-1] if k >= 4 else a)


def load(path):
    return np.asarray(Image.open(path).convert('RGB'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--group-dir', required=True)
    ap.add_argument('--t2', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--train-frac', type=float, default=0.8)
    args = ap.parse_args()
    gdir, out = Path(args.group_dir), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    groups = json.loads((gdir / 'groups.json').read_text())['groups']
    comp_of = {n: i for i, g in enumerate(groups) for n in g}
    blocks = defaultdict(list)
    for n in comp_of:
        blocks[(num(n) - FIRST) // BLOCK].append(n)
    spanning = sum(1 for g in groups if len({(num(n) - FIRST) // BLOCK for n in g}) > 1)
    split_into = Counter(len({comp_of[n] for n in v}) for v in blocks.values())

    ids = sorted(blocks)
    rng = random.Random(args.seed)
    rng.shuffle(ids)
    k = round(len(ids) * args.train_frac)
    train_blocks, val_blocks = sorted(ids[:k]), sorted(ids[k:])
    split = dict(
        rule='block = (n - 12000) // 25; 80/20 split over blocks, seed %d' % args.seed,
        blocks=len(ids), components=len(groups), components_spanning_blocks=spanning,
        blocks_by_component_count={str(a): b for a, b in sorted(split_into.items())},
        train_blocks=train_blocks, val_blocks=val_blocks,
        train_images=sorted(f'train_{n}' for b in train_blocks for n in blocks[b]),
        val_images=sorted(f'train_{n}' for b in val_blocks for n in blocks[b]))
    (out / 'split.json').write_text(json.dumps(split, indent=1))
    print({k: v for k, v in split.items() if not isinstance(v, list)},
          len(split['train_images']), len(split['val_images']))

    t2 = Path(args.t2)
    full = [b for b in sorted(blocks) if split_into and len({comp_of[n] for n in blocks[b]}) == 1][:2]
    broken = [b for b in sorted(blocks) if len({comp_of[n] for n in blocks[b]}) >= 2][:2]
    for tag, sel in (('full', full), ('split', broken)):
        for b in sel:
            names = sorted(blocks[b], key=num)
            comps = sorted({comp_of[n] for n in names})
            colour = {c: plt.cm.tab10(i % 10) for i, c in enumerate(comps)}
            fig, axes = plt.subplots(5, 5, figsize=(10, 10.6))
            for ax, n in zip(axes.ravel(), names):
                ax.imshow(load(t2 / n))
                ax.set_xticks([]), ax.set_yticks([])
                for s in ax.spines.values():
                    s.set_edgecolor(colour[comp_of[n]]), s.set_linewidth(4)
                ax.set_title(n, fontsize=8)
            fig.suptitle(f'block {b} (crops {FIRST + BLOCK * b}-{FIRST + BLOCK * b + 24}): '
                         f'{len(comps)} overlap component(s), border colour = component', fontsize=11)
            fig.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.93, wspace=0.05, hspace=0.18)
            fig.savefig(out / f'block_{tag}_{b}.jpg', dpi=80, pil_kwargs=dict(quality=88))
            plt.close(fig)

    pairs = [json.loads(l) for l in open(gdir / 'pairs.jsonl')]
    rng = random.Random(args.seed)
    picks = [p for p in rng.sample(pairs, 200) if p['transform'] != 0][:2] + \
            [p for p in rng.sample(pairs, 200) if p['transform'] == 0][:1]
    fig, axes = plt.subplots(len(picks), 3, figsize=(10.5, 3.6 * len(picks)))
    for row, p in zip(axes, picks):
        a, b = load(t2 / p['a']), load(t2 / p['b'])
        ta = dihedral(a, p['transform'])
        dy, dx = p['dy'], p['dx']
        h, w = b.shape[:2]
        # b[y, x] == ta[y - dy, x - dx]: overlap box in b's frame and in ta's frame.
        yb, xb, hh, ww = max(0, dy), max(0, dx), h - abs(dy), w - abs(dx)
        ya, xa = max(0, -dy), max(0, -dx)
        for ax, img, (y0, x0), title in ((row[0], a, (None, None), f"{p['a']} (stored)"),
                                         (row[1], ta, (ya, xa), f"{p['a']} after transform {p['transform']}"),
                                         (row[2], b, (yb, xb), p['b'])):
            ax.imshow(img)
            ax.set_xticks([]), ax.set_yticks([])
            ax.set_title(title, fontsize=9)
            if y0 is not None:
                ax.add_patch(plt.Rectangle((x0 - .5, y0 - .5), ww, hh, fill=False, ec='red', lw=2))
        row[2].set_xlabel(f"overlap {hh}x{ww} px, pixel-identical in t1, t2 and label", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / 'pair_examples.jpg', dpi=80, pil_kwargs=dict(quality=88))


if __name__ == '__main__':
    main()
