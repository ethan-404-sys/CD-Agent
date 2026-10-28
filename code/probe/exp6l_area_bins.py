"""② AUC/AP by candidate novel-pixel area, MLLM vs same-sample D / M1-ign3 / exp8s7 fusion.

Reuses existing exp6l run journals only; no model calls.  Rows are the primary task (② positives vs fill
negatives).  Within each bin: weighted AUC/AP, 2000-replicate image-cluster bootstrap 95% CI, and the
paired MLLM - D AP difference on the same resamples.
"""
from __future__ import annotations
import argparse
import json
import math
import os

import numpy as np

import exp6l_common as c
import exp6l_eval as e

BINS = [('< 200', 0, 200), ('200–500', 200, 500), ('500–2000', 500, 2000), ('≥ 2000', 2000, math.inf)]
FUSE = {'sysu': 'fuse-img-a0.5-s1020', 'second': 'fuse-img-a0.75-s1020'}
NEG = ('hard_neg', 'rand_neg')
RUNS = {
    'test': ['final_qwen_{ds}', 'api_{ds}_test'],
    'dev': ['qwen_{ds}_Z0', 'qwen_{ds}_Z1', 'qwen_{ds}_F16', 'dars4b_{ds}_Z0', 'dars4b_{ds}_Z1', 'dars4b_{ds}_F8',
            'api_{ds}_Z0', 'api_{ds}_Z1', 'api_{ds}_F16'],
}
MIN_POS = 10


def metric(y, s, w):
    return c.store.auc(y, s, w), c.store.ap(y, s, w)


def bin_stats(rows, mllm, comps, n_boot):
    y = np.asarray([r['label'] for r in rows], int)
    w = np.asarray([r['weight'] for r in rows], float)
    scores = {'MLLM': np.asarray(mllm, float), **{k: np.asarray(v, float) for k, v in comps.items()}}
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(r['image'], []).append(i)
    members = [np.asarray(v) for v in groups.values()]
    point = {k: metric(y, s, w) for k, s in scores.items()}
    rng = np.random.default_rng(c.BOOT_SEED)
    boot = {k: ([], []) for k in scores}
    diff = []
    for _ in range(n_boot):
        idx = np.concatenate([members[g] for g in rng.integers(0, len(members), len(members))])
        yy, ww = y[idx], w[idx]
        if yy.min() == yy.max():
            continue
        vals = {k: metric(yy, s[idx], ww) for k, s in scores.items()}
        for k, (auc, ap) in vals.items():
            boot[k][0].append(auc)
            boot[k][1].append(ap)
        diff.append(vals['MLLM'][1] - vals['D'][1])
    q = lambda v: np.quantile(v, [.025, .975]).tolist() if v else None
    pos = int(y.sum())
    out = dict(n_pos=pos, n_neg=int(len(y) - pos), images=len(members),
               weighted_pos_rate=float(w[y == 1].sum() / w.sum()), valid_boot=len(diff),
               reference_only=pos < MIN_POS)
    out['methods'] = {k: dict(auc=point[k][0], ap=point[k][1], auc_ci=q(boot[k][0]), ap_ci=q(boot[k][1]))
                      for k in scores}
    out['ap_diff_mllm_minus_d'] = dict(value=point['MLLM'][1] - point['D'][1], ci=q(diff))
    return out


def analyse(source, n_boot):
    conf, pred, _, report = c.load_run(source)
    if not report['complete']:
        raise ValueError(f'{source}: incomplete run')
    ds = conf['dataset']
    rows, _ = c.manifests(ds, conf['final'], conf['half'])
    scores, _ = e.effective_scores(pred, rows)
    comp = e.controls(ds, rows, conf['final'], FUSE[ds])
    keep = [i for i, r in enumerate(rows) if r['stratum'] == 'pos2' or r['stratum'] in NEG]
    rows = [rows[i] for i in keep]
    scores = [scores[i] for i in keep]
    comp = {k: [comp[k][i] for i in keep] for k in ('D', 'M1-ign3', FUSE[ds])}
    out = dict(source=source, dataset=ds, model=conf['model'], variant=conf['variant'], k=conf['k'],
               final=conf['final'], half=conf['half'], bins={})
    for name, lo, hi in [('全部', 0, math.inf), *BINS]:
        idx = [i for i, r in enumerate(rows) if lo <= r['novel'] < hi]
        if not idx:
            out['bins'][name] = None
            continue
        out['bins'][name] = bin_stats([rows[i] for i in idx], [scores[i] for i in idx],
                                      {k: [v[i] for i in idx] for k, v in comp.items()}, n_boot)
    # All exp6k candidates were drawn after the exp8s6 frozen filter, so the filter split is degenerate.
    out['filter_check'] = dict(min_novel=min(r['novel'] for r in rows))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default='run/area_bins.json')
    p.add_argument('--n-boot', type=int, default=2000)
    a = p.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        p.error('Run inside Slurm')
    results = []
    for split, names in RUNS.items():
        for ds in ('sysu', 'second'):
            for name in names:
                results.append(analyse(f'run/{name.format(ds=ds)}', a.n_boot))
                print(results[-1]['source'], flush=True)
    c.write(a.out, dict(bins=[b[0] for b in BINS], min_pos=MIN_POS, seed=c.BOOT_SEED, n_boot=a.n_boot, runs=results))


if __name__ == '__main__':
    main()
