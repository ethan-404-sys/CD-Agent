"""Weighted image-cluster evaluation, same-sample controls and development-only freezing."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path

import exp6l_common as c


def ranking(rows, scores, predictions=None, n_boot=2000):
    import numpy as np
    y = np.asarray([r['label'] for r in rows], int)
    w = np.asarray([r['weight'] for r in rows], float)
    s = np.asarray(scores, float)
    if s.shape != y.shape or not np.isfinite(s).all() or not np.isfinite(w).all() or (w <= 0).any():
        raise ValueError('Invalid aligned scores/weights')
    strata = np.asarray([r['stratum'] for r in rows])
    primary = (strata == 'pos2') | (y == 0)
    def measure(idx):
        out = {}
        for name, mask in [('primary', primary), ('all', np.ones(len(rows), bool))]:
            chosen = idx[mask[idx]]
            out[name + '_auc'] = c.store.auc(y[chosen], s[chosen], w[chosen])
            out[name + '_ap'] = c.store.ap(y[chosen], s[chosen], w[chosen])
        return out
    out = measure(np.arange(len(rows)))
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(row['image'], []).append(i)
    members = [np.asarray(ids) for ids in groups.values()]
    rng = np.random.default_rng(c.BOOT_SEED)
    boot = {key: [] for key in out}
    for _ in range(n_boot):
        idx = np.concatenate([members[g] for g in rng.integers(0, len(members), len(members))])
        for key, value in measure(idx).items():
            if value is not None and math.isfinite(value):
                boot[key].append(value)
    out['bootstrap_valid'] = {key: len(values) for key, values in boot.items()}
    out['ci95'] = {key: np.quantile(values, [.025, .975]).tolist() if values else None for key, values in boot.items()}
    out.update(n=len(rows), images=len(groups), bootstrap=n_boot, seed=c.BOOT_SEED)
    if predictions is not None:
        pred = np.asarray(predictions, float)
        out['strata'] = {name: dict(n=int((strata == name).sum()),
                                     yes_rate=float(np.average(pred[strata == name], weights=w[strata == name])))
                         for name in c.store.STRATA}
    return out


def align_npz(path, rows):
    import numpy as np
    with np.load(path, allow_pickle=False) as data:
        images = data['images']
        mapping = {(str(images[int(i)]), int(unit)): float(value)
                   for i, unit, value in zip(data['image_id'], data['id'], data['score'], strict=True)}
        if len(mapping) != len(data['score']):
            raise ValueError(f'Duplicate image/unit pairs: {path}')
    return [mapping[(r['image'], int(r['unit_id']))] for r in rows]


def controls(dataset, rows, final, fuse):
    root = c.HERE.parent / 'batch48/run' / dataset / 'scores'
    split = 'test' if final else 'val'
    methods = {'D': [r['det_mean'] for r in rows], 'M1-ign3': [r['m1_score'] for r in rows]}
    frozen = c.read(c.HERE.parent / 'batch48/run' / dataset / 'evaluation/final/frozen.json')
    if fuse not in frozen['methods']:
        raise ValueError('Fusion comparator is not in the existing exp8s7 freeze')
    for method in ['img-s1020', 'meta-m1-s1020', fuse]:
        methods[method] = align_npz(root / method / f'{split}.npz', rows)
    return methods


def db_run(path, name):
    with c.closing(c.readonly(path)) as db:
        run = db.execute('SELECT * FROM run WHERE name=?', (name,)).fetchone()
        if run is None:
            raise ValueError(f'Unknown run: {name}')
        config = json.loads(run['config'])['exp6l']
        results = db.execute('SELECT s.cid,o.* FROM output o JOIN sample s ON s.id=o.sample_id '
                             'WHERE o.run_id=?', (run['id'],)).fetchall()
        pred = {}
        for result in results:
            row = dict(result)
            meta = json.loads(row.pop('extra') or '{}')
            row.update(meta)
            pred[row['cid']] = row
        report = json.loads(run['config'])['report']
    return config, pred, [], report


def effective_scores(pred, rows):
    # A failed top-5 readout must not turn absent probability into zero.
    scores, answers = [], []
    for row in rows:
        result = pred[row['cid']]
        value = c.store.score(dict(p_yes=result.get('p_yes'), answer=result.get('answer'), confidence=result.get('confidence')))
        if value is None or not math.isfinite(value):
            raise ValueError('Missing/nonfinite candidate score')
        scores.append(value)
        answers.append(int(result['answer'] == 'yes'))
    return scores, answers


def evaluate(config, pred, report, fuse):
    if not report['complete']:
        return dict(config=config, report=report, eligible_for_freeze=False)
    rows, _ = c.manifests(config['dataset'], config['final'], config['half'])
    if set(pred) != {r['cid'] for r in rows}:
        raise ValueError('Run coverage differs from eval_set')
    scores, answers = effective_scores(pred, rows)
    out = dict(config=config, report=report, eligible_for_freeze=True,
               metrics=ranking(rows, scores, answers),
               controls={name: ranking(rows, values) for name, values in controls(config['dataset'], rows, config['final'], fuse).items()})
    if not config['half']:
        half, _ = c.manifests(config['dataset'], config['final'], half=True)
        hs, ha = effective_scores(pred, half)
        out['half_subset'] = dict(eval_set=f'exp6l_{"test" if config["final"] else "dev"}_half_{config["dataset"]}',
                                 metrics=ranking(half, hs, ha),
                                 controls={name: ranking(half, values) for name, values in controls(config['dataset'], half, config['final'], fuse).items()})
    return out


FREEZE_KS = (0, 4, 8, 16)


def freeze(reports, destination, final=False, stage='final'):
    reports = [r for r in reports if r['config']['k'] in FREEZE_KS]
    if final:
        raise ValueError('Cannot select configurations using test results')
    grids = {}
    for report in reports:
        conf, state = report['config'], report['report']
        if state['completed'] + state['failed_candidates'] != state['expected']:
            raise ValueError('Unfinished runs cannot enter freezing; resume them first')
        grids.setdefault((conf['dataset'], conf['model']), set()).add((conf['variant'], conf['k'], conf['bq']))
    # Ethan 2026-10-27（文档 76ddaa5）：冻结只在 k=0/4/8/16 中选；已跑的 k=12 只作描述，不参与选择。
    required = {('Z0', 0, False), ('Z1', 0, False), *[('F', k, False) for k in FREEZE_KS[1:]]}
    for (dataset, model), grid in grids.items():
        if not required <= grid:
            raise ValueError(f'{dataset}/{model}: missing development grid configurations')
        if stage == 'final' and model == c.MODEL_NAMES['api'] and not any(bq for _, _, bq in grid):
            raise ValueError('Final API selection requires the BQ comparison; use --freeze-stage individual first')
    selected = {}
    for report in reports:
        conf = report['config']
        if conf['final']:
            raise ValueError('Test data cannot enter freezing')
        if not report['eligible_for_freeze']:
            continue
        ap = report['metrics']['primary_ap']
        if ap is None:
            continue
        usage = report['report']['usage']
        cheap = usage['cost'] + usage.get('reserved_unknown_cny', 0) if conf['half'] else usage['seconds'] + usage['load_seconds']
        rank = (-ap, cheap, conf['k'], conf['variant'], conf['bq'])
        models = selected.setdefault(conf['dataset'], {})
        previous = models.get(conf['model'])
        if previous is None or rank < tuple(previous['selection_rank']):
            models[conf['model']] = dict(config=conf, primary_ap=ap, selection_rank=list(rank), source=report['source'])
    if not selected:
        raise ValueError('No complete development runs to freeze')
    result = dict(stage=stage, selection='weighted primary AP; exact ties use lower cost/seconds including loading', selected=selected)
    destination = c.local(destination)
    if destination.exists() and c.read(destination) != result:
        raise ValueError('Freeze file already exists with a different selection; use a new named stage')
    c.write(destination, result)


def validate_final(conf, frozen):
    model = c.MODEL_NAMES['qwen'] if conf['model'] == c.MODEL_NAMES['earthdial'] else conf['model']
    chosen = frozen['selected'][conf['dataset']][model]['config']
    for key in ('variant', 'k', 'form', 'bq', 'demos', 'prompt_text'):
        if conf[key] != chosen[key]:
            raise ValueError(f'Test configuration differs from frozen {key}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', type=Path, nargs='*', default=[])
    p.add_argument('--db', type=Path, default=c.DB)
    p.add_argument('--db-runs', nargs='*', default=[])
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--freeze', type=Path)
    p.add_argument('--freeze-stage', choices=['individual', 'final'], default='final')
    p.add_argument('--final', action='store_true')
    p.add_argument('--frozen', type=Path)
    p.add_argument('--fuse-sysu', default='fuse-img-a0.5-s1020')
    p.add_argument('--fuse-second', default='fuse-img-a0.75-s1020')
    p.add_argument('--dry-run', action='store_true', help='Check manifests and score alignment without computing metrics')
    a = p.parse_args()
    if a.freeze and a.dry_run:
        p.error('Cannot freeze dry-run results')
    if a.final and (a.freeze or not a.frozen):
        p.error('--final requires --frozen and disallows --freeze')
    if not a.runs and not a.db_runs:
        p.error('Specify at least one run source')
    if not a.dry_run and not os.environ.get('SLURM_JOB_ID'):
        p.error('2000-replicate evaluation must run in Slurm')
    sources = [(str(path), c.load_run(path)) for path in a.runs]
    sources += [(name, db_run(a.db, name)) for name in a.db_runs]
    reports, seen_final = [], set()
    frozen = c.read(a.frozen) if a.final else None
    if a.final and frozen.get('stage') != 'final':
        raise ValueError('Test evaluation requires the final development freeze')
    for source, (conf, pred, calls, report) in sources:
        if conf['final'] != a.final:
            raise ValueError('Use --final exclusively for test runs')
        if a.final:
            if report['completed'] + report['failed_candidates'] < report['expected']:
                raise ValueError('Test run is unfinished; resume inference before final evaluation')
            validate_final(conf, frozen)
            key = conf['dataset'], conf['model']
            if key in seen_final:
                raise ValueError('Only one test run per model/dataset')
            seen_final.add(key)
        fuse = a.fuse_sysu if conf['dataset'] == 'sysu' else a.fuse_second
        if a.dry_run:
            rows, _ = c.manifests(conf['dataset'], conf['final'], conf['half'])
            aligned = controls(conf['dataset'], rows, conf['final'], fuse)
            item = dict(config=conf, source=source, report=report, dry_run=True,
                        aligned_controls={k: len(v) for k, v in aligned.items()})
        else:
            item = dict(source=source, **evaluate(conf, pred, report, fuse))
        reports.append(item)
    if a.final and c.local(a.out).exists():
        raise ValueError('Final evaluation output already exists; it will not be overwritten')
    c.write(a.out, dict(final=a.final, runs=reports))
    if a.freeze:
        freeze(reports, a.freeze, stage=a.freeze_stage)
    print(json.dumps(dict(runs=len(reports), dry_run=a.dry_run, output=str(a.out))))


if __name__ == '__main__':
    main()
