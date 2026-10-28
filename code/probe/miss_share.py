"""Share of isolated misses (cell ②) in GT change pixels, and images containing them.

Stages
  count  --dataset {second,sysu,levir,whu} [--shard i --shards n]   per-image counts -> parts/
  report                                                            figures + markdown table

Cells follow the exp8s5 corrected definition (batch46 geometry), applied to the same test pools
and detectors (official weights, probability > 0.5); LEVIR/WHU reuse exp8s8's patched loader.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np

STAGING = Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging')
OUT = STAGING / 'batch55'
REPO = Path(__file__).resolve().parents[2]
DATASETS = ('second', 'sysu', 'levir', 'whu')
LABEL = {'second': 'SECOND', 'sysu': 'SYSU-CD', 'levir': 'LEVIR-CD', 'whu': 'WHU-CD'}
SEGMENTS = [('tp', 'Detected (TP)'), ('c3', '③ weak signal'), ('c1', '① adjacent, no signal'),
            ('c2', '② isolated, no signal'), ('c5', '⑤ other (tiny)')]
BINS = [('0', 'No isolated miss'), ('1', '1 region'), ('2-5', '2–5 regions'), ('>5', '>5 regions')]


def geometry_module(ds):
    if ds in ('levir', 'whu'):
        sys.path.insert(0, str(STAGING / 'batch50'))
        import exp8s8_common as m  # patches exp8s5_common for LEVIR / WHU
        return m.geometry, m.scope_names(ds, 'test')
    sys.path.insert(0, str(STAGING / 'batch46'))
    import exp8s5_common as s5
    return s5.geometry, s5.scope_names(ds, 'test', 0)


def count(a):
    from scipy import ndimage as ndi
    geometry, names = geometry_module(a.dataset)
    rows = []
    for name in names[a.shard::a.shards]:
        d = geometry(a.dataset, 'test', name)
        gt, base, cell = d['gt'], d['base'], d['cell']
        row = dict(image=name, gt=int(gt.sum()), tp=int((gt & base).sum()))
        for k in (1, 2, 3, 5):
            row[f'c{k}'] = int((cell == k).sum())
        row['regions2'] = int(ndi.label(cell == 2, np.ones((3, 3), bool))[1])
        if row['tp'] + sum(row[f'c{k}'] for k in (1, 2, 3, 5)) != row['gt']:
            raise ValueError(f'{name}: segments do not partition GT')
        rows.append(row)
    path = OUT / 'parts' / f'{a.dataset}-{a.shard:03d}-of-{a.shards:03d}.json'
    path.write_text(json.dumps(rows))
    print(path, len(rows))


def load_rows(ds):
    rows = [r for p in sorted((OUT / 'parts').glob(f'{ds}-*.json')) for r in json.loads(p.read_text())]
    if len({r['image'] for r in rows}) != len(rows):
        raise ValueError(f'{ds}: duplicate images across parts')
    return rows


def bin_of(n):
    return '0' if n == 0 else '1' if n == 1 else '2-5' if n <= 5 else '>5'


def label_stack(ax, i, bottoms, values, keys, inside, fmt, color_of, weight_of, text_color_of, width=0.58):
    """Label large segments inside; stack small ones to the right of the bar with leader lines."""
    small = []
    for b, v, k in zip(bottoms, values, keys):
        if v <= 0:
            continue
        if v >= inside:
            ax.text(i, b + v / 2, fmt(v), ha='center', va='center', fontsize=7.5, weight=weight_of(k),
                    color=text_color_of(k))
        else:
            small.append((b + v / 2, v, k))
    y_prev = 101.5
    for y, v, k in sorted(small, reverse=True):
        y_text = min(y, y_prev - 3.6)
        y_prev = y_text
        x0, x1 = i + width / 2, i + width / 2 + 0.12
        ax.plot([x0, x1], [y, y_text], color='#8a97a6', lw=0.5, clip_on=False)
        ax.text(x1 + 0.02, y_text, fmt(v), ha='left', va='center', fontsize=7, weight=weight_of(k),
                color=color_of(k), clip_on=False)


def report(a):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    summary = {}
    for ds in DATASETS:
        rows = load_rows(ds)
        gt = sum(r['gt'] for r in rows)
        pix = {k: sum(r[k] for r in rows) / gt for k, _ in SEGMENTS}
        fn = gt - sum(r['tp'] for r in rows)
        img = {b: sum(bin_of(r['regions2']) == b for r in rows) / len(rows) for b, _ in BINS}
        summary[ds] = dict(images=len(rows), gt_pixels=gt, pixel_share=pix,
                           c2_share_of_fn=sum(r['c2'] for r in rows) / fn,
                           image_share=img, regions2=sum(r['regions2'] for r in rows),
                           images_with_c2=sum(r['regions2'] > 0 for r in rows))
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=1))

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.spines.top': False,
                         'axes.spines.right': False, 'pdf.fonttype': 42})
    names = [LABEL[d] for d in DATASETS]
    x = np.arange(len(DATASETS)) * 1.4
    fig_dir = REPO / 'figures' / 'miss_share'
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Figure 1: 100% stacked GT pixels.
    colors = {'tp': '#c9d3df', 'c3': '#8fa3ba', 'c1': '#5d7896', 'c2': '#e4572e', 'c5': '#dfe5ec'}
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    bottom = np.zeros(len(DATASETS))
    segs = []
    for key, label in SEGMENTS:
        vals = np.array([summary[d]['pixel_share'][key] for d in DATASETS]) * 100
        ax.bar(x, vals, 0.58, bottom=bottom, color=colors[key], label=label, edgecolor='white', linewidth=0.6)
        segs.append(vals)
        bottom += vals
    keys = [k for k, _ in SEGMENTS]
    for i in range(len(DATASETS)):
        vals_i = [seg[i] for seg in segs]
        bots = np.cumsum([0] + vals_i[:-1])
        label_stack(ax, x[i], bots, vals_i, keys, 4,
                    lambda v: f'{v:.1f}%' if v >= 0.95 else f'{v:.2f}%',
                    lambda k: '#e4572e' if k == 'c2' else '#3b4a5c',
                    lambda k: 'bold' if k == 'c2' else 'normal',
                    lambda k: 'white' if k in ('c1', 'c2') else '#1f2d3d')
    ax.set_xticks(x, names)
    ax.set_xlim(-0.6, x[-1] + 1.05)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Share of GT change pixels (%)')
    ax.legend(*[list(reversed(v)) for v in ax.get_legend_handles_labels()], frameon=False, fontsize=7.5,
              loc='upper left', bbox_to_anchor=(1.01, 1.0))
    fig.tight_layout()
    for ext in ('png', 'pdf'):
        fig.savefig(fig_dir / f'gt_pixel_share.{ext}', dpi=300, bbox_inches='tight')
    plt.close(fig)

    # Figure 2: images containing isolated misses.
    colors2 = {'0': '#dfe5ec', '1': '#f4a582', '2-5': '#e4572e', '>5': '#a3271a'}
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    bottom = np.zeros(len(DATASETS))
    segs2 = []
    for key, label in BINS:
        vals = np.array([summary[d]['image_share'][key] for d in DATASETS]) * 100
        ax.bar(x, vals, 0.58, bottom=bottom, color=colors2[key], label=label, edgecolor='white', linewidth=0.6)
        segs2.append(vals)
        bottom += vals
    for i in range(len(DATASETS)):
        vals_i = [seg[i] for seg in segs2]
        label_stack(ax, x[i], np.cumsum([0] + vals_i[:-1]), vals_i, [k for k, _ in BINS], 4,
                    lambda v: f'{v:.0f}%' if v >= 1 else f'{v:.1f}%', lambda k: '#3b4a5c', lambda k: 'normal',
                    lambda k: 'white' if k in ('2-5', '>5') else '#1f2d3d')
    ax.set_xticks(x, names)
    ax.set_xlim(-0.6, x[-1] + 1.05)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Share of test images (%)')
    ax.legend(*[list(reversed(v)) for v in ax.get_legend_handles_labels()], frameon=False, fontsize=7.5,
              title='Isolated miss regions', title_fontsize=7.5, loc='upper left', bbox_to_anchor=(1.01, 1.0))
    fig.text(0.01, -0.04, 'Region counts depend on annotation style; across datasets, compare\n'
             'presence ("any isolated miss") rather than counts.', fontsize=7, color='#3b4a5c')
    fig.tight_layout()
    for ext in ('png', 'pdf'):
        fig.savefig(fig_dir / f'images_with_isolated_miss.{ext}', dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(json.dumps({d: dict(c2=round(s['pixel_share']['c2'] * 100, 2), any=round(1 - s['image_share']['0'], 3))
                      for d, s in summary.items()}))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('stage', choices=['count', 'report'])
    p.add_argument('--dataset', choices=DATASETS)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    a = p.parse_args()
    (OUT / 'parts').mkdir(parents=True, exist_ok=True)
    count(a) if a.stage == 'count' else report(a)


if __name__ == '__main__':
    main()
