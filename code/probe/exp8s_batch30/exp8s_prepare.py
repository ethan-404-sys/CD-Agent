"""缓存清单、验证 C2 切点、历史一致性核对与可复用逐图数据底座。"""
from __future__ import annotations

from pathlib import Path
import time

from exp8s_common import (np, rt, ROOT, B18, B19, VERSION, LEVELS, DATASETS,
                         runtime, upstream, names, sam_path, h1_path, u5_path, read_json,
                         write_json, crop, full, put, counts, scores, target_objects,
                         save_objects, annotate, groups_for, union_object, dedup)
from exp8s_sources import c2_scores, generate

SAM_KEYS = ('cls_t1', 'cls_t2', 'conf_t1', 'conf_t2', 'inst_t1', 'inst_t2',
            'inst_scores_t1', 'inst_scores_t2', 'dist')
H1_KEYS = ('shape', 'h1_masks', 'h1_scores')


def inventory(dataset, images, require_u5=True):
    missing, h1_missing, invalid = [], [], []
    r, out = runtime(dataset), upstream(dataset)
    for name in images:
        stem = Path(name).stem
        paths = [r.prediction_dir(dataset, out) / 'prob' / (stem + '.npy'),
                 r.prediction_dir(dataset, out) / 'mask' / name, sam_path(dataset, name)]
        if require_u5 and dataset in ('second_cv', 'second', 'sysu_val', 'sysu'):
            paths.append(u5_path(dataset, name))
        for path in paths:
            if not path.is_file():
                missing.append(str(path))
        sam = sam_path(dataset, name)
        if sam.is_file():
            with np.load(sam, allow_pickle=False) as z:
                invalid.extend(f'{sam}: missing {k}' for k in SAM_KEYS if k not in z.files)
        path = h1_path(dataset, name)
        if not path.is_file():
            h1_missing.append(str(path))
        else:
            with np.load(path, allow_pickle=False) as z:
                invalid.extend(f'{path}: missing {k}' for k in H1_KEYS if k not in z.files)
    return dict(dataset=dataset, images=images, missing=missing, invalid=invalid,
                h1_available=not h1_missing, h1_missing=h1_missing,
                h1_status='available' if not h1_missing else 'missing; skip entire split H1 and its combinations')


def preflight(args):
    # 全量清单先检查，冒烟也不能把未采样区域的缺失 H1 当作已完整覆盖。
    datasets = getattr(args, 'datasets', DATASETS)
    reports = {ds: inventory(ds, names(ds)) for ds in datasets}
    for ds, out in (('second_cv', B18), ('sysu_val', B19)):
        if ds not in datasets:
            continue
        frozen = read_json(out / 'frozen' / (ds + '.json'))
        if frozen['partial'] or frozen['multiplier'] != 4 or frozen['validation_dataset'] != ds:
            raise ValueError(f'{ds}: require frozen U5 x4')
    if any(ds in datasets for ds in ('levir', 'whu')):
        levir_images = names('levir_val')
        absent = [str(sam_path('levir_val', name)) for name in levir_images
                  if not sam_path('levir_val', name).is_file()]
        reports['levir_val_c2'] = dict(images=levir_images, missing_sam3=absent,
                                       fallback_all_c2=bool(absent))
        if not absent:
            calibration = inventory('levir_val', levir_images, require_u5=False)
            reports['levir_val_c2']['missing'] = calibration['missing']
            reports['levir_val_c2']['invalid'] = calibration['invalid']
    write_json(args.out / 'preflight.json', reports)
    failures = [x for row in reports.values() for key in ('missing', 'invalid') for x in row.get(key, [])]
    if failures:
        raise FileNotFoundError('Required caches unavailable; no recomputation:\n' + '\n'.join(failures))
    return reports


def calibrate(args, reports):
    result = dict(version=VERSION, partial=bool(args.limit), datasets={})
    datasets = getattr(args, 'datasets', DATASETS)
    for ds in ('second_cv', 'sysu_val', 'levir_val'):
        targets = {'second_cv': ('second_cv', 'second'), 'sysu_val': ('sysu_val', 'sysu'),
                   'levir_val': ('levir', 'whu')}[ds]
        targets = [target for target in targets if target in datasets]
        if not targets:
            continue
        images = names(ds)
        selected = images[:args.limit or None]
        if ds == 'levir_val' and reports['levir_val_c2']['fallback_all_c2']:
            row = dict(validation_dataset=ds, images=[], score_count=0, cut=None,
                       policy='C1 + all C2; LEVIR val SAM3 missing',
                       missing_sam3=reports['levir_val_c2']['missing_sam3'])
        else:
            values = [value for name in selected for value in c2_scores(ds, name)]
            row = dict(validation_dataset=ds, images=selected, score_count=len(values),
                       cut=float(np.quantile(values, .8)) if values else None,
                       policy='validation C2 score >= 80th percentile; exp4 eligibility before quantile'
                              if values else 'no eligible validation C2; conservatively keep all C2')
        for target in targets:
            result['datasets'][target] = {**row, 'transfer': target == 'whu'}
    write_json(args.out / 'calibration.json', result)
    return result


def reference(args):
    result = dict(version=VERSION, partial=bool(args.limit), historical_verified=False, datasets={})
    failed = []
    for ds, display in (('second', '4.69'), ('sysu', '2.16')):
        r, out = runtime(ds), upstream(ds)
        images = names(ds)[:args.limit or None]
        summary = read_json(out / 'pool' / ds / 'summary.json')
        expected_delta = summary['rows']['objects_direct_pairs']['delta_f1_pp']
        base_total, final_total = dict(tp=0, fp=0, fn=0), dict(tp=0, fp=0, fn=0)
        per_image = []
        for name in images:
            prob = r.probability(ds, name, out)
            base = r.prediction(ds, name, out)
            gt = r.common.load_label(ds, name)
            # 原函数包含面积 >=10、pred 覆盖 <=0.5、B/G/H1 顺序与整候选。
            objects = r.raw_objects(ds, name, prob, out)
            units = [union_object(objects, ids) for ids in groups_for(objects)['direct_pairs']]
            adapted = dedup(objects)
            adapter_equal = len(units) == len(adapted) and all(
                a['bbox'] == b['bbox'] and np.array_equal(a['mask'], b['mask'])
                for a, b in zip(units, adapted))
            final = base.copy()
            for obj in units:
                x0, y0, x1, y1 = obj['bbox']
                if gt[y0:y1, x0:x1][obj['mask']].mean() >= .5:
                    put(final, obj)
            b, f = counts(base, gt), counts(final, gt)
            previous = read_json(out / 'pool' / ds / 'images' / (Path(name).stem + '.json'))
            eb = {k: previous['baseline']['counts'][k] for k in b}
            ef = {k: previous['units']['objects_direct_pairs']['metrics']['counts'][k] for k in f}
            ok = (adapter_equal and b == eb and f == ef
                  and len(units) == previous['units']['objects_direct_pairs']['unit_count'])
            if not ok:
                failed.append(f'{ds}/{name}: historical integer counts differ')
            per_image.append(dict(image=name, passed=ok, baseline=b, oracle=f))
            for k in b:
                base_total[k] += b[k]
                final_total[k] += f[k]
        delta = 100*(scores(final_total)[0]-scores(base_total)[0])
        full_match = (delta == expected_delta and format(delta, '.2f') == display
                      and base_total == {k: summary['baseline']['counts'][k] for k in base_total}
                      and final_total == {k: summary['rows']['objects_direct_pairs']['evaluation']['counts'][k]
                                          for k in final_total})
        if not args.limit and not full_match:
            failed.append(f'{ds}: exact full aggregate or published display differs')
        result['datasets'][ds] = dict(images=images, image_checks=per_image, delta_f1_pp=delta,
                                      expected_delta_f1_pp=expected_delta, published_display=display,
                                      full_match=full_match, summary_path=str(out / 'pool' / ds / 'summary.json'))
    result['failures'] = failed
    result['passed'] = not failed
    result['historical_verified'] = not failed and not args.limit
    write_json(args.out / 'reference.json', result)
    if failed:
        raise ValueError('Historical consistency failed:\n' + '\n'.join(failed))
    return result


def require_reference(args):
    source = getattr(args, 'reference_from', None)
    row = read_json(source if source is not None else args.out / 'reference.json')
    if source is not None and (row['version'] != VERSION or row['partial'] or not row['historical_verified']):
        raise ValueError('--reference-from requires a compatible, fully verified reference.json')
    if not row['passed'] or row['partial'] != bool(args.limit) or (not args.limit and not row['historical_verified']):
        raise ValueError('Require matching, successful historical reference before preparing/reporting')
    for ds in ('second', 'sysu'):
        if row['datasets'][ds]['images'] != names(ds)[:args.limit or None]:
            raise ValueError('Reference image scope differs from current run')


def prepare(args, reports, calibration):
    require_reference(args)
    shard, shards = getattr(args, 'shard', 0), getattr(args, 'shards', 1)
    finalize = getattr(args, 'finalize', False)
    if calibration['partial'] != bool(args.limit):
        raise ValueError('Calibration smoke/full mismatch')
    for ds in getattr(args, 'datasets', DATASETS):
        images = names(ds)[:args.limit or None]
        h1_available = reports[ds]['h1_available']
        config = dict(version=VERSION, dataset=ds, images=images, partial=bool(args.limit),
                      c2=calibration['datasets'][ds], h1_available=h1_available, levels=LEVELS)
        # JSON 规范化，使断点恢复时元组/列表不会产生伪差异。
        import json
        config = json.loads(json.dumps(config))
        root = args.out / 'interim/exp8s' / ds
        existing = root / 'manifest.json'
        if existing.exists() and read_json(existing)['config'] != config:
            raise ValueError(f'{root}: use a fresh output directory for a different configuration')
        baseline = dict(tp=0, fp=0, fn=0)
        for number, name in enumerate(images, 1):
            if not finalize and (number - 1) % shards != shard:
                continue
            dest = root / Path(name).stem
            done = dest / 'image.json'
            if done.exists():
                row = read_json(done)
                if row['config'] != config or row['image'] != name:
                    raise ValueError(f'{done}: incompatible image cache')
                for rel in row['files']:
                    if not (dest / rel).is_file():
                        raise FileNotFoundError(dest / rel)
            else:
                if finalize:
                    raise FileNotFoundError(f'{done}: missing completion marker; finish the prepare shards first')
                start = time.perf_counter()
                r, out = runtime(ds), upstream(ds)
                prob = r.probability(ds, name, out)
                base = prob > .5
                gt = r.common.load_label(ds, name)
                if gt.shape != base.shape:
                    raise ValueError(f'{ds}/{name}: GT/probability shape mismatch')
                missed, ns = target_objects(ds, name, gt, base)
                variants, dropped = generate(ds, name, prob, calibration['datasets'][ds]['cut'], h1_available)
                if ds in ('second_cv', 'second', 'sysu_val', 'sysu'):
                    original, shape = rt.load_units(u5_path(ds, name))
                    if shape != base.shape:
                        raise ValueError(f'{ds}/{name}: U5 geometry mismatch')
                    u5 = []
                    for ident, obj in enumerate(original):
                        mask = full(obj, shape) & ~base
                        if mask.sum() >= 20:
                            u5.append(crop(mask, source='U5', sources=['U5'], score=0.,
                                           level=4, original_id=ident, members=[ident]))
                    variants['U5'] = u5
                    dropped['U5'] = dict(selected_before_area_filter=len(original),
                                         discarded_new_lt20=len(original)-len(u5), retained_before_dedup=len(u5))
                dest.mkdir(parents=True, exist_ok=True)
                # 保存逐图底座，后续不用再读上游 SAM3/AnyChange、预测或标签。
                rt.save_npz(dest / 'pixels.npz', shape=np.asarray(gt.shape), probability=prob,
                            gt=np.packbits(gt.ravel()), base=np.packbits(base.ravel()))
                save_objects(dest / 'missed', missed, gt.shape)
                save_objects(dest / 'no_signal', ns, gt.shape)
                files = ['pixels.npz', 'missed.npz', 'missed.json', 'no_signal.npz', 'no_signal.json']
                for key, objects in variants.items():
                    annotated = [annotate(o, gt, prob, missed, ns) for o in objects]
                    save_objects(dest / key, annotated, gt.shape)
                    files += [key + '.npz', key + '.json']
                row = dict(config=config, image=name, files=files, variants=list(variants),
                           dropped=dropped, baseline=counts(base, gt), seconds=time.perf_counter()-start)
                write_json(done, row)
            for key in baseline:
                baseline[key] += row['baseline'][key]
            print(f'prepare {ds} {number}/{len(images)} {name}', flush=True)
        if shards == 1 or finalize:
            write_json(existing, dict(config=config, baseline=baseline,
                                      h1_status=reports[ds]['h1_status'], h1_missing=reports[ds]['h1_missing']))
