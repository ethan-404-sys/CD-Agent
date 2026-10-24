"""第 2 步按裁开规则补报；评分、标签、分组、自助法和阈值逻辑复用 batch40。"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

from exp8s2_cache import HERE, EIGHT, np, ndimage, judge, local_output, split_parts

ROUTE_DESCRIPTION = ('N = m & ~D; D+ is dilated on the whole image by one pixel (8-neighborhood). '
                     'Isolated components >=20 px contribute detached_px. '
                     'Fill if N is nonempty and detached_px >= 0.5 * |N|; otherwise remove all isolated '
                     'components including <20 px, then fill if remainder is empty or remainder_r <0.1.')
original_geometry = judge.unit_geometry
original_assemble = judge.assemble
original_write_json = judge.write_json
original_write_tables = judge.write_tables


def unit_geometry(objects, labels, gt, base, cell):
    result = original_geometry(objects, labels, gt, base, cell)
    result['old_fill'] = result['fill'].copy()
    result['fill'] = result['fill'].copy()
    result['remaining_r'] = np.full(len(objects), np.nan)
    for key in ('detached_px', 'novel_px', 'dropped_px'):
        result[key] = np.zeros(len(objects), np.int64)
    dilated = ndimage.binary_dilation(base, EIGHT)
    for i, obj in enumerate(objects):
        remaining, _, audit = split_parts(obj, base, dilated)
        for key in ('detached_px', 'novel_px', 'dropped_px'):
            result[key][i] = audit[key]
        if remaining is None:
            result['fill'][i] = True
            continue
        x0, y0, x1, y1 = remaining['bbox']
        r = (remaining['mask'] & base[y0:y1, x0:x1]).sum() / remaining['area']
        result['remaining_r'][i] = r
        majority = audit['novel_px'] > 0 and 2*audit['detached_px'] >= audit['novel_px']
        result['fill'][i] = majority or r < .1
    return result


def change_counts(labels, old, new):
    return {name: dict(units=int((labels == value).sum()),
                       refine_to_fill=int(((labels == value) & ~old & new).sum()),
                       fill_to_refine=int(((labels == value) & old & ~new).sum()))
            for name, value in (('positive', 1), ('negative', 0), ('ignored', -1))}


def run_merge(args):
    changes = {}

    def assemble(parts, pop):
        data, scores, hist, unavailable = original_assemble(parts, pop)
        old = np.concatenate([p[pop + '__old_fill'] for p in parts])
        ds = str(parts[0]['dataset'])
        changes[(ds, pop)] = change_counts(data['label'], old, data['fill'])
        return data, scores, hist, unavailable

    def write_summary(path, summary):
        summary['route'] = ROUTE_DESCRIPTION
        summary['geometry_fields'] = 'r/k/cell preserve original unit geometry; remaining_r is NaN in NPZ for empty remainder'
        for ds, row in summary['datasets'].items():
            for pop, result in row['populations'].items():
                result['route_changes'] = changes[(ds, pop)]
        original_write_json(path, summary)

    def write_tables(path, summary):
        dest = path
        original_write_tables(dest, summary)
        lines = ['', '## 修正分流与去向变化', '',
                 'D 在整图上膨胀 1 像素。单元新增像素中，不贴 D⁺ 且 ≥20 像素的独立块合计达到 50% 时去补漏；'
                 '否则去掉所有不贴 D⁺ 的块（包括小于 20 的块），在剩余掩码上重算 r。'
                 '剩余为空或 r<0.1 去补漏，其余去精修。新增像素为空时不触发 50% 条件。', '',
                 '单元保持一个分数，原标签、格、原始 r/k、分数和元数据不变；'
                 '第 4 项在修正后的补漏总体上沿用原阈值选择流程，验证选阈值、测试评一次。', '',
                 '| 数据集/部分 | 总体 | 标签 | 单元数 | 精修→补漏 | 补漏→精修 |', '|---|---|---|---:|---:|---:|']
        for ds, row in summary['datasets'].items():
            for pop, result in row['populations'].items():
                for label, counts in result['route_changes'].items():
                    lines.append(f'| {ds} | {pop} | {dict(positive="正例", negative="负例", ignored="忽略标签")[label]} | '
                                 f'{counts["units"]} | {counts["refine_to_fill"]} | {counts["fill_to_refine"]} |')
        dest.write_text(dest.read_text() + '\n'.join(lines) + '\n')

    # 保留原统计函数，只改最终文件名 suffix；独立命名空间接入附加审计和表格。
    path = Path(judge.__file__)
    function = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'run_merge')
    suffix = [n for n in function.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'suffix' for t in n.targets)]
    if len(suffix) != 1:
        raise ValueError('batch40 run_merge suffix assignment changed')
    suffix[0].value = ast.parse("f'_split_{args.population}'", mode='eval').body
    namespace = dict(vars(judge), assemble=assemble, write_json=write_summary, write_tables=write_tables)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(path), 'exec'), namespace)
    namespace['run_merge'](args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('units', 'merge'))
    parser.add_argument('--dataset', choices=('all',) + judge.DATASETS, default='all')
    parser.add_argument('--population', choices=('all', 'u5', 'p6b', 'v2'), default='all')
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--out', type=Path, default=HERE / 'run')
    parser.add_argument('--seed', type=int, choices=(judge.SEED,), default=judge.SEED)
    parser.add_argument('--bootstrap', type=int, default=judge.BOOTSTRAPS)
    args = parser.parse_args()
    args.out = local_output(args.out)
    if args.limit < 0 or args.shards < 1 or not 0 <= args.shard < args.shards or args.bootstrap < 1:
        parser.error('invalid limit/shard/bootstrap')
    if args.bootstrap != judge.BOOTSTRAPS and not 0 < args.limit <= 2:
        parser.error('正式统计固定 2000 次自助法；缩减只允许 --limit 1/2')
    if args.stage == 'merge' and (args.shard != 0 or args.shards != 1):
        parser.error('merge 读取全部逐图分片，不接受分片参数')
    if args.population in ('p6b', 'v2') and args.dataset.startswith('sysu'):
        parser.error('P2/P3 只适用于 SECOND')
    judge.HERE, judge.VERSION, judge.unit_geometry = HERE, 2, unit_geometry
    if args.stage == 'units':
        for ds in judge.selected_datasets(args):
            judge.run_units(args, ds)
    else:
        run_merge(args)


if __name__ == '__main__':
    main()
