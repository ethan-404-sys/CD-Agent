"""实验 8 补充第 6 步第 5 项：漏检按“有无信号 × 是否贴边”分四格（不依赖评分器，只读 batch30 数据底座）。

定义（与 batch30 的漏检对象一致）：
  真值目标    objects.gt_label（SECOND 按两期语义转移拆，其余按二值 GT 连通域）。
  有无信号    检测器 0.5 掩码覆盖目标像素 < 10% 为无信号（exp4 定义），否则为有信号。
  漏检连通域  目标内 GT & ~pred 的 8 连通域，≥ 20 像素（exp4 MIN_TARGET）；与 batch30 missed 逐个核对。
  贴边        连通域外扩一像素（3×3）后与检测器掩码相交，否则为孤立。
  四格        ① 无信号·贴边 ② 无信号·孤立 ③ 有信号·贴边 ④ 有信号·孤立；
              不足 20 像素的连通域或面积 < 20 的目标记为 ⑤ 残余（只计像素）。
每图写出像素级格标签（0 非漏检，1–5 同上），供第 5、6 步按格统计漏检率。
四源候选的 oracle 覆盖：默认档位四源 D1 去重后全部候选；R_obj@0.5 按单候选新增像素与单连通域的 IoU，
R_pix 为候选并集覆盖该格漏检像素的比例。

阶段：cells（按图分片）→ merge（汇总写 summary.json 与 report.md）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, '/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch30')
from exp8s_common import (np, rt, gt_label, DEFAULTS, SOURCES, read_json, write_json, names,  # noqa: E402
                         level_key, load_objects, combined, crop, put, overlap)

OUT = Path('/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe')
BASE = {'second_cv': OUT / 'exp8s_batch30', 'second': OUT / 'exp8s_batch30',
        'levir': OUT / 'exp8s_batch30', 'whu': OUT / 'exp8s_batch30',
        'sysu_val': OUT / 'exp8s_batch30_sysu', 'sysu': OUT / 'exp8s_batch30_sysu'}
CELLS = (1, 2, 3, 4)
BINS = ((.1, .2), (.2, .3), (.3, .5), (.5, .8), (.8, 1.01))
BIN_NAMES = ('10–20%', '20–30%', '30–50%', '50–80%', '≥80%')


def image_list(ds):
    images = read_json(BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')['config']['images']
    if images != names(ds):
        raise ValueError(f'{ds}: data base image list differs from the split')
    return images


def pixels(path):
    with np.load(path, allow_pickle=False) as z:
        shape = tuple(int(v) for v in z['shape'])
        gt, base = (np.unpackbits(z[k], count=int(np.prod(shape))).reshape(shape).astype(bool)
                    for k in ('gt', 'base'))
        return gt, base


def touches(mask, base):
    from scipy import ndimage
    return bool((ndimage.binary_dilation(mask, structure=np.ones((3, 3), bool)) & base).any())


def classify(ds, name, gt, base):
    """返回格标签图、连通域列表（crop 对象 + cell）和有信号目标的覆盖率分档行。"""
    gl, count, _ = gt_label(ds, name, gt)
    cell = np.zeros(gt.shape, np.uint8)
    comps, coverage_rows = [], []
    for ident in range(1, count + 1):
        original = gl == ident
        area = int(original.sum())
        covered = float(base[original].mean())
        signal = covered >= .1
        missed_px = 0
        for mask, _ in rt.components(original & ~base, base):
            size = int(mask.sum())
            if area < rt.e4.MIN_TARGET or size < rt.e4.MIN_TARGET:
                cell[mask] = 5
                continue
            c = (3 if signal else 1) + (0 if touches(mask, base) else 1)
            cell[mask] = c
            missed_px += size
            comps.append(dict(crop(mask, parent=ident), cell=c, no_signal_object=not signal))
        if signal and missed_px:
            coverage_rows.append((covered, missed_px))
    if (cell > 0).sum() != (gt & ~base).sum():
        raise ValueError(f'{ds}/{name}: cell map does not cover all FN pixels')
    return cell, comps, coverage_rows


def run_cells(args, ds):
    dest = args.out / ds / f'cells_{args.shard:03d}.jsonl'
    maps = args.out / 'interim' / ds
    maps.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    items = [(i, n) for i, n in enumerate(image_list(ds)[:args.limit or None]) if i % args.shards == args.shard]
    with dest.with_suffix('.tmp').open('w') as fh:
        for index, name in items:
            path = BASE[ds] / 'interim/exp8s' / ds / Path(name).stem
            gt, base = pixels(path / 'pixels.npz')
            cell, comps, coverage_rows = classify(ds, name, gt, base)
            missed, _ = load_objects(path / 'missed')
            if [(o['parent'], o['area'], tuple(o['bbox'])) for o in missed] != \
                    [(o['parent'], o['area'], tuple(o['bbox'])) for o in comps]:
                raise ValueError(f'{ds}/{name}: components differ from batch30 missed objects')
            by_source = {}
            for s in SOURCES:
                objs, _ = load_objects(path / level_key(s, DEFAULTS[s]))
                by_source[s] = objs
            cands = combined(by_source, SOURCES)
            union = np.zeros(gt.shape, bool)
            for o in cands:
                put(union, o)
            row = dict(index=index, image=name, pixels=int(gt.size), gt=int(gt.sum()),
                       cells={}, coverage=[[float(c), int(p)] for c, p in coverage_rows])
            for c in (1, 2, 3, 4, 5):
                in_cell = cell == c
                members = [o for o in comps if o['cell'] == c]
                hits = 0
                for t in members:
                    best = 0.
                    for o in cands:
                        inter = overlap(o, t)
                        if inter:
                            best = max(best, inter / (o['area'] + t['area'] - inter))
                    hits += best >= .5
                row['cells'][str(c)] = dict(components=len(members),
                                            objects=len({o['parent'] for o in members}),
                                            pixels=int(in_cell.sum()), covered=int((in_cell & union).sum()),
                                            hit05=int(hits))
            np.savez_compressed(maps / (Path(name).stem + '.npz'), cell=cell)
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    dest.with_suffix('.tmp').replace(dest)


def run_merge(args, ds):
    rows = []
    for f in sorted((args.out / ds).glob('cells_*.jsonl')):
        rows += [json.loads(line) for line in f.read_text().splitlines()]
    rows.sort(key=lambda r: r['index'])
    expected = len(image_list(ds)[:args.limit or None])
    if len(rows) != expected or len({r['index'] for r in rows}) != expected:
        raise ValueError(f'{ds}: {len(rows)} rows, expected {expected}')
    gt = sum(r['gt'] for r in rows)
    cells = {}
    for c in ('1', '2', '3', '4', '5'):
        tot = {k: sum(r['cells'][c][k] for r in rows) for k in ('components', 'objects', 'pixels', 'covered', 'hit05')}
        cells[c] = tot
    fn = sum(v['pixels'] for v in cells.values())
    comps = sum(cells[c]['components'] for c in '1234')
    for c, v in cells.items():
        v['pixel_share'] = v['pixels'] / fn
        v['component_share'] = v['components'] / comps if c != '5' else None
        v['miss_rate'] = v['pixels'] / gt
        v['R_pix'] = v['covered'] / v['pixels'] if v['pixels'] else None
        v['R_obj_05'] = v['hit05'] / v['components'] if v['components'] else None
    groups = {}
    for key, members in (('12', '12'), ('34', '34'), ('all', '12345')):
        px = sum(cells[c]['pixels'] for c in members)
        groups[key] = dict(pixels=px, miss_rate=px / gt, pixel_share=px / fn,
                           components=sum(cells[c]['components'] for c in members if c != '5'),
                           R_pix=sum(cells[c]['covered'] for c in members) / px,
                           R_obj_05=(sum(cells[c]['hit05'] for c in members if c != '5') /
                                     max(1, sum(cells[c]['components'] for c in members if c != '5'))))
    bins = []
    cov = [c for r in rows for c in r['coverage']]
    for (lo, hi), label in zip(BINS, BIN_NAMES):
        sel = [p for c, p in cov if lo <= c < hi]
        bins.append(dict(bin=label, objects=len(sel), missed_pixels=int(sum(sel)),
                         pixel_share_of_34=sum(sel) / groups['34']['pixels'] if groups['34']['pixels'] else None))
    summary = dict(dataset=ds, images=len(rows), gt_pixels=gt, fn_pixels=fn, cells=cells, groups=groups,
                   coverage_bins=bins, objects_with_signal_and_miss=len(cov))
    write_json(args.out / ds / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False)[:2000])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('cells', 'merge'))
    p.add_argument('--dataset', required=True, choices=tuple(BASE))
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    (run_cells if args.stage == 'cells' else run_merge)(args, args.dataset)


if __name__ == '__main__':
    main()
