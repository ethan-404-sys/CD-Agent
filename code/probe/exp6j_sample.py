"""Sample routed U5 validation units, preserving exact score keys and inclusion probabilities."""
from __future__ import annotations
import argparse
import math
import random
from collections import Counter
from exp6j_common import *


def population(dataset, batch43):
    import numpy as np
    manifest = read_json(batch43 / dataset / 'manifest.json')
    rule = manifest['rule']
    expected = {'second': 297, 'sysu': 800}[dataset]
    if manifest['partial'] or len(manifest['images']['val']) != expected:
        raise ValueError(f'{dataset}: expected full {expected}-image validation inventory')
    if (rule['method'], rule['tau'], rule['A_max'], rule['prefilter_novel_lt']) != ('a', .1, None, None):
        raise ValueError('Expected batch43 frozen 1d rule, before novel-pixel filtering')
    frozen = read_json(PROBE.parent.parent / 'results/exp8s2_frozen_rule.json')
    if any(rule[k] != frozen[k] for k in ('method', 'tau', 'A_max', 'prefilter_novel_lt')):
        raise ValueError('batch43 routing differs from the experiment frozen rule')
    scores = {}
    for method in ('D', 'M1-ign3'):
        with np.load(batch43 / dataset / 'scores' / method / 'val.npz', allow_pickle=False) as z:
            scores[method] = {k: z[k] for k in z.files}
        if scores[method]['images'].tolist() != manifest['images']['val']:
            raise ValueError(f'{method}: image inventory mismatch')
    rows, offset, excluded = [], 0, Counter()
    for image_id, name in enumerate(manifest['images']['val']):
        with np.load(batch43 / dataset / 'units/val' / (Path(name).stem + '.npz'), allow_pickle=False) as z:
            if str(z['image']) != name or str(z['rule']) != manifest['rule_text']:
                raise ValueError(f'{name}: cached unit rule mismatch')
            count = len(z['id'])
            for method, score in scores.items():
                sl = slice(offset, offset + count)
                if not np.array_equal(score['id'][sl], z['id']) or not np.all(score['image_id'][sl] == image_id):
                    raise ValueError(f'{name}/{method}: unit score mismatch')
                if not np.isfinite(score['score'][sl]).all():
                    raise ValueError(f'{method}: nonfinite score')
            for j, unit_id in enumerate(z['id']):
                if z['novel'][j] < 100:
                    excluded['novel_lt_100'] += 1
                    continue
                label = int(z['label'][j])
                if label == -1:
                    excluded['middle_precision_0.1_to_0.5'] += 1
                    continue
                if label not in (0, 1) or z['r'][j] >= .1:
                    raise ValueError('Invalid cached routed label')
                rows.append(dict(cid=f'{dataset}:{name}:{int(unit_id)}', dataset=dataset, image=name,
                    unit_id=int(unit_id), image_id=image_id, label=label, majority=int(z['majority'][j]),
                    novel=int(z['novel'][j]), truth=int(z['truth'][j]), precision=float(z['precision'][j]),
                    D=float(scores['D']['score'][offset+j]), M1=float(scores['M1-ign3']['score'][offset+j])))
            offset += count
    if any(len(v['id']) != offset for v in scores.values()):
        raise ValueError('Unconsumed cached scores')
    return rows, dict(images=manifest['images']['val'], test_images=len(manifest['images']['test']),
                      rule={k: rule[k] for k in ('id', 'method', 'tau', 'A_max')},
                      prefilter_novel_lt=100, excluded=dict(excluded), cached_units=offset)


def draw(rows, seed):
    rng = random.Random(seed)
    selected, strata = [], {}
    positive = [r for r in rows if r['label'] == 1]
    for cell in sorted({r['majority'] for r in positive}):
        group = [r for r in positive if r['majority'] == cell]
        cap = {1: 200, 2: 800, 3: 200}.get(cell, len(group))
        chosen = rng.sample(group, min(cap, len(group)))
        pi = len(chosen) / len(group)
        strata[f'positive_cell{cell}'] = dict(population=len(group), selected=len(chosen), inclusion=pi)
        selected.extend(dict(r, stratum=f'positive_cell{cell}', inclusion=pi, weight=1/pi,
                             hard_negative=False, draw='positive') for r in chosen)
    neg = sorted((r for r in rows if r['label'] == 0), key=lambda r: (-r['D'], r['cid']))
    if not neg or not positive:
        raise ValueError('Both eligible classes are required')
    hard = neg[:math.ceil(.2 * len(neg))]
    hard_ids = {r['cid'] for r in hard}
    chosen_hard = rng.sample(hard, min(600, len(hard)))
    selected_hard = {r['cid'] for r in chosen_hard}
    remainder = [r for r in neg if r['cid'] not in selected_hard]
    chosen_random = rng.sample(remainder, min(600, len(remainder)))
    h, n, H, random_n = len(chosen_hard), len(neg), len(hard), len(chosen_random)
    p_random = random_n / (n-h) if n > h else 0.0
    p_hard = h/H + (1-h/H) * p_random
    for origin, group in (('hard', chosen_hard), ('random', chosen_random)):
        for r in group:
            is_hard = r['cid'] in hard_ids
            pi = p_hard if is_hard else p_random
            selected.append(dict(r, stratum='negative_hard' if is_hard else 'negative_other',
                                 inclusion=pi, weight=1/pi, hard_negative=is_hard, draw=origin))
    strata['negative'] = dict(population=n, hard_population=H, hard_draw=h, random_draw=random_n,
         hard_inclusion=p_hard, other_inclusion=p_random,
         formula='hard: h/H + (1-h/H)*r/(N-h); other: r/(N-h)',
         hard_cutoff_score=hard[-1]['D'], tie_break='D descending then cid ascending')
    selected.sort(key=lambda r: (r['image_id'], r['unit_id']))
    indexed(selected)
    return selected, strata


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--datasets', nargs='+', choices=('sysu', 'second'), default=['sysu', 'second'])
    p.add_argument('--batch43', type=Path, default=B43 / 'run')
    p.add_argument('--out', type=Path, default=RUN)
    p.add_argument('--seed', type=int, default=SEED)
    args = p.parse_args()
    args.out = local(args.out)
    for dataset in args.datasets:
        rows, info = population(dataset, args.batch43)
        selected, strata = draw(rows, args.seed)
        path = sample_path(args.out, 'step2', dataset)
        # A new sampling invocation must not silently replace an established selection.
        if path.exists():
            old = [{k:v for k,v in r.items() if k not in ('inputs', 'crop', 'context_crop', 'bbox')}
                   for r in read_jsonl(path)]
            if old != selected:
                raise ValueError('Existing sample selection differs; use a new --out')
        else:
            write_jsonl(path, selected)
        info.update(seed=args.seed, population=len(rows), positive=sum(r['label'] for r in rows),
                    selected=len(selected), strata=strata, batch43=str(args.batch43.resolve()),
                    positive_definition='novel truth fraction >= .5; negative < .1; middle excluded',
                    other_positive_cells='census so all-positive metrics retain population coverage')
        write_json(path.with_suffix('.meta.json'), info)
        print(dataset, len(rows), 'eligible ->', len(selected), 'sampled', flush=True)

if __name__ == '__main__':
    main()
