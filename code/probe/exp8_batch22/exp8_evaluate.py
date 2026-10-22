"""Validation-only calibration, frozen evaluation, cascades and report generation."""
from __future__ import annotations

import argparse
import csv
import time
import uuid
from pathlib import Path

import exp8_config as cfg
from exp8_features import Pool, extract
import numpy as np
import torch
from exp6b_eval import curve_counts

rt, old = cfg.rt, cfg.old


def calibration_path(args):
    return args.out / ('smoke_calibration.json' if args.limit else 'frozen.json')


def training_reports(args):
    result = {}
    for variant in ('full', 'clean'):
        row = rt.read_json(args.out / 'models' / variant / 'training.json')
        if row['protocol'] != cfg.protocol(args) or row['variant'] != variant:
            raise ValueError(f'{variant}: training protocol differs')
        if row['checkpoint_saved'] != (not bool(args.limit)):
            raise ValueError(f'{variant}: smoke/checkpoint status differs')
        result[variant] = row
    return result


def validation_scores(args):
    pool = Pool('val', args)
    reports = training_reports(args)
    scores = {'detector': pool.meta[:, 0].copy()}
    for variant in ('full', 'clean'):
        path = args.out / 'models' / variant / 'validation_scores.npz'
        with np.load(path, allow_pickle=False) as z:
            if (str(z['run_id']) != reports[variant]['run_id']
                    or not np.array_equal(z['image'], np.asarray(pool.images)[pool.image_id])
                    or not np.array_equal(z['piece_id'], pool.piece_id)):
                raise ValueError(f'{path}: scores do not match validation pool/training run')
            scores[variant] = z['score'].copy()
    for value in scores.values():
        if not np.isfinite(value).all() or np.any((value < 0) | (value > 1)):
            raise ValueError('Scores must be finite probabilities')
    return pool, scores, reports


def score_map(objects, shape, scores):
    if len(objects) != len(scores):
        raise ValueError('Object count differs from score count')
    result = np.full(shape, -np.inf, np.float64)
    for obj, score in zip(objects, scores):
        x0, y0, x1, y1 = obj['bbox']
        view = result[y0:y1, x0:x1]
        mask = obj['mask']
        view[mask] = np.maximum(view[mask], float(score))
    return result


def pixel_values(tp, fp, fn):
    tp, fp, fn = np.asarray(tp), np.asarray(fp), np.asarray(fn)
    den_f1, den_iou = 2 * tp + fp + fn, tp + fp + fn
    f1 = np.divide(2 * tp, den_f1, out=np.zeros_like(tp, dtype=float), where=den_f1 > 0)
    iou = np.divide(tp, den_iou, out=np.zeros_like(tp, dtype=float), where=den_iou > 0)
    return f1, iou


def calibrate(args):
    path = calibration_path(args)
    if path.exists():
        load_calibration(args)
        print(f'Already frozen: {path}', flush=True)
        return
    pool, all_scores, reports = validation_scores(args)
    curves = {}
    for key, scores in all_scores.items():
        thresholds = np.unique(np.r_[0., scores.astype(np.float64), 2.])
        curves[key] = dict(threshold=thresholds, added_tp=np.zeros(len(thresholds), np.int64),
                           added_fp=np.zeros(len(thresholds), np.int64))
    base_counts = np.zeros(3, np.int64)
    for number, name in enumerate(pool.images, 1):
        objects, shape, _ = cfg.read_units('val', name, args)
        base, gt = cfg.raw_inputs('val', name, args)
        if shape != gt.shape:
            raise ValueError(f'{name}: pool/GT shape mismatch')
        counts = rt.metrics.pixel_counts(base, gt)
        base_counts += [counts[k] for k in ('tp', 'fp', 'fn')]
        for key, scores in all_scores.items():
            field = score_map(objects, shape, scores[pool.slices[name]])
            curve = curves[key]
            curve['added_tp'] += curve_counts(field[~base & gt], curve['threshold'], 'recall')
            curve['added_fp'] += curve_counts(field[~base & ~gt], curve['threshold'], 'recall')
        if number % 25 == 0:
            print(f'calibration {number}/{len(pool.images)}', flush=True)
    frozen = dict(scheme_id=str(uuid.uuid4()), protocol=cfg.protocol(args),
                  run_ids={k: v['run_id'] for k, v in reports.items()},
                  selection=['pixel_f1', 'pixel_iou'], tie_rule='higher threshold, then full/clean/detector',
                  rule='score >= threshold; cascade accept > upper, reject < lower',
                  roc80_diagnostic='current-split ranking only; pixel histograms from the single frozen pass',
                  seconds_per_object=cfg.SECONDS_PER_OBJECT, scorers={})
    baseline_f1, baseline_iou = map(float, pixel_values(*base_counts))
    for key in cfg.SCORERS:
        curve, scores = curves[key], all_scores[key]
        tp = base_counts[0] + curve['added_tp']
        fp = base_counts[1] + curve['added_fp']
        fn = base_counts[2] - curve['added_tp']
        f1, iou = pixel_values(tp, fp, fn)
        best = int(np.lexsort((curve['threshold'], iou, f1))[-1])
        supervised = pool.label >= 0
        classification = old.binary_metrics(pool.label[supervised], scores[supervised])
        recall80 = classification['recall_80']['threshold']
        if recall80 is None:
            raise ValueError('Validation has no positive units')
        lows = np.quantile(scores, cfg.LOW_QUANTILES)
        highs = np.quantile(scores, cfg.HIGH_QUANTILES)
        cascades = [dict(id=f'q{int(lq*100)}_q{int(hq*100)}', lower=float(low), upper=float(high),
                         lower_quantile=lq, upper_quantile=hq)
                    for lq, low in zip(cfg.LOW_QUANTILES, lows)
                    for hq, high in zip(cfg.HIGH_QUANTILES, highs)]
        frozen['scorers'][key] = dict(pixel_threshold=float(curve['threshold'][best]),
                                      recall80_threshold=recall80, classification=classification,
                                      pixel_f1=float(f1[best]), pixel_iou=float(iou[best]),
                                      delta_f1_pp=float(100*(f1[best]-baseline_f1)), cascades=cascades)
        old.save_npz(args.out / 'curves' / f'{key}.npz', **curve, tp=tp, fp=fp, fn=fn, f1=f1, iou=iou)
    frozen['baseline'] = dict(pixel_f1=baseline_f1, pixel_iou=baseline_iou)
    frozen['primary'] = max(cfg.SCORERS, key=lambda k: (frozen['scorers'][k]['pixel_f1'],
                                                      frozen['scorers'][k]['pixel_iou'], -cfg.SCORERS.index(k)))
    cfg.b20.write_fixed(path, frozen)
    print(f'Frozen validation thresholds: {path}', flush=True)


def load_calibration(args):
    frozen = rt.read_json(calibration_path(args))
    reports = training_reports(args)
    if (frozen['protocol'] != cfg.protocol(args)
            or frozen['run_ids'] != {k: v['run_id'] for k, v in reports.items()}):
        raise ValueError('Frozen scheme no longer matches completed training')
    return frozen


def label_counts(labels, accepted):
    return dict(tp=int(((labels == 1) & accepted).sum()),
                fp=int(((labels == 0) & accepted).sum()),
                positive=int((labels == 1).sum()), negative=int((labels == 0).sum()))


def evaluate_image(split, name, objects, shape, labels, scores, frozen, args):
    base, gt = cfg.raw_inputs(split, name, args)
    if shape != gt.shape or len(labels) != len(objects):
        raise ValueError(f'{name}: pool/label shape mismatch')
    result = dict(image=name, scheme_id=frozen['scheme_id'],
                  baseline=cfg.b20.score(base, gt, base, name), scorers={})
    histograms = {}
    for key in cfg.SCORERS:
        s = scores[key].astype(np.float64)
        field = score_map(objects, shape, s)
        residual = ~base & np.isfinite(field)
        values, inverse = np.unique(field[residual], return_inverse=True)
        tp = np.bincount(inverse[gt[residual]], minlength=len(values)).astype(np.int64)
        fp = np.bincount(inverse[~gt[residual]], minlength=len(values)).astype(np.int64)
        histograms.update({key + '_pixel_score': values, key + '_pixel_tp': tp, key + '_pixel_fp': fp})
        definition = frozen['scorers'][key]
        evaluated = {}

        def accepted_metrics(threshold, strict=False):
            cache_key = (float(threshold), strict)
            if cache_key not in evaluated:
                accepted = s > threshold if strict else s >= threshold
                mask = field > threshold if strict else field >= threshold
                added = mask & ~base
                evaluated[cache_key] = dict(
                    metrics=cfg.b20.score(base | mask, gt, base, name),
                    units=int(accepted.sum()), classification=label_counts(labels, accepted),
                    added_tp=int((added & gt).sum()), added_fp=int((added & ~gt).sum()))
            return evaluated[cache_key]

        policies = {policy: accepted_metrics(definition[threshold])
                    for policy, threshold in (('pixel', 'pixel_threshold'), ('recall80', 'recall80_threshold'))}
        cascades, high_policies = {}, {}
        for row in definition['cascades']:
            uncertain = (s >= row['lower']) & (s <= row['upper'])
            high_key = f'q{int(row["upper_quantile"] * 100)}'
            high_policies[high_key] = accepted_metrics(row['upper'], True)
            cascades[row['id']] = dict(accepted_key=high_key,
                                      uncertain=int(uncertain.sum()),
                                      positive=int(((labels == 1) & uncertain).sum()),
                                      negative=int(((labels == 0) & uncertain).sum()),
                                      middle=int(((labels == -1) & uncertain).sum()),
                                      rejected=int((s < row['lower']).sum()))
        result['scorers'][key] = dict(policies=policies, high_policies=high_policies, cascades=cascades)
    return result, histograms


def load_models(args, frozen):
    models = {}
    torch.set_num_threads(1)
    for key in ('full', 'clean'):
        path = args.out / 'models' / key / 'model.pt'
        checkpoint = torch.load(path, map_location='cpu', weights_only=True)
        if (checkpoint['run_id'] != frozen['run_ids'][key]
                or checkpoint['protocol'] != frozen['protocol']):
            raise ValueError(f'{path}: checkpoint differs from frozen scheme')
        model = old.classifier(checkpoint['input_dim']).eval()
        model.load_state_dict(checkpoint['state_dict'])
        models[key] = (model, checkpoint['mean'].numpy(), checkpoint['std'].numpy())
    return models


@torch.inference_mode()
def score_one(meta, extra, models):
    scores = dict(detector=meta[:, 0].copy())
    for key, (model, mean, std) in models.items():
        x = np.concatenate((meta[:, :11], extra), axis=1)
        result = np.empty(len(x), np.float32)
        for start in range(0, len(x), 4096):
            tensor = torch.from_numpy((x[start:start+4096] - mean) / std)
            result[start:start+4096] = model(tensor).squeeze(1).sigmoid().numpy()
        if not np.isfinite(result).all():
            raise ValueError(f'{key}: non-finite inference scores')
        scores[key] = result
    return scores


def evaluate(args, split):
    if split == 'test' and args.limit:
        raise ValueError('No test access in smoke mode')
    frozen = load_calibration(args)
    root = args.out / 'evaluation' / split
    images = cfg.names(split, args.limit)
    models, pool, all_scores = None, None, None
    if split == 'test':
        validation = rt.read_json(args.out / 'evaluation/val/summary.json')
        if validation['scheme_id'] != frozen['scheme_id'] or validation['images'] != cfg.names('val'):
            raise ValueError('Complete validation evaluation before the single frozen test pass')
        models = load_models(args, frozen)
    else:
        pool, all_scores, _ = validation_scores(args)
    for number, name in enumerate(images[args.shard[0]::args.shard[1]], 1):
        dest = root / 'images' / (Path(name).stem + '.json')
        score_path = root / 'scores' / (Path(name).stem + '.npz')
        if dest.exists():
            saved = rt.read_json(dest)
            if saved['scheme_id'] != frozen['scheme_id'] or not score_path.exists():
                raise ValueError(f'{dest}: incomplete or different frozen evaluation')
            continue
        start = time.monotonic()
        if split == 'test':
            meta, extra, labels = extract(split, name, args)
            scores = score_one(meta, extra, models)
        else:
            sl = pool.slices[name]
            labels = pool.label[sl]
            scores = {k: v[sl] for k, v in all_scores.items()}
        objects, shape, _ = cfg.read_units(split, name, args)
        result, histograms = evaluate_image(split, name, objects, shape, labels, scores, frozen, args)
        old.save_npz(score_path, label=labels, unit_id=np.arange(len(labels)),
                     scheme_id=frozen['scheme_id'], **scores, **histograms)
        result['seconds'] = time.monotonic() - start
        rt.write_json(dest, result)
        print(f'{split} {number}: {name}, {result["seconds"]:.2f}s', flush=True)


def aggregate_policy(rows, baseline):
    aggregate = rt.metrics.aggregate([r['metrics'] for r in rows])
    counts = {k: sum(r['classification'][k] for r in rows) for k in ('tp', 'fp', 'positive', 'negative')}
    tp, fp = sum(r['added_tp'] for r in rows), sum(r['added_fp'] for r in rows)
    precision = cfg.b20.ratio(tp, tp + fp)
    half = baseline['micro']['pixel_f1'] / 2
    return dict(metrics=aggregate, classification=dict(**counts,
                recall=cfg.b20.ratio(counts['tp'], counts['positive']),
                fpr=cfg.b20.ratio(counts['fp'], counts['negative'])),
                accepted_units=sum(r['units'] for r in rows),
                added_tp=tp, added_fp=fp, added_pixel_precision=precision, f1_half=half,
                exceeds_f1_half=None if precision is None else precision > half,
                delta_f1_pp=100*(aggregate['micro']['pixel_f1']-baseline['micro']['pixel_f1']),
                delta_iou_pp=100*(aggregate['micro']['pixel_iou']-baseline['micro']['pixel_iou']))


def legacy_reference(split):
    dataset = cfg.RAW_DATASET[split]
    calibration = rt.read_json(cfg.OLD_RUN / 'thresholds_second_cv.json')
    report_path = cfg.OLD_RUN / f'eval_{dataset}.json'
    report = rt.read_json(report_path)
    if (calibration['recall'][cfg.LEGACY_COMBINATION]['threshold'] != cfg.LEGACY_THRESHOLD
            or report['run_id'] != calibration['run_id']):
        raise ValueError('Historical 6b run/threshold differs')
    result = dict(report_file=str(report_path), score_file=str(cfg.OLD_RUN / dataset / 'scores.npz'),
                  threshold=cfg.LEGACY_THRESHOLD, classification=report['recall'],
                  classification_scope='historical all five recall sources; by-source values retained',
                  recall80_scope='historical descriptive current-split ROC80 over all five sources',
                  baseline=report['pixels']['baseline'],
                  pixel=report['pixels']['recall/' + cfg.LEGACY_COMBINATION],
                  recall80=report['pixels']['pixel_precision_at_recall_80'])
    if split == 'test':
        full = rt.read_json(cfg.OLD_METRICS)
        if full['config']['v1_threshold'] != cfg.LEGACY_THRESHOLD:
            raise ValueError('Historical A–E report threshold differs')
        result['appendix_file'] = str(cfg.OLD_METRICS)
        result['appendix'] = full['rows']['5']
    return result


def aggregate(args, split):
    if split == 'test' and args.limit:
        raise ValueError('Smoke cannot report test')
    frozen = load_calibration(args)
    images = cfg.names(split, args.limit)
    root = args.out / 'evaluation' / split
    records = []
    labels, score_lists = [], {k: [] for k in cfg.SCORERS}
    histograms = {k: [] for k in cfg.SCORERS}
    for name in images:
        record = rt.read_json(root / 'images' / (Path(name).stem + '.json'))
        if record['scheme_id'] != frozen['scheme_id'] or record['image'] != name:
            raise ValueError(f'{name}: different evaluation scheme')
        records.append(record)
        with np.load(root / 'scores' / (Path(name).stem + '.npz'), allow_pickle=False) as z:
            if str(z['scheme_id']) != frozen['scheme_id']:
                raise ValueError(f'{name}: scores belong to a different scheme')
            labels.append(z['label'].copy())
            for key in cfg.SCORERS:
                score_lists[key].append(z[key].copy())
                histograms[key].append(tuple(z[key + suffix].copy()
                                             for suffix in ('_pixel_score', '_pixel_tp', '_pixel_fp')))
    baseline = rt.metrics.aggregate([r['baseline'] for r in records])
    labels = np.concatenate(labels)
    selected = labels >= 0
    summary = dict(scheme_id=frozen['scheme_id'], split=split, images=images, partial=bool(args.limit),
                   primary=frozen['primary'], baseline=baseline, scorers={},
                   inference_seconds=sum(r['seconds'] for r in records))
    for key in cfg.SCORERS:
        scores = np.concatenate(score_lists[key])
        classification = old.binary_metrics(labels[selected], scores[selected])
        roc80 = classification['recall_80']
        threshold = roc80['threshold']
        added_tp = added_fp = 0
        if threshold is not None:
            for values, tp, fp in histograms[key]:
                take = values >= threshold
                added_tp += int(tp[take].sum())
                added_fp += int(fp[take].sum())
        precision = cfg.b20.ratio(added_tp, added_tp + added_fp)
        half = baseline['micro']['pixel_f1'] / 2
        diagnostic = dict(**roc80, diagnostic_only=True, threshold_from=split + '_labeled_units',
                          added_tp=added_tp, added_fp=added_fp, added_pixel_precision=precision,
                          f1_half=half, exceeds_f1_half=None if precision is None else precision > half)
        classification = {k: classification[k] for k in ('n', 'positive', 'negative', 'auc', 'ap')}
        policies = {policy: aggregate_policy([r['scorers'][key]['policies'][policy] for r in records], baseline)
                    for policy in ('pixel', 'recall80')}
        cascades = {}
        for definition in frozen['scorers'][key]['cascades']:
            rows = [r['scorers'][key]['cascades'][definition['id']] for r in records]
            totals = {k: sum(r[k] for r in rows) for k in ('uncertain', 'positive', 'negative', 'middle', 'rejected')}
            cascades[definition['id']] = dict(**definition, **totals,
                per_image=rt.metrics.distribution(r['uncertain'] for r in rows),
                positive_rate_all=cfg.b20.ratio(totals['positive'], totals['uncertain']),
                positive_rate_supervised=cfg.b20.ratio(totals['positive'], totals['positive'] + totals['negative']),
                gpu_hours=totals['uncertain'] * cfg.SECONDS_PER_OBJECT / 3600,
                projected_full_test_gpu_hours=(totals['uncertain'] / len(images) * cfg.EXPECTED['test']
                                               * cfg.SECONDS_PER_OBJECT / 3600),
                accepted=aggregate_policy([r['scorers'][key]['high_policies'][c['accepted_key']]
                                           for r, c in zip(records, rows)], baseline))
        summary['scorers'][key] = dict(classification=classification, roc80_diagnostic=diagnostic,
                                      policies=policies, cascades=cascades)
    if not args.limit:
        summary['legacy'] = legacy_reference(split)
    rt.write_json(root / 'summary.json', summary)
    write_report(root, summary, frozen)


def fmt(value, percent=False):
    return '—' if value is None else f'{value * (100 if percent else 1):.4f}'


def write_report(root, summary, frozen):
    text = ['# SECOND 对象池 V1', '',
            f'划分：{summary["split"]}；图数：{len(summary["images"])}；冒烟：{summary["partial"]}。',
            f'验证集预选主方案：{summary["primary"]}。测试结果不重新排序选择方案。',
            '监督标签：正例 ≥50%，负例 <10%，中间标签仅参与推理与像素/级联统计。',
            '所有操作阈值来自验证集。recall80 在验证集达到至少80%；测试表报告其实际召回。',
            '新增像素精度按接受单元并集扣除原预测计算；重叠像素只计一次。', '',
            '| 方案 | 阈值规则 | AUC | AP | 阈值 | 召回% | FPR% | 新增像素精度% | F1/2% | F1% | IoU% | ΔF1 pp | ΔIoU pp |',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for key, row in summary['scorers'].items():
        for policy, p in row['policies'].items():
            c, m = p['classification'], p['metrics']['micro']
            threshold = frozen['scorers'][key]['pixel_threshold' if policy == 'pixel' else 'recall80_threshold']
            values = [fmt(row['classification']['auc']), fmt(row['classification']['ap']), fmt(threshold),
                      fmt(c['recall'], True), fmt(c['fpr'], True), fmt(p['added_pixel_precision'], True),
                      fmt(p['f1_half'], True), fmt(m['pixel_f1'], True), fmt(m['pixel_iou'], True),
                      fmt(p['delta_f1_pp']), fmt(p['delta_iou_pp'])]
            text.append(f'| {key} | {policy} | ' + ' | '.join(values) + ' |')
    text += ['', '## 当前划分 ROC80 描述性统计', '',
             '使用冻结模型的同一次推理结果。此处描述达到80%召回的排序表现；不选择端到端操作方案。',
             '像素精度由本次已保存的逐像素最大分数直方图汇总，不重新推理或读取测试掩码。', '',
             '| 方案 | ROC80阈值 | 实际召回% | FPR% | 新增像素精度% | F1/2% |',
             '|---|---:|---:|---:|---:|---:|']
    for key, row in summary['scorers'].items():
        d = row['roc80_diagnostic']
        text.append(f'| {key} | {fmt(d["threshold"])} | {fmt(d["achieved_recall"], True)}'
                    f' | {fmt(d["fpr"], True)} | {fmt(d["added_pixel_precision"], True)}'
                    f' | {fmt(d["f1_half"], True)} |')
    text += ['', '## 级联（全部阈值组合预先冻结）', '',
             '接受 score > upper；丢弃 score < lower；等于任一边界归不确定。',
             'GPU小时 = 不确定数 × 0.44 / 3600，不含加载、裁块和传输开销；未调用 MLLM。', '',
             '| 方案 | 分位数组合 | 下/上阈值 | 不确定/图均值 | P50 | P90 | 正例率%（全体/有标签） | 中间数 | 仅接受 ΔF1 pp | GPU小时 | 全测试规模估计小时 |',
             '|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|']
    for key, row in summary['scorers'].items():
        for tag, c in row['cascades'].items():
            d = c['per_image']
            text.append(f'| {key} | {tag} | {fmt(c["lower"])}/{fmt(c["upper"])} | {fmt(d["mean"])}'
                        f' | {fmt(d["p50"])} | {fmt(d["p90"])} | {fmt(c["positive_rate_all"], True)}/'
                        f'{fmt(c["positive_rate_supervised"], True)} | {c["middle"]} | {fmt(c["accepted"]["delta_f1_pp"])}'
                        f' | {fmt(c["gpu_hours"])} | {fmt(c["projected_full_test_gpu_hours"])} |')
    appendices = {'baseline': summary['baseline']}
    for key, row in summary['scorers'].items():
        appendices.update({f'{key}/{p}': x['metrics'] for p, x in row['policies'].items()})
        appendices.update({f'{key}/{p}': x['accepted']['metrics'] for p, x in row['cascades'].items()})
    if 'legacy' in summary:
        old_row = summary['legacy']
        text += ['', '## 旧 U0 V1（直接引用，未重训）', '',
                 f'像素阈值 {old_row["threshold"]}；像素行限 B+G0.05+H1。',
                 '旧分类总体及旧 recall80 来自五种补漏来源，不能当作本批三来源的同口径分类对照。',
                 f'来源：`{old_row["report_file"]}`；原分数：`{old_row["score_file"]}`。', '',
                 '| 分类范围 | AUC | AP | ROC80 FPR% |', '|---|---:|---:|---:|']
        for tag, row in [('overall (5 sources)', old_row['classification']['overall'])] + list(old_row['classification']['by_source'].items()):
            text.append(f'| {tag} | {fmt(row["auc"])} | {fmt(row["ap"])} | {fmt(row["recall_80"]["fpr"], True)} |')
        p, r80, b = old_row['pixel'], old_row['recall80'], old_row['baseline']
        text += ['', '| 像素方案 | F1% | IoU% | ΔF1 pp | ΔIoU pp |',
                 '|---|---:|---:|---:|---:|',
                 f'| 旧 U0 B+G0.05+H1 | {fmt(p["f1"], True)} | {fmt(p["iou"], True)}'
                 f' | {fmt(p["delta_f1_pp"])} | {fmt(100*(p["iou"]-b["iou"]))} |', '',
                 f'旧五来源描述性 ROC80 新增像素精度 {fmt(r80["precision"], True)}%，'
                 f'F1/2={fmt(r80["f1_half"], True)}%；其阈值在当前划分上计算，未作为本批操作阈值。',
                 '旧附表缺少 BF d1/d4、拆分/粘连、有害编辑率和边界逐图变化，CSV 对应项留空。',
                 '原值及来源范围见 `legacy.json`；本批新方案的 A–E 完整计算。']
        rt.write_json(root / 'legacy.json', old_row)
        if 'appendix' in old_row:
            appendices['legacy_U0'] = old_row['appendix']
    text += ['', '## A–E 附表', '',
             '完整 micro、目标面积桶、逐图分位数、最差五图和有害编辑率见 `appendix.json`；',
             '`appendix_micro.csv` 收录全部 micro 指标，逐图原值在 `images/*.json`。', '',
             '| 方案 | BF1 d2% | Boundary IoU% | 目标F1% | 预测粗糙度 | 新误检对象/图 | 下降图% | 有害编辑率% |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for tag, row in appendices.items():
        m, d = row['micro'], row['distributions']
        text.append(f'| {tag} | {fmt(m["bf2_f1"], True)} | {fmt(m["biou_diag"], True)}'
                    f' | {fmt(m["object_f1"], True)} | {fmt(m["pred_roughness"])}'
                    f' | {fmt(d["new_false_objects"]["mean"])} | {fmt(row["delta_f1"]["decline_fraction"], True)}'
                    f' | {fmt(row.get("harmful_edit_rate"), True)} |')
    rt.write_json(root / 'appendix.json', appendices)
    fields = list(summary['baseline']['micro'])
    with (root / 'appendix_micro.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['scheme'] + fields)
        writer.writeheader()
        for tag, row in appendices.items():
            writer.writerow(dict(scheme=tag, **{k: row['micro'].get(k) for k in fields}))
    (root / 'report.md').write_text('\n'.join(text) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cfg.arguments(parser)
    parser.add_argument('action', choices=('calibrate', 'validation', 'test', 'report'))
    parser.add_argument('--split', choices=('val', 'test'), default='val', help='report only')
    parser.add_argument('--shard', type=rt.shard_type, default=(0, 1))
    args = parser.parse_args()
    if args.action in ('calibrate', 'report') and args.shard != (0, 1):
        parser.error('Calibration/report require all images')
    if args.action == 'calibrate':
        calibrate(args)
    elif args.action == 'report':
        aggregate(args, args.split)
    else:
        evaluate(args, 'val' if args.action == 'validation' else 'test')


if __name__ == '__main__':
    main()
