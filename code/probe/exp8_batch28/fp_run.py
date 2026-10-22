"""batch28：历史复核、数据准备、验证冻结、单次测试与中文报告。"""
from __future__ import annotations

import argparse
import csv
import time
import uuid

import fp_common as c
import numpy as np
import torch


def metric(mask, gt, base, name):
    row = c.metrics.image_metrics(mask, gt, before=base)
    row['image'] = name
    deleted = base & ~mask
    row['removed_tp'] = int((deleted & gt).sum())
    row['removed_fp'] = int((deleted & ~gt).sum())
    return row


def oracle_selection(data):
    return (data['area'] >= 10) & (data['truth'] < .1 * data['area'])


def score_selection(data, score, threshold):
    if len(score) != len(data['area']):
        raise ValueError('分数和单元数量不符')
    return np.asarray(score, np.float64) < threshold


def reference(args):
    """先复算历史测试口径；这些结果仅用于一致性核对，不用于选方案。"""
    result = {}
    for dataset in ('second', 'sysu'):
        images = c.names(dataset, 'test', args.limit)
        rows, labels, scores = [], [], []
        for i, name in enumerate(images, 1):
            path = args.out / 'reference' / dataset / 'images' / (c.Path(name).stem + '.json')
            if not path.exists():
                started = time.monotonic()
                raw = c.inputs(dataset, 'test', name)
                base, gt, _ = raw
                c.prepare_image(args, dataset, 'test', 'slic', name, raw)
                data = c.read_image(args, dataset, 'test', 'slic', name)
                take = oracle_selection(data)
                final = c.remove(args, dataset, 'test', 'slic', name, base, take)
                c.write_json(path, dict(protocol=c.protocol(args), image=name,
                    baseline=metric(base, gt, base, name), slic_oracle=metric(final, gt, base, name),
                    deleted_units=int(take.sum()), seconds=time.monotonic() - started))
            row = c.read_json(path)
            if row['protocol'] != c.protocol(args) or row['image'] != name:
                raise ValueError(f'{path}: 历史缓存协议不符')
            rows.append(row)
            data = c.read_image(args, dataset, 'test', 'slic', name)
            keep = data['label'] >= 0
            labels.append(data['label'][keep])
            scores.append(data['meta'][keep, 0])
            if i % 50 == 0:
                print(f'reference {dataset}: {i}/{len(images)}', flush=True)
        before = c.metrics.aggregate([r['baseline'] for r in rows])
        after = c.metrics.aggregate([r['slic_oracle'] for r in rows])
        auc = c.old.binary_metrics(np.concatenate(labels), np.concatenate(scores))
        delta = 100 * (after['micro']['pixel_f1'] - before['micro']['pixel_f1'])
        removed = dict(deleted_tp_px=sum(r['slic_oracle']['removed_tp'] for r in rows),
                       deleted_fp_px=sum(r['slic_oracle']['removed_fp'] for r in rows),
                       deleted_regions=sum(r['deleted_units'] for r in rows))
        checks = {}
        historical = c.read_json(c.OUT / 'exp6e' / dataset / 'oracle.json')
        if not args.limit:
            for source, actual in [('original', before), ('oracle_piece', after)]:
                checks[source + '_counts'] = all(actual['counts'][k] == historical[source][k]
                                                for k in ('tp', 'fp', 'fn'))
            checks['removed_counts'] = all(v == historical['oracle_piece'][k] for k, v in removed.items())
            checks['reported_delta'] = abs(delta - {'second': 5.74, 'sysu': 4.07}[dataset]) <= .015
            if dataset == 'second':
                checks['reported_auc'] = auc['auc'] is not None and abs(auc['auc'] - .784) <= .0005
        result[dataset] = dict(images=len(images), baseline=before, slic_oracle=after,
                               delta_f1_pp=delta, classification=auc, removed=removed, checks=checks)
    passed = all(all(row['checks'].values()) for row in result.values())
    report = dict(protocol=c.protocol(args), partial=bool(args.limit), passed=passed,
                  historical_verified=not bool(args.limit) and passed, datasets=result)
    c.write_json(args.out / 'reference/verification.json', report)
    if not passed:
        raise ValueError('6e 上界或 SECOND AUC 未复现；见 reference/verification.json，停止后续流程')


def prepare(args):
    c.require_reference(args)
    for dataset in ('second', 'sysu', 'levir'):
        for split in (('train', 'val') if dataset in ('second', 'sysu') else ('val',)):
            images = c.names(dataset, split, args.limit)
            for i, name in enumerate(images, 1):
                raw = None
                for unit in c.UNITS:
                    root = c.cache_root(args, dataset, split, unit)
                    if not (root / 'images' / (c.Path(name).stem + '.json')).exists():
                        if raw is None:
                            raw = c.inputs(dataset, split, name)
                    c.prepare_image(args, dataset, split, unit, name, raw)
                if i % 50 == 0:
                    print(f'prepare {dataset}/{split}: {i}/{len(images)}', flush=True)
            c.fixed(args.out / 'units' / dataset / split / 'manifest.json',
                    dict(protocol=c.protocol(args), images=images))


def validation_scores(args, dataset, name, data):
    path = args.out / 'models' / dataset
    training = c.read_json(path / 'training.json')
    if (training['protocol'] != c.protocol(args)
            or training['val_images'] != c.names(dataset, 'val', args.limit)
            or training['train_images'] != c.names(dataset, 'train', args.limit)):
        raise ValueError(f'{path}: 模型划分或配置不符')
    with np.load(path / 'validation_scores' / (c.Path(name).stem + '.npz'), allow_pickle=False) as z:
        score = z['score']
        if str(z['run_id']) != training['run_id'] or str(z['image']) != name or len(score) != len(data['label']):
            raise ValueError(f'{name}: 验证分数不符')
    if not np.isfinite(score).all():
        raise ValueError(f'{name}: 验证分数非有限')
    return score


def threshold_curve(score, area, truth, baseline):
    """互斥单元的精确像素曲线；中间标签同样参与像素选择。"""
    score = np.asarray(score, np.float64)
    order = np.argsort(score, kind='stable')
    score, area, truth = score[order], area[order], truth[order]
    if len(score):
        ends = np.r_[np.flatnonzero(score[1:] != score[:-1]), len(score) - 1]
        thresholds = np.r_[0., np.nextafter(score[ends], np.inf)]
        deleted_tp = np.r_[0, np.cumsum(truth)[ends]]
        deleted_fp = np.r_[0, np.cumsum(area - truth)[ends]]
    else:
        thresholds, deleted_tp, deleted_fp = np.array([0.]), np.array([0]), np.array([0])
    tp, fp, fn = baseline['tp'] - deleted_tp, baseline['fp'] - deleted_fp, baseline['fn'] + deleted_tp
    f1 = 2 * tp / np.maximum(2 * tp + fp + fn, 1)
    iou = tp / np.maximum(tp + fp + fn, 1)
    # 并列取较低阈值，保留更多原预测。
    best = max(range(len(thresholds)), key=lambda i: (f1[i], iou[i], -thresholds[i]))
    curves = dict(threshold=thresholds, tp=tp, fp=fp, fn=fn, f1=f1, iou=iou)
    return dict(threshold=float(thresholds[best]), f1=float(f1[best]), iou=float(iou[best])), curves


def frozen_path(args):
    return args.out / ('smoke_calibration.json' if args.limit else 'frozen.json')


def load_frozen(args):
    frozen = c.read_json(frozen_path(args))
    if frozen['protocol'] != c.protocol(args):
        raise ValueError('冻结协议不符')
    return frozen


def calibrate(args):
    c.require_reference(args)
    if frozen_path(args).exists():
        load_frozen(args)
        return
    datasets = {}
    for dataset in ('second', 'sysu', 'levir'):
        images = c.names(dataset, 'val', args.limit)
        baseline = dict(tp=0, fp=0, fn=0)
        for name in images:
            base, gt, _ = c.inputs(dataset, 'val', name)
            counts = c.metrics.pixel_counts(base, gt)
            for key in baseline:
                baseline[key] += counts[key]
        configs = {'baseline': dict(threshold=None, **c.pixel(**baseline))}
        for unit in c.UNITS:
            pool = c.Pool(args, dataset, 'val', unit)
            score_sets = {'detector': pool.meta[:, 0]}
            if unit == 'object' and dataset in ('second', 'sysu'):
                score_sets['mlp'] = np.concatenate([
                    validation_scores(args, dataset, name, {'label': pool.label[sl]})
                    for name, sl in pool.slices.items()])
            for scorer, score in score_sets.items():
                key = unit + '_' + scorer
                chosen, curve = threshold_curve(score, pool.area, pool.truth, baseline)
                configs[key] = chosen
                c.old.save_npz(args.out / 'curves' / dataset / (key + '.npz'), **curve)
        primary = max(c.schemes(dataset), key=lambda k: (configs[k]['f1'], configs[k]['iou']))
        model_run_id = None
        if dataset in ('second', 'sysu'):
            model_run_id = c.read_json(args.out / 'models' / dataset / 'training.json')['run_id']
        datasets[dataset] = dict(schemes=configs, primary=primary, model_run_id=model_run_id,
            threshold_source=raw_source(dataset), val_images=images,
            train_images=c.names(dataset, 'train', args.limit) if dataset in ('second', 'sysu') else [],
            test_images=c.names(dataset, 'test'), limitation=c.LIMITATIONS[dataset])
    datasets['whu'] = dict(schemes={k: dict(v) for k, v in datasets['levir']['schemes'].items()},
        primary=datasets['levir']['primary'], model_run_id=None, threshold_source='levir_val_transfer',
        val_images=[], train_images=[], test_images=c.names('whu', 'test'), limitation=c.LIMITATIONS['whu'],
        validation_metrics_belong_to='levir_val')
    c.write_json(frozen_path(args), dict(protocol=c.protocol(args), freeze_id=str(uuid.uuid4()),
        datasets=datasets, selection_metrics=['pixel_f1', 'pixel_iou'],
        tie_break='lower threshold; then baseline, slic_detector, object_detector, object_mlp',
        test_policy='all schemes frozen together; completed images never reevaluated',
        oracle_policy='fixed truth fraction <0.1, area >=10; descriptive only'))


def raw_source(dataset):
    return dict(second='second_cv_validation_297', sysu='sysu_val_pool_val_800', levir='levir_val_64')[dataset]


def model_for(args, dataset, frozen):
    path = args.out / 'models' / dataset / 'model.pt'
    checkpoint = torch.load(path, map_location='cpu', weights_only=True)
    if (checkpoint['run_id'] != frozen['datasets'][dataset]['model_run_id']
            or checkpoint['protocol'] != c.protocol(args)):
        raise ValueError(f'{path}: 模型与冻结协议不符')
    model = c.old.classifier(c.Features.input_dim, .15)
    model.load_state_dict(checkpoint['state_dict'])
    model.eval()
    torch.set_num_threads(1)
    return model, checkpoint['mean'].numpy(), checkpoint['std'].numpy()


def infer(model, data):
    network, mean, std = model
    score = np.empty(len(data['label']), np.float32)
    with torch.inference_mode():
        for start in range(0, len(score), 2048):
            end = start + 2048
            x = (data['meta'][start:end] - mean) / std
            score[start:end] = network(torch.from_numpy(x)).squeeze(1).sigmoid().numpy()
    if not np.isfinite(score).all():
        raise ValueError('测试分数非有限')
    return score


def evaluate(args):
    c.require_reference(args)
    frozen = load_frozen(args)
    dataset, split = args.dataset, args.split
    if split == 'test':
        if args.limit:
            raise ValueError('冒烟不能运行冻结测试')
        validation_dataset = 'levir' if dataset == 'whu' else dataset
        summary = c.read_json(args.out / 'evaluation' / validation_dataset / 'val/summary.json')
        if summary['freeze_id'] != frozen['freeze_id']:
            raise ValueError('必须先完成本冻结版本的验证报告')
    elif dataset == 'whu':
        raise ValueError('WHU 无验证集')
    config = frozen['datasets'][dataset]
    images = c.names(dataset, split, args.limit)
    expected = config['test_images' if split == 'test' else 'val_images']
    if images != expected:
        raise ValueError('当前图像清单与冻结清单不符')
    model = None
    if split == 'test' and dataset in ('second', 'sysu'):
        model = model_for(args, dataset, frozen)
    dest = args.out / 'evaluation' / dataset / split
    for i, name in enumerate(images[args.shard[0]::args.shard[1]], 1):
        stem = c.Path(name).stem
        done = dest / 'images' / (stem + '.json')
        score_path = dest / 'scores' / (stem + '.npz')
        if done.exists():
            row = c.read_json(done)
            if row['freeze_id'] != frozen['freeze_id'] or row['image'] != name:
                raise ValueError(f'{done}: 已有其他冻结版本的结果')
            if not score_path.exists():
                raise FileNotFoundError(score_path)
            continue
        started = time.monotonic()
        raw = c.inputs(dataset, split, name)
        base, gt, _ = raw
        historical = None
        if split == 'test' and dataset in ('second', 'sysu'):
            historical = c.read_json(args.out / 'reference' / dataset / 'images' / (stem + '.json'))
        results = {'baseline': historical['baseline'] if historical else metric(base, gt, base, name)}
        saved, unit_stats = {}, {}
        for unit in c.UNITS:
            c.prepare_image(args, dataset, split, unit, name, raw)
            data = c.read_image(args, dataset, split, unit, name)
            stats = c.read_json(c.cache_root(args, dataset, split, unit) / 'images' / (stem + '.json'))
            unit_stats[unit] = dict(count=len(data['label']), uncovered_prediction_pixels=stats['uncovered_prediction_pixels'])
            saved[unit + '_label'] = data['label']
            score_sets = {'detector': data['meta'][:, 0]}
            if unit == 'object' and dataset in ('second', 'sysu'):
                score_sets['mlp'] = (validation_scores(args, dataset, name, data) if split == 'val'
                                      else infer(model, data))
            oracle_key = unit + '_oracle'
            if historical and unit == 'slic':
                results[oracle_key] = historical[oracle_key]
            else:
                final = c.remove(args, dataset, split, unit, name, base, oracle_selection(data))
                results[oracle_key] = metric(final, gt, base, name)
            for scorer, score in score_sets.items():
                key = unit + '_' + scorer
                saved[key] = score
                final = c.remove(args, dataset, split, unit, name, base,
                                 score_selection(data, score, config['schemes'][key]['threshold']))
                results[key] = metric(final, gt, base, name)
        c.old.save_npz(score_path, **saved, image=name, freeze_id=frozen['freeze_id'])
        c.write_json(done, dict(image=name, freeze_id=frozen['freeze_id'], units=unit_stats,
                               metrics=results, seconds=time.monotonic() - started))
        if i % 25 == 0:
            print(f'evaluate {dataset}/{split}: {i}', flush=True)


def report(args):
    frozen = load_frozen(args)
    dataset, split = args.dataset, args.split
    if split == 'test' and args.limit:
        raise ValueError('冒烟不生成测试报告')
    images = c.names(dataset, split, args.limit)
    dest = args.out / 'evaluation' / dataset / split
    rows = [c.read_json(dest / 'images' / (c.Path(n).stem + '.json')) for n in images]
    if any(r['freeze_id'] != frozen['freeze_id'] or r['image'] != n for r, n in zip(rows, images)):
        raise ValueError('逐图结果不属于当前冻结版本')
    config = frozen['datasets'][dataset]
    keys = ['baseline', 'slic_oracle', 'slic_detector', 'object_oracle', 'object_detector']
    if dataset in ('second', 'sysu'):
        keys.append('object_mlp')
    summaries = {}
    classification = {k: ([], []) for k in c.schemes(dataset) if k != 'baseline'}
    for name in images:
        with np.load(dest / 'scores' / (c.Path(name).stem + '.npz'), allow_pickle=False) as z:
            if str(z['freeze_id']) != frozen['freeze_id'] or str(z['image']) != name:
                raise ValueError('分数文件不属于当前图像或冻结版本')
            for key, (ys, ss) in classification.items():
                label = z[key.split('_')[0] + '_label']
                take = label >= 0
                ys.append(label[take])
                ss.append(z[key][take])
    base = c.metrics.aggregate([r['metrics']['baseline'] for r in rows])['micro']
    for key in keys:
        records = [r['metrics'][key] for r in rows]
        row = c.metrics.aggregate(records)
        row['delta_f1_pp'] = 100 * (row['micro']['pixel_f1'] - base['pixel_f1'])
        row['delta_iou_pp'] = 100 * (row['micro']['pixel_iou'] - base['pixel_iou'])
        deleted_tp = sum(r['removed_tp'] for r in records)
        deleted_fp = sum(r['removed_fp'] for r in records)
        row['removed_tp'] = deleted_tp
        row['removed_fp'] = deleted_fp
        row['deletion_precision'] = c.metrics.ratio(deleted_fp, deleted_tp + deleted_fp, None)
        row['passes_no_harm'] = row['delta_f1_pp'] >= -.3 if dataset in ('levir', 'whu') else None
        row['threshold'] = config['schemes'].get(key, {}).get('threshold')
        row['threshold_source'] = config['threshold_source'] if key in classification else None
        row['classification'] = None
        if key in classification:
            ys, ss = classification[key]
            row['classification'] = c.old.binary_metrics(np.concatenate(ys), np.concatenate(ss))
            total_units = sum(r['units'][key.split('_')[0]]['count'] for r in rows)
            row['classification']['all_units'] = total_units
            row['classification']['excluded_middle'] = total_units - row['classification']['n']
        if key != 'baseline':
            unit = key.split('_')[0]
            row['units_per_image'] = c.metrics.distribution(r['units'][unit]['count'] for r in rows)
            row['uncovered_prediction_pixels'] = c.metrics.distribution(
                r['units'][unit]['uncovered_prediction_pixels'] for r in rows)
        summaries[key] = row
    if split == 'val':
        # 精确断点搜索与逐图真实掩码结果必须一致。
        for key, chosen in config['schemes'].items():
            actual = summaries[key]['micro']
            if abs(actual['pixel_f1'] - chosen['f1']) > 1e-12 or abs(actual['pixel_iou'] - chosen['iou']) > 1e-12:
                raise ValueError(f'{key}: 阈值曲线与真实掩码指标不一致')
    summary = dict(freeze_id=frozen['freeze_id'], protocol=c.protocol(args), dataset=dataset,
                   split=split, images=len(images), primary=config['primary'],
                   limitation=config['limitation'], rows=summaries,
                   seconds=sum(r['seconds'] for r in rows),
                   historical_verification=c.read_json(args.out / 'reference/verification.json'))
    c.write_json(dest / 'appendix_A_E.json', summaries)
    write_report(dest, summary)
    # summary 是验证报告完成标记；附表生成成功后才允许测试读取。
    c.write_json(dest / 'summary.json', summary)


def fmt(value, percent=False):
    if value is None:
        return '—'
    return f'{value * 100 if percent else value:.4f}'


def write_report(dest, summary):
    rows = summary['rows']
    text = [f'# {summary["dataset"].upper()} {summary["split"]} 去误检', '',
        f'图像数：{summary["images"]}；验证冻结主方案：`{summary["primary"]}`。', '',
        summary['limitation'], '',
        '主指标按全划分 micro 汇总。Δ 使用百分点；oracle 使用真值，仅作描述性上界。',
        'AUC/AP 的正类为保留单元；中间标签不计分类指标，但参与阈值像素搜索及最终删除。',
        'oracle 保留面积小于10的切块；概率规则按分数删除全部切块。对象交集至少10像素，未覆盖的预测像素保持原样。', '',
        '| 单元/方案 | 每图单元均值 | 阈值 | 阈值来源 | F1% | IoU% | ΔF1 | ΔIoU | 删除精度% | AUC | AP | ΔF1≥−0.3 |',
        '|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for key, row in rows.items():
        m, cl = row['micro'], row['classification'] or {}
        harmless = row['passes_no_harm']
        threshold_text = '—' if row['threshold'] is None else format(row['threshold'], '.17g')
        values = [key, fmt(row.get('units_per_image', {}).get('mean')), threshold_text,
                  row['threshold_source'] or '—', fmt(m['pixel_f1'], True), fmt(m['pixel_iou'], True),
                  fmt(row['delta_f1_pp']), fmt(row['delta_iou_pp']), fmt(row['deletion_precision'], True),
                  fmt(cl.get('auc')), fmt(cl.get('ap')), '—' if harmless is None else ('满足' if harmless else '不满足')]
        text.append('| ' + ' | '.join(values) + ' |')
    text += ['', '边界指标单独列于主表之后，全部不参与选择。BF 为容差1/2/4/5像素；Boundary IoU 为对角线2%内带及3像素内带。', '',
             '| 单元/方案 | BF1% | BF2% | BF4% | BF5% | Boundary IoU diag% | Boundary IoU 3% | 粗糙度 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key, row in rows.items():
        m = row['micro']
        values = [key] + [fmt(m[k], True) for k in ('bf1_f1', 'bf2_f1', 'bf4_f1', 'bf5_f1', 'biou_diag', 'biou_3')]
        text.append('| ' + ' | '.join(values + [fmt(m['pred_roughness'])]) + ' |')
    text += ['', 'A–E 附表完整保留在 appendix_A_E.json；appendix_micro.csv 便于横向比较。',
             '包含像素计数、边界、目标面积桶、拆分/粘连、粗糙度、碎片、逐图变化与有害编辑率。',
             '每图单元数、未覆盖预测像素和耗时见 images/*.json。删除精度分母为0时记为空。', '']
    if summary['protocol']['partial']:
        text += ['本报告为局部冒烟；历史复核未通过完整规模验收，不能作为正式实验结论。', '']
    (dest / 'report.md').write_text('\n'.join(text), encoding='utf-8')
    fields = ['scheme'] + list(next(iter(rows.values()))['micro']) + [
        'delta_f1_pp', 'delta_iou_pp', 'deletion_precision', 'removed_tp', 'removed_fp',
        'threshold', 'threshold_source', 'passes_no_harm', 'harmful_edit_rate', 'edited_images', 'harmful_images']
    with (dest / 'appendix_micro.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key, row in rows.items():
            writer.writerow(dict(scheme=key, **row['micro'], **{k: row[k] for k in fields if k not in ('scheme', *row['micro'])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('reference', 'prepare', 'calibrate', 'evaluate', 'report'))
    c.arguments(parser)
    parser.add_argument('--dataset', choices=tuple(c.EXPECTED))
    parser.add_argument('--split', choices=('val', 'test'), default='val')
    parser.add_argument('--shard', type=c.rt.shard_type, default=(0, 1))
    args = parser.parse_args()
    if args.command in ('evaluate', 'report') and args.dataset is None:
        parser.error('evaluate/report 必须指定 --dataset')
    if args.command != 'evaluate' and args.shard != (0, 1):
        parser.error('只有 evaluate 支持分片')
    c.initialize(args)
    globals()[args.command](args)


if __name__ == '__main__':
    main()
