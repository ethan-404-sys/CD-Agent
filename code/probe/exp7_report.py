"""Chinese numeric report and validation-selected example panels; CPU exp5-env."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import numpy as np

from eval_metrics_report import fmt, table, overlay, zoom_box
from exp7_common import CONSERVATIVE, read_json, write_json
from exp7_eval import config_id

P0 = 'none_P0_t2_none'


def full_tables(summary):
    rows = summary['rows']
    lines = []
    for keys in (('pixel_precision', 'pixel_recall', 'pixel_f1', 'pixel_iou'),
                 tuple(f'bf{d}_{k}' for d in (1, 2, 4, 5) for k in ('precision', 'recall', 'f1')) + ('biou_diag', 'biou_3'),
                 ('object_precision', 'object_recall', 'object_f1', 'object_recall_lt100',
                  'object_recall_100_to_1000', 'object_recall_1000_to_5000', 'object_recall_gt5000',
                  'split_rate', 'merge_rate', 'merge_rate_50')):
        lines += table(['配置', *keys], [[key, *[fmt(row['micro'][k], True) for k in keys]] for key, row in rows.items()])
    lines += table(['配置', '预测粗糙度', '真值粗糙度', '连通域/图', '<20碎片/图', '新增误检/图', '新增误检P90',
                    '有害编辑率 %', '推理秒/图'],
                   [[key, fmt(r['micro']['pred_roughness']), fmt(r['micro']['gt_roughness']),
                     *[fmt(r['distributions'][k]['mean']) for k in ('pred_fragments', 'pred_fragments_lt20', 'new_false_objects')],
                     fmt(r['distributions']['new_false_objects']['p90']), fmt(r['harmful_edit_rate'], True),
                     fmt(r['inference_seconds']['mean'])] for key, r in rows.items()])
    for delta in ('delta_f1', 'delta_biou_diag', 'delta_biou_3'):
        lines += table(['配置', delta, '提升 %', '下降 %', '不变 %', 'P10', 'P50', 'P90', '编辑图下降>0.5pp %', '最差五图'],
                       [[key, '', *[fmt(r[delta][q], True) for q in ('improve_fraction', 'decline_fraction', 'unchanged_fraction',
                                                                  'p10', 'p50', 'p90', 'harmful_edit_rate')],
                         '; '.join(f"{x['image']}: {fmt(x['delta'], True)}" for x in r[delta]['worst5'])]
                        for key, r in rows.items()])
    for key, row in rows.items():
        lines += [f'#### {key} 逐图分布', '']
        lines += table(['指标', '有效图数', '均值', 'P10', 'P50', 'P90', '最小', '最大'],
                       [[k, d['n'], *[fmt(d[q]) for q in ('mean', 'p10', 'p50', 'p90', 'min', 'max')]]
                        for k, d in row['distributions'].items()])
    return lines


def draw(directory, summary, selection, destination):
    selected = selection['top3'][:3]
    keys = [P0] + [config_id(c) for c in selected]
    metadata = []
    for name in summary['examples']:
        with np.load(directory / (Path(name).stem + '.npz'), allow_pickle=False) as z:
            gt, before, rgb = z['gt'], z['before'], z['t2']
            masks = {k: z['mask_' + k] for k in keys}
            morph = z['mask_none_morph_t2_none']
        box, score = zoom_box(before, masks[P0], morph)
        x0, y0, x1, y1 = box
        fig, axes = plt.subplots(2, 4, figsize=(15, 8))
        for j in range(4):
            for row in range(2):
                axes[row, j].axis('off')
            if j >= len(keys):
                axes[0, j].text(.1, .5, 'No eligible validation scheme')
                continue
            key = keys[j]
            axes[0, j].imshow(masks[key][y0:y1, x0:x1], cmap='gray', vmin=0, vmax=1, interpolation='nearest')
            axes[1, j].imshow(overlay(rgb, masks[key], gt)[y0:y1, x0:x1], interpolation='nearest')
            axes[0, j].set_title(key, fontsize=8)
        fig.suptitle(name + ' | Prediction: cyan; GT: yellow; overlap: white')
        fig.tight_layout()
        filename = f"{summary['setting']}_{Path(name).stem}.png"
        fig.savefig(destination / filename, dpi=160)
        plt.close(fig)
        metadata.append(dict(image=name, zoom_xyxy=box, proxy_pixels=score, configurations=keys, figure=filename))
    write_json(destination / f"selection_{summary['setting']}.json", metadata)
    return metadata


def questions(evaluations, validations, unit_summaries):
    lines = ['## 五个判断问题', '']
    for setting, title in (('O', '1. O设定：F1容差与边界'), ('V', '2. V设定：误检与像素F1')):
        lines += ['### ' + title, '']
        available = [(s, selection) for _, s, selection in evaluations if s['setting'] == setting]
        if not available:
            lines += ['未做。', '']
            continue
        for summary, selection in available:
            p0 = summary['rows'][P0]
            lines += [f"{summary['dataset']}：{len(summary['images'])}张；P0 F1={fmt(p0['micro']['pixel_f1'], True)}%，"
                      f"BF2={fmt(p0['micro']['bf2_f1'], True)}%，粗糙度={fmt(p0['micro']['pred_roughness'])}。", '']
            lines += table(['配置', '相对P0 ΔF1(pp)', '相对P0 ΔBF2(pp)', '粗糙度', '新增误检/图', '相对P0 FP像素变化', '验证F1容差通过', '验证BF2/粗糙度同时改善'],
                           [[key, fmt(r['micro']['pixel_f1']-p0['micro']['pixel_f1'], True),
                             fmt(r['micro']['bf2_f1']-p0['micro']['bf2_f1'], True), fmt(r['micro']['pred_roughness']),
                             fmt(r['distributions']['new_false_objects']['mean']), r['counts']['fp']-p0['counts']['fp'],
                             str(selection['validation_flags'].get(key, {}).get('f1_eligible', '对照')),
                             str(all(selection['validation_flags'][key][k] for k in ('boundary_improved', 'roughness_improved')))
                             if key in selection['validation_flags'] else '对照']
                            for key, r in summary['rows'].items()])
    lines += ['### 3. 面积、变化类型与影像期', '']
    if not evaluations:
        lines += ['未做。', '']
    for _, summary, _ in evaluations:
        for key, stats in summary.get('sam', {}).items():
            lines += [f"{summary['dataset']} / {summary['setting']} / {key}", '']
            for group in ('bucket', 'kind'):
                lines += table(['分组', '区域数', 'SAM IoU均值 %', 'P10 %', 'P50 %', 'P90 %'],
                               [[label, d['n'], *[fmt(d[k], True) for k in ('mean', 'p10', 'p50', 'p90')]]
                                for label, d in stats[group].items()])
        if not any('/t1' in k or '/best' in k for k in summary.get('sam', {})):
            lines += ['t1/t2及取高分比较：未做。', '']
    lines += ['按类型或面积切换精修规则：未做。', '', '### 4. 跨数据集', '']
    datasets = {s['dataset'] for _, s, _ in evaluations}
    lines += [f"{d}：{'已报告上表数值' if d in datasets else '未做'}。" for d in ('second', 'sysu', 'levir', 'whu')]
    lines += ['', '### 5. 7b判断单元', '']
    if not unit_summaries:
        lines += ['未做。', '']
    for summary in unit_summaries:
        lines += [f"{summary['dataset']}，{len(summary['images'])}张。", '']
        lines += table(['单元', 'F1 %', 'BF2 %', 'BIoU %', '粗糙度', '目标F1 %', '单元数/图', '面积中位数',
                        '无信号目标覆盖 %', '无信号像素覆盖 %', '秒/图'],
                       [[key, *[fmt(r['micro'][k], True) for k in ('pixel_f1', 'bf2_f1', 'biou_diag')],
                         fmt(r['micro']['pred_roughness']), fmt(r['micro']['object_f1'], True),
                         fmt(r['unit_count']['mean']), fmt(r['unit_area']['p50']),
                         fmt(r['no_signal']['object_coverage'], True), fmt(r['no_signal']['pixel_coverage'], True),
                         fmt(r['inference_seconds']['mean'])] for key, r in summary['rows'].items()])
    lines += ['V1重训：未做。', '']
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluations', nargs='*', type=Path, default=[])
    parser.add_argument('--validations', nargs='*', type=Path, default=[])
    parser.add_argument('--units', nargs='*', type=Path, default=[])
    parser.add_argument('--doc-root', type=Path, required=True)
    args = parser.parse_args()
    evaluations = [(p, read_json(p / 'summary.json'), read_json(p / 'selection_used.json')) for p in args.evaluations]
    validations = [read_json(p / 'summary.json') for p in args.validations]
    unit_summaries = [read_json(p / 'summary.json') for p in args.units]
    destination = args.doc_root / 'results'
    figures = args.doc_root / 'figures/exp7'
    destination.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    q = questions(evaluations, validations, unit_summaries)
    lines = ['# 实验7：确认后的掩码精修', '',
             '比例表以百分数显示，Δ以百分点显示；逐图分布附表使用原始0–1值。'
             'F1和IoU在同一micro汇总下单调等价。推理计时包含确认读取、概率与影像读取、GPU编码和提示、CPU合并；'
             '排除模型初始化、原始SAM缓存读取、GT读取、指标和作图。高分期计入两期推理。', '', *q]
    for p, summary, selection in evaluations:
        lines += [f"## {summary['dataset']} / {summary['setting']} 测试指标 A–E", '', *full_tables(summary)]
        if summary['dataset'] == 'second':
            panels = draw(p, summary, selection, figures)
            lines += [f"![{r['image']}](../figures/exp7/{r['figure']})" for r in panels]
            lines += ['', '最好三种方案由验证集冻结；不足三种时留空。两张补充示例按文件名取首两张，未按测试结果选图。', '']
    for summary in validations:
        lines += [f"## 验证集 {summary['setting']} 全部配置", '', summary['guided_filter'], '', *full_tables(summary)]
        # Includes t1/t2 type tables before a final date choice, when those caches were evaluated.
        for key, stats in summary.get('sam', {}).items():
            lines += table([key + ' 验证变化类型', '区域数', 'SAM IoU均值 %'],
                           [[label, d['n'], fmt(d['mean'], True)] for label, d in stats['kind'].items()])
    for summary in unit_summaries:
        lines += [f"## 7b {summary['dataset']} A–E", '', *full_tables(summary), *summary['rules'], '']
    lines += ['## 保守处理与口径', '', *['- ' + text for text in CONSERVATIVE], '',
              '拆分/粘连默认以≥1像素重叠计数；另报每个真值覆盖≥50%的粘连。'
              '有害编辑率分母仅含实际编辑图，阈值为ΔF1严格小于−0.5个百分点。'
              'Boundary IoU也报告同样的逐图变化统计。', '']
    (destination / 'exp7_prompt_refinement.md').write_text('\n'.join(lines), encoding='utf-8')
    summary_path = destination / 'summary.md'
    previous = summary_path.read_text() if summary_path.exists() else '# 实验汇总\n'
    marker = '## 实验 7\n'
    if marker in previous:
        raise FileExistsError('summary.md already contains experiment 7; refusing to append duplicate section')
    summary_path.write_text(previous.rstrip() + '\n\n' + marker + '\n' + '\n'.join(q), encoding='utf-8')


if __name__ == '__main__':
    main()
