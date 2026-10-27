"""四格、距离与 1d 分流；run 可按图分片，merge 汇总。"""
from exp8s8_common import *
import time


def stats(d):
    best = np.full(len(d['rows'])+1, -1, int)
    best_px = np.zeros(len(best), int)
    union = np.zeros_like(d['gt'])
    for ident, (obj, fill) in enumerate(zip(d['objects'], d['routes'])):
        x0, y0, x1, y1 = obj['bbox']
        sizes = np.bincount(d['labels'][y0:y1, x0:x1][obj['mask']], minlength=len(best))
        take = sizes > best_px
        best[take], best_px[take] = ident, sizes[take]
        if fill:
            s5.add_mask(union, obj)
    result = dict(images=1, units=len(d['objects']), fill_units=sum(d['routes']),
                  refine_units=len(d['objects'])-sum(d['routes']), fn_pixels=int((d['gt'] & ~d['base']).sum()),
                  fn_components=len(d['rows']), fn_objects=len({r['target'] for r in d['rows']}),
                  gt_objects=len(d['ga'])-1)
    groups = {str(g): [r for r in d['rows'] if r['cell'] == g] for g in CELLS}
    groups['3b'] = [r for r in groups['3'] if r['detector_coverage'] >= .3]
    groups['2far'] = [r for r in groups['2'] if r['distance'] is None or r['distance'] > 5]
    for group, rows in groups.items():
        result[f'{group}_pixels'] = sum(r['area'] for r in rows)
        result[f'{group}_components'] = len(rows)
        result[f'{group}_objects'] = len({r['target'] for r in rows})
        ids = [r['id'] for r in rows]
        result[f'{group}_covered_pixels'] = int(np.isin(d['labels'][union], ids).sum())
        for route in ('fill', 'refine', 'none'):
            chosen = [r for r in rows if ('none' if best[r['id']] < 0 else
                      'fill' if d['routes'][best[r['id']]] else 'refine') == route]
            result[f'{group}_{route}_pixels'] = sum(r['area'] for r in chosen)
            result[f'{group}_{route}_components'] = len(chosen)
    return result


def summarize(rows):
    r = s5.sum_rows(rows)
    r['groups'] = {}
    for g in ('1', '2', '3', '5', '3b', '2far'):
        r['groups'][g] = dict(pixel_share=fraction(r[f'{g}_pixels'], r['fn_pixels']),
            component_share=fraction(r[f'{g}_components'], r['fn_components']),
            object_share=fraction(r[f'{g}_objects'], r['fn_objects']),
            covered_pixel_fraction=fraction(r[f'{g}_covered_pixels'], r[f'{g}_pixels']),
            routed_fill_pixel_fraction=fraction(r[f'{g}_fill_pixels'], r[f'{g}_pixels']),
            routed_fill_component_fraction=fraction(r[f'{g}_fill_components'], r[f'{g}_components']))
    r['coverage12'] = fraction(r['1_covered_pixels']+r['2_covered_pixels'], r['1_pixels']+r['2_pixels'])
    r['fill_per_image'], r['refine_per_image'] = r['fill_units']/r['images'], r['refine_units']/r['images']
    return r


def run(a):
    for name in names(a, True):
        d = load(a.dataset, a.split, name)
        write(part_path(a, 'cells_parts', name), envelope(a, image=name, counts=stats(d)))
        print(f'cells {a.dataset}/{a.split}/{name}', flush=True)


def merge(a):
    rows = read_parts(a, 'cells_parts')
    write(a.out / a.dataset / f'cells_{a.split}.json', envelope(a, **summarize([r['counts'] for r in rows])))


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run', 'merge', 'all')))
    if a.stage == 'all' and a.shards != 1:
        raise ValueError('all 不接受多分片')
    started = time.monotonic()
    if a.stage in ('run', 'all'): run(a)
    if a.stage in ('merge', 'all'): merge(a)
    finish(a, 'cells-'+a.stage, started)
