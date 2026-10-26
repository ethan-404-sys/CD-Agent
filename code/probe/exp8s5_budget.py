"""Top-k exact union ceilings using saved scores; no inference or training."""
from exp8s5_common import *

METHODS = ('D', 'M1-ign3', 'area', 'random-1020', 'random-1021', 'random-1022', 'oracle')


def orders(d, units):
    m1 = image_scores(d)
    ids = np.asarray([u['id'] for u in units])
    values = {'D': [u['dscore'] for u in units],
              'M1-ign3': [float(m1[u['index']]) for u in units],
              'area': [u['area'] for u in units], 'oracle': [u['precision'] for u in units]}
    result = {k: np.lexsort((ids, -np.asarray(v, float))) for k, v in values.items()}
    image_index = manifest(d['ds'])['images'][d['split']].index(d['name'])
    for seed in SEEDS:
        rng = np.random.default_rng(np.random.SeedSequence([seed, image_index]))
        result[f'random-{seed}'] = rng.permutation(len(units))
    return result


def run(a):
    if a.split == 'train':
        raise ValueError('Budget evaluation is val/test only')
    choice = frozen(a)
    for name in names(a, True):
        d = load(a.dataset, a.split, name)
        units = filtered(d, choice['scheme'])
        results = {}
        for method, order in orders(d, units).items():
            union = np.zeros_like(d['gt'])
            last, positive = 0, 0
            results[method] = {}
            for k in KS:
                stop = len(units) if k == 'all' else min(k, len(units))
                for index in order[last:stop]:
                    u = units[int(index)]
                    add_mask(union, u)
                    positive += u['positive']
                results[method][str(k)] = dict(coverage(d, union), images=1,
                                                candidates=stop, positives=int(positive))
                last = stop
        write(part_path(a, 'budget', name), envelope(a, name, scheme=choice['scheme'], methods=results))


def crossing(rows, target):
    for k in KS:
        r = rows[str(k)]
        if r['object_recall2'] is not None and r['object_recall2'] >= target:
            return dict(k=k, candidates_per_image=r['candidates_per_image'],
                        object_recall2=r['object_recall2'], mllm_calls=r['mllm_calls'],
                        gpu_hours=r['gpu_hours'])
    return None


def merge(a):
    choice = frozen(a)
    totals = {m: {str(k): {} for k in KS} for m in METHODS}
    for r in parts(a, 'budget'):
        if r['scheme'] != choice['scheme']:
            raise ValueError('Budget shard filter differs from frozen selection')
        for m in METHODS:
            for k in KS:
                key = str(k)
                totals[m][key] = sum_rows([totals[m][key], r['methods'][m][key]])
    # Average additive counts across seeds before computing pooled rates.
    totals['random-mean'] = {}
    for k in KS:
        key = str(k)
        total = sum_rows(totals[f'random-{s}'][key] for s in SEEDS)
        totals['random-mean'][key] = {field: value / len(SEEDS) for field, value in total.items()}
    results = {m: {k: rates(v) for k, v in ks.items()} for m, ks in totals.items()}
    for k in KS:
        values = [results[f'random-{s}'][str(k)]['object_recall2'] for s in SEEDS]
        results['random-mean'][str(k)]['object_recall2_min'] = min(values) if all(v is not None for v in values) else None
        results['random-mean'][str(k)]['object_recall2_max'] = max(values) if all(v is not None for v in values) else None
    # All candidates must give the same union regardless of ordering.
    for m in METHODS:
        if totals[m]['all'] != totals['D']['all']:
            raise ValueError(f'{m}: all-candidate counts depend on ranking')
        for key in ('covered1', 'covered2', 'hits1', 'hits2', 'candidates'):
            seq = [totals[m][str(k)][key] for k in KS]
            if any(x > y for x, y in zip(seq, seq[1:])):
                raise ValueError(f'{m}: nonmonotone {key}')
    # Cross-check the separate filter implementation against the full budget.
    f = read(a.out / a.dataset / f'filter_{a.split}.json')
    if f['limit'] != a.limit or f['images'] != names(a):
        raise ValueError('Filter/budget inventories differ')
    full = f['filters'][choice['scheme']]
    if any(full[k] != v for k, v in totals['D']['all'].items()):
        raise ValueError('All budget candidates differ from filtered union')
    write(a.out / a.dataset / f'budget_{a.split}.json', dict(dataset=a.dataset, split=a.split,
          limit=a.limit, partial=bool(a.limit), images=names(a), scheme=choice['scheme'],
          methods=results, target_budgets={m: {str(t): crossing(ks, t) for t in (.5, .7)}
                                         for m, ks in results.items()},
          target_rule='预设k网格首次达到；不插值；null表示全部候选仍未达到；测试仅描述'))


def plot(a):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    methods = ('D', 'M1-ign3', 'area', 'random-mean', 'oracle')
    folder = local(a.out / 'figures/exp8s5')
    folder.mkdir(parents=True, exist_ok=True)
    for axis in ('k', 'candidates'):
        if axis == 'candidates':
            fig, grid = plt.subplots(4, 2, figsize=(13, 13), constrained_layout=True,
                                     gridspec_kw={'height_ratios': [4, 1.2, 4, 1.2]})
            axes, table_axes = grid[::2], grid[1::2]
        else:
            fig, axes = plt.subplots(2, 2, figsize=(13, 10), constrained_layout=True)
        for i, ds in enumerate(('second', 'sysu')):
            for j, split in enumerate(('val', 'test')):
                report = read(a.out / ds / f'budget_{split}.json')
                if report['limit'] != a.limit:
                    raise ValueError('Plot scope mismatch')
                ax = axes[i, j]
                for method in methods:
                    rows = report['methods'][method]
                    # "all" is categorical; put it after 50 on the log axis
                    # and label it explicitly, rather than implying k=100.
                    xs = [100 if k == 'all' else k for k in KS] if axis == 'k' else [rows[str(k)]['candidates_per_image'] for k in KS]
                    ys = [np.nan if rows[str(k)]['object_recall2'] is None else rows[str(k)]['object_recall2'] * 100 for k in KS]
                    line, = ax.plot(xs, ys, marker='o', markersize=3, label=method)
                    if method == 'random-mean':
                        low = [np.nan if rows[str(k)]['object_recall2_min'] is None else rows[str(k)]['object_recall2_min'] * 100 for k in KS]
                        high = [np.nan if rows[str(k)]['object_recall2_max'] is None else rows[str(k)]['object_recall2_max'] * 100 for k in KS]
                        ax.fill_between(xs, low, high, color=line.get_color(), alpha=.15)
                    if axis == 'candidates':
                        for target in (.5, .7):
                            point = report['target_budgets'][method][str(target)]
                            if point:
                                ax.scatter([point['candidates_per_image']], [point['object_recall2'] * 100],
                                           marker='*' if target == .5 else 's', s=45 if target == .5 else 18,
                                           color=line.get_color(), zorder=4)
                if axis == 'candidates':
                    cells = []
                    for method in methods:
                        points = [report['target_budgets'][method][str(t)] for t in (.5, .7)]
                        cells.append([method] + [f'{p["candidates_per_image"]:.2f}' if p else 'not reached' for p in points])
                    table_axes[i, j].axis('off')
                    tab = table_axes[i, j].table(cellText=cells,
                        colLabels=['Candidates / image', '50% recall', '70% recall'],
                        cellLoc='center', bbox=[0, 0, 1, 1])
                    tab.auto_set_font_size(False)
                    tab.set_fontsize(8)
                if axis == 'k':
                    ax.set_xscale('log')
                    ax.set_xlim(.85, 120)
                    ax.set_xticks([1, 2, 3, 5, 10, 20, 50, 100], ['1', '2', '3', '5', '10', '20', '50', 'all'])
                if report['methods']['D']['all']['objects2'] == 0:
                    ax.text(.5, .3, 'No cell-2 GT targets in this scope',
                            transform=ax.transAxes, ha='center', fontsize=9)
                for level in (50, 70):
                    ax.axhline(level, color='gray', linewidth=.6, linestyle='--')
                ax.set(title=f'{ds.upper()} / {split} / {report["scheme"]}' + (' / SMOKE' if a.limit else ''),
                       xlabel='Per-image k (all shown separately)' if axis == 'k' else 'Actual candidates per image',
                       ylabel='Cell-2 GT-object recall ceiling (%)', ylim=(0, 101))
                ax.grid(alpha=.2)
                ax.legend(fontsize=8)
        fig.savefig(folder / f'budget-{axis}.png', dpi=160, bbox_inches='tight', pad_inches=.15)
        plt.close(fig)


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run', 'merge', 'plot')))
    start = time.monotonic()
    {'run': run, 'merge': merge, 'plot': plot}[a.stage](a)
    timed(a, 'budget-' + a.stage, start)
