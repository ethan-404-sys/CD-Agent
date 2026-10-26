"""Compare F0-F3, freeze on validation, and report test with the frozen filter."""
from exp8s5_common import *


def run(a):
    if a.split == 'train':
        raise ValueError('Filter comparisons use val/test; page uses train independently')
    choice = frozen(a) if a.split == 'test' else None
    for name in names(a, True):
        d = load(a.dataset, a.split, name)
        results = {}
        for scheme in FILTERS:
            units = filtered(d, scheme)
            union = np.zeros_like(d['gt'])
            for u in units:
                add_mask(union, u)
            kept = {u['id'] for u in units}
            dropped = [u for u in d['candidates'] if u['id'] not in kept]
            results[scheme] = dict(coverage(d, union), images=1, candidates=len(units),
                positives=sum(u['positive'] for u in units), dropped=len(dropped),
                dropped_positive=sum(u['positive'] for u in dropped),
                dropped_any_truth=sum(int(d['z']['truth'][u['index']]) > 0 for u in dropped))
        write(part_path(a, 'filter', name), envelope(a, name, filters=results,
              selected=choice['scheme'] if choice else None,
              corrected_pixels=int((d['cell'] != d['old']).sum())))


def merge(a):
    records = list(parts(a, 'filter'))
    choice = frozen(a) if a.split == 'test' else None
    if choice and any(r['selected'] != choice['scheme'] for r in records):
        raise ValueError('Test parts used a different frozen filter')
    rows = {f: rates(sum_rows(r['filters'][f] for r in records)) for f in FILTERS}
    for row in rows.values():
        row['dropped_any_truth_fraction'] = fraction(row['dropped_any_truth'], row['dropped'])
    write(a.out / a.dataset / f'filter_{a.split}.json', dict(dataset=a.dataset, split=a.split,
          limit=a.limit, partial=bool(a.limit), images=names(a), filters=rows,
          selected=choice['scheme'] if choice else None))


def select(a):
    report = read(a.out / a.dataset / 'filter_val.json')
    if report['limit'] != a.limit or report['images'] != scope_names(a.dataset, 'val', a.limit):
        raise ValueError('Validation inventory differs from requested selection scope')
    rows = report['filters']
    base = rows['F0']
    eligible, decisions = [], {}
    for f, r in rows.items():
        losses = {key: None if base[key] is None else base[key] - r[key]
                  for key in ('pixel_recall2', 'object_recall2')}
        # Empty cell-2 denominator cannot establish a loss bound; use F0.
        ok = f == 'F0' or all(v is not None and v <= .01 + 1e-12 for v in losses.values())
        decisions[f] = dict(loss=losses, eligible=ok, candidates=r['candidates'])
        if ok:
            eligible.append(f)
    selected = min(eligible, key=lambda f: (rows[f]['candidates'], FILTERS.index(f)))
    result = dict(dataset=a.dataset, limit=a.limit, partial=bool(a.limit), scheme=selected,
                  validation_images=report['images'], decisions=decisions,
                  rule='②像素和GT对象召回各损失≤0.01；候选总数最少；同分F0/F1/F2/F3',
                  parameters=dict(F1_novel_min=100, F2_opening=[3, 3], F2_novel_min=100, F3_radius_min=2))
    path = a.out / a.dataset / 'frozen_filter.json'
    if path.exists() and read(path) != result:
        raise ValueError('Frozen selection differs; use a new output directory')
    write(path, result)
    print(f'{a.dataset}: frozen {selected}', flush=True)


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run', 'merge', 'select')))
    start = time.monotonic()
    {'run': run, 'merge': merge, 'select': select}[a.stage](a)
    timed(a, 'filter-' + a.stage, start)
