"""Audit cached cell-2 labels, distance distributions, and train_2697."""
from exp8s5_common import *
from PIL import Image, ImageDraw


def counts(d, field):
    result = {f'cell{g}_pixels': int((field == g).sum()) for g in CELLS}
    for g in (1, 2):
        ids = np.unique(d['gl'][field == g])
        result[f'cell{g}_objects'] = int((ids > 0).sum())
    return result


def audit(d):
    old, base, gl = d['old'], d['base'], d['gl']
    before2 = (old == 2)
    old_ids = np.unique(gl[before2 & (gl > 0)])
    fixed_ids = np.unique(gl[d['cell'] == 2])
    result = dict(images=1, gt_pixels=int(d['gt'].sum()), fn_pixels=int((d['gt'] & ~base).sum()),
                  changed_pixels=int((old != d['cell']).sum()),
                  cached_cell2_overlap_pixels=int((before2 & base).sum()),
                  cached_cell2_overlapping_objects=int((d['gb'][old_ids] > 0).sum()),
                  corrected_cell2_overlapping_objects=int((d['gb'][fixed_ids] > 0).sum()),
                  cached_cell2_outside_gt=int((before2 & ~d['gt']).sum()))
    for prefix, field in (('before', old), ('after', d['cell']), ('radius3', d['relaxed'])):
        result.update({prefix + '_' + k: v for k, v in counts(d, field).items()})
    for prefix, key in (('after', 'cell'), ('radius3', 'relaxed_cell')):
        for g in (1, 2):
            result[f'{prefix}_cell{g}_components'] = sum(r[key] == g for r in d['rows'])
    for title, predicate in (('2', lambda x: x == 2), ('3', lambda x: x == 3),
                             ('4_5', lambda x: x in (4, 5)),
                             ('gt5', lambda x: x is not None and x > 5),
                             ('infinite', lambda x: x is None)):
        rows = [r for r in d['rows'] if r['cell'] == 2 and predicate(r['distance'])]
        result[f'distance_{title}_components'] = len(rows)
        result[f'distance_{title}_pixels'] = sum(r['area'] for r in rows)
        result[f'distance_{title}_objects'] = len({r['target'] for r in rows})
    result['cell2_components'] = sum(r['cell'] == 2 for r in d['rows'])
    result['cell2_objects'] = len(fixed_ids)
    return result


def run(a):
    for name in names(a, True):
        d = geometry(a.dataset, a.split, name)
        row = audit(d)
        anomalies = [r for r in d['rows'] if r['old_cells'] != {str(r['cell']): r['area']}]
        write(part_path(a, 'check', name), envelope(a, name, counts=row,
              anomalies=anomalies, targets=[r for r in d['rows'] if r['cell'] in (1, 2)]))


def merge(a):
    rows = list(parts(a, 'check'))
    total = sum_rows(r['counts'] for r in rows)
    ratios = {}
    for prefix in ('before', 'after', 'radius3'):
        for g in CELLS:
            n = total[f'{prefix}_cell{g}_pixels']
            ratios[f'{prefix}_cell{g}_fn_fraction'] = fraction(n, total['fn_pixels'])
            ratios[f'{prefix}_cell{g}_gt_fraction'] = fraction(n, total['gt_pixels'])
    for title in ('2', '3', '4_5', 'gt5', 'infinite'):
        ratios[f'distance_{title}_pixel_fraction'] = fraction(total[f'distance_{title}_pixels'], total['after_cell2_pixels'])
        ratios[f'distance_{title}_component_fraction'] = fraction(total[f'distance_{title}_components'], total['cell2_components'])
    write(a.out / a.dataset / f'check_{a.split}.json', dict(dataset=a.dataset, split=a.split,
          limit=a.limit, partial=bool(a.limit), images=names(a), counts=total, ratios=ratios,
          changed_images=[r['image'] for r in rows if r['counts']['changed_pixels']],
          repair='按GT语义目标及漏检连通域重建①②③⑤；只修正本次统计，保留原缓存'))


def boundary(mask):
    return mask & ~ndi.binary_erosion(mask, structure=EIGHT, border_value=0)


def inspect(a):
    ds, split, name = 'second', 'train', 'train_2697.png'
    if name not in manifest(ds)['images'][split]:
        raise ValueError('train_2697 must belong to SECOND training')
    d = geometry(ds, split, name)
    gt, base, gl = d['gt'], d['base'], d['gl']
    h, w = gt.shape
    roi = np.zeros(gt.shape, bool)
    roi[:h // 2, w // 2:] = True
    ids = set(np.unique(d['labels'][roi & (d['old'] == 2)]).tolist()) - {0}
    # Include corrected cell-2 regions too, so a labeling defect cannot hide a target.
    ids |= set(np.unique(d['labels'][roi & (d['cell'] == 2)]).tolist()) - {0}
    euclid = ndi.distance_transform_edt(~base) if base.any() else None
    chess = ndi.distance_transform_cdt(~base, metric='chessboard') if base.any() else None
    targets = []
    for r in d['rows']:
        if r['id'] not in ids:
            continue
        mask, parent = d['labels'] == r['id'], gl == r['target']
        targets.append(dict(r, distance_euclidean=float(euclid[mask].min()) if euclid is not None else None,
                            target_distance=int(chess[parent].min()) if chess is not None else None,
                            target_distance_euclidean=float(euclid[parent].min()) if euclid is not None else None,
                            overlap_pixels=int((mask & base).sum()),
                            empty_grid_layers=None if r['distance'] is None else max(0, r['distance'] - 1),
                            reason='原格标签与冻结定义不一致，按定义修正' if r['old_cells'] != {str(r['cell']): r['area']}
                            else '图内没有D' if r['distance'] is None else f'漏检域与D的棋盘距离为{r["distance"]}，一像素膨胀不相接'))
    folder = local(a.out / 'inspection')
    folder.mkdir(parents=True, exist_ok=True)
    rgb = load_rgb(c.raw_dataset(ds, split), name, 't2').copy()
    overlay = rgb.copy()
    for r in targets:
        overlay[boundary(gl == r['target'])] = (255, 255, 255)
        overlay[boundary(d['labels'] == r['id'])] = (255, 40, 170)
    overlay[boundary(base)] = (0, 200, 255)
    im = Image.fromarray(overlay)
    draw = ImageDraw.Draw(im)
    for r in targets:
        x0, y0, _, _ = r['bbox']
        draw.text((x0, y0), str(r['id']), fill='yellow', stroke_width=1, stroke_fill='black')
    im.save(folder / 'train_2697-native.png')
    box = (w // 2, 0, w, h // 2)
    # Nearest-neighbor enlargement preserves every source pixel; a grid makes
    # individual pixels visible without interpolating the alleged gap away.
    zoom = Image.fromarray(overlay).crop(box).resize(((w - w // 2) * 4, (h // 2) * 4), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(zoom, 'RGBA')
    for x in range(0, zoom.width, 4):
        draw.line((x, 0, x, zoom.height), fill=(0, 0, 0, 65))
    for y in range(0, zoom.height, 4):
        draw.line((0, y, zoom.width, y), fill=(0, 0, 0, 65))
    zoom.save(folder / 'train_2697-upper-right-4x.png')
    Image.fromarray(rgb).crop(box).save(folder / 'train_2697-t2-crop.png')
    detail = dict(dataset=ds, split=split, image=name, roi=list(box), targets=targets,
                  counts=audit(d), class_names=c.DATASETS['second']['classes'],
                  legend='白色=所属GT目标轮廓；粉色=②漏检连通域；青色=D轮廓；黄色=漏检域编号',
                  diagnosis='发现格标签差异，详见逐域记录' if (d['old'] != d['cell']).any()
                  else '本图格标签与冻结定义一致。' + '；'.join(
                      f'右上角GT目标{r["target"]}共{r["target_area"]}像素，D覆盖{r["detector_coverage"]:.2%}，'
                      f'棋盘距离{r["distance"]}、欧氏距离{r["distance_euclidean"]}像素'
                      for r in targets))
    write(folder / 'train_2697.json', detail)


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run', 'merge', 'inspect')))
    start = time.monotonic()
    {'run': run, 'merge': merge, 'inspect': inspect}[a.stage](a)
    timed(a, 'check-' + a.stage, start)
