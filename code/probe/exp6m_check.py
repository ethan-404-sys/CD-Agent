"""TASK 授权的真实数据轻量验收；不加载模型、不提交作业。"""
from __future__ import annotations
from argparse import Namespace
from collections import Counter
from pathlib import Path
import argparse
import ast
import math
import random
import resource
import subprocess
import sys
import time

import exp6m_common as c


def data_check(out):
    import numpy as np
    import exp6m_data as d
    from PIL import Image
    import exp6m_eval as evaluation
    from sklearn.metrics import average_precision_score, roc_auc_score
    started = time.monotonic()
    results = {}
    for dataset in ('sysu', 'second'):
        excluded = d.exclusions(dataset)
        z = d.inventory(dataset, 'train')
        lookup = {(str(z['images'][im]), int(unit)): j
                  for j, (im, unit) in enumerate(zip(z['image_id'], z['id'], strict=True))}
        runs = [c.rows(c.HERE / 'check/data' / f'{dataset}_r{ratio}.jsonl') for ratio in (1, 3, 5)]
        positives, negatives = [], []
        for ratio, data in zip((1, 3, 5), runs, strict=True):
            assert max(Counter(r['image'] for r in data).values()) <= 8
            hard_count = 0
            for row in data:
                key = row['image'], row['unit_id']
                assert key not in excluded
                j = lookup[key]
                assert row['precision'] == float(z['precision'][j])
                if row['label']:
                    assert z['precision'][j] >= .5 and z['majority'][j] != 3
                else:
                    assert z['precision'][j] < .1
                    hard_count += row['stratum'] == 'hard_neg'
            pos = [r['cid'] for r in data if r['label']]
            neg = [r['cid'] for r in data if not r['label']]
            assert 2*hard_count >= len(neg)
            stats = c.read(c.HERE / 'check/data' / f'{dataset}_r{ratio}.stats.json')
            assert stats['negative_shortfall'] == ratio*len(pos)-len(neg)
            positives.append(pos)
            negatives.append(neg)
        assert positives[0] == positives[1] == positives[2]
        assert negatives[0] == negatives[1][:len(negatives[0])]
        assert negatives[1] == negatives[2][:len(negatives[1])]
        # 逐图独立按D降序核对难负例身份，包括并列时的单元次序。
        negative_pool = {}
        for j, (im, unit, precision) in enumerate(zip(z['image_id'], z['id'], z['precision'], strict=True)):
            key = str(z['images'][im]), int(unit)
            if precision < .1 and key not in excluded:
                negative_pool.setdefault(key[0], []).append(j)
        hard_keys = set()
        for image, js in negative_pool.items():
            ordered = sorted(js, key=lambda j: (-float(z['dscore'][j]), j))
            hard_keys.update((image, int(z['id'][j])) for j in ordered[:math.ceil(len(js)/5)])
        for row in runs[-1]:
            if not row['label']:
                assert (row['stratum'] == 'hard_neg') == ((row['image'], row['unit_id']) in hard_keys)
        repeat = out / f'{dataset}_repeat.jsonl'
        d.sample(Namespace(dataset=dataset, seed=c.SEED, neg_ratio=3, pos_policy='ign3',
                            per_image_cap=8, max_pos=15000, out=repeat))
        assert c.rows(repeat) == runs[1]
        val = c.rows(c.HERE / 'check/data' / f'{dataset}_val.jsonl')
        vz = d.inventory(dataset, 'val')
        valid = [(j, (str(vz['images'][im]), int(unit)))
                 for j, (im, unit) in enumerate(zip(vz['image_id'], vz['id'], strict=True))
                 if (str(vz['images'][im]), int(unit)) not in excluded]
        expected_pos2 = {key for j, key in valid if vz['precision'][j] >= .5 and vz['majority'][j] == 2}
        assert {(r['image'], r['unit_id']) for r in val if r['stratum'] == 'pos2'} == expected_pos2
        population = c.read(c.HERE / 'check/data' / f'{dataset}_val.stats.json')['strata']
        for stratum in c.STRATA:
            group = [r for r in val if r['stratum'] == stratum]
            assert math.isclose(sum(r['weight'] for r in group), population[stratum]['population'], rel_tol=1e-12)
            assert len(group) == population[stratum]['selected']
        e = c.upstream_eval()
        controls = e.controls(dataset, val, False, c.FUSE[dataset])
        mask = evaluation.primary(val)
        y, weights = np.array([r['label'] for r in val]), np.array([r['weight'] for r in val])
        scores = np.asarray(controls['D'])
        ranking = e.ranking(val, scores, n_boot=0)
        assert np.isclose(ranking['primary_ap'], average_precision_score(y[mask], scores[mask], sample_weight=weights[mask]), atol=1e-12)
        assert np.isclose(ranking['primary_auc'], roc_auc_score(y[mask], scores[mask], sample_weight=weights[mask]), atol=1e-12)
        fuse = np.asarray(controls[c.FUSE[dataset]])
        late = evaluation.select_late(val, scores, fuse)
        late_scores = evaluation.late_score(scores, fuse, late)
        # 输入是已有D和融合分数，检验数学流程；并非微调预测或效果报告。
        for field, s in [('ft', scores), ('fuse', fuse)]:
            values = evaluation.standardize(s, late['normalization'][field])
            assert abs(np.average(values, weights=weights)) < 1e-10
            assert abs(np.average(values**2, weights=weights)-1) < 1e-10
        diff = evaluation.paired_intervals(val, dict(finetuned=scores, late_fusion=late_scores, fusion=fuse), n_boot=20)
        images = list(dict.fromkeys(r['image'] for r in val))
        rng = np.random.default_rng(c.BOOT_SEED)
        independent = [[], []]
        for _ in range(20):
            sampled = rng.integers(0, len(images), len(images))
            multiplicity = Counter(images[j] for j in sampled)
            weighted = weights * np.array([multiplicity[r['image']] for r in val])
            active = mask & (weighted > 0)
            if len(np.unique(y[active])) < 2:
                continue
            base = average_precision_score(y[active], fuse[active], sample_weight=weighted[active])
            for index, values in enumerate((scores, late_scores)):
                independent[index].append(average_precision_score(y[active], values[active], sample_weight=weighted[active])-base)
        for key, values in zip(('finetuned_minus_fusion', 'late_fusion_minus_fusion'), independent, strict=True):
            assert np.allclose(diff[key]['ci95'], np.quantile(values, [.025, .975]), atol=1e-12)
        results[dataset] = dict(train_sizes=[len(r) for r in runs], positives=len(positives[0]),
                              negatives=[len(n) for n in negatives], nested=True, reproducible=True,
                              exclusions=len(excluded), val_count=len(val),
                              val_weights_verified=True, control_alignment={k: len(v) for k, v in controls.items()},
                              metrics_match_sklearn=True, paired_bootstrap20_match_sklearn=True)
    # all 备用分支使用真实SYSU单元。
    d.sample(Namespace(dataset='sysu', seed=c.SEED, neg_ratio=1, pos_policy='all',
                        per_image_cap=8, max_pos=15000, out=out / 'sysu_all.jsonl'))
    all_rows = c.rows(out / 'sysu_all.jsonl')
    assert any(r['label'] == 1 and r['majority'] == 3 for r in all_rows)
    # 同一 cap=8 下，用明确较小max-pos验证SECOND三比例确有可执行配置。
    alternate = []
    for ratio in (1, 3, 5):
        dest = out / f'second_maxpos3000_r{ratio}.jsonl'
        d.sample(Namespace(dataset='second', seed=c.SEED, neg_ratio=ratio, pos_policy='ign3',
                            per_image_cap=8, max_pos=3000, out=dest))
        stats = c.read(dest.with_suffix('.stats.json'))
        assert stats['ratio_satisfied']
        alternate.append(stats['positives']+stats['negatives'])
    results['second_alternative_maxpos3000'] = dict(train_sizes=alternate, validated=True, selected_for_experiment=False)
    source = c.rows(c.HERE / 'check/render_source.jsonl')
    rendered = c.rows(c.HERE / 'check/rendered.jsonl')
    for original, new in zip(source, rendered, strict=True):
        for key in ('t1', 't2'):
            with Image.open(original['inputs'][key]) as a, Image.open(new['inputs'][key]) as b:
                assert np.array_equal(np.asarray(a), np.asarray(b))
    # 对同20条执行两片和合并，复用本任务已有图片以检查续跑。
    for shard in range(2):
        d.render(Namespace(manifest=c.HERE / 'check/render_source.jsonl', out=out / 'sharded.jsonl',
                            render_root=c.HERE / 'check/render', merge=False, shard=shard, shards=2))
        part = c.read((out / 'sharded.jsonl').with_suffix(f'.part-{shard:03d}.json'))
        assert part['written'] == 0
    d.render(Namespace(manifest=c.HERE / 'check/render_source.jsonl', out=out / 'sharded.jsonl',
                        render_root=c.HERE / 'check/render', merge=True, shard=0, shards=2))
    assert c.rows(out / 'sharded.jsonl') == rendered
    results['render'] = dict(candidates=20, images=40, pixel_equal=True, shard_merge_equal=True, resume_skipped=20)
    results['elapsed_seconds'] = time.monotonic()-started
    results['max_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    c.write(out / 'data_checks.json', results)
    return results


def processor_check(out):
    import exp6m_model as m
    processor, tokens = m.load_processor()
    results = {}
    for ds in ('sysu', 'second'):
        data = c.rows(c.HERE / 'check' / f'{ds}_train_smoke.jsonl')
        name, system = c.prompt(ds)
        result = m.dry_check(processor, tokens, data, system, 8192)
        batch, targets = m.pack(processor, tokens, data, system, 8192, answers=True, aug_seeds=[1, 2])
        assert (batch['labels'] != -100).sum(1).tolist() == [1, 1]
        result['batch_shape'] = list(batch['input_ids'].shape)
        result['prompt_name'] = name
        result['prompt_text'] = system
        # 同seed增强可重现；不同几何变换仍保持两张图尺寸一致。
        import numpy as np
        first = m.image_pair(data[0], random.Random(123))
        second = m.image_pair(data[0], random.Random(123))
        assert all(np.array_equal(np.asarray(x), np.asarray(y)) for x, y in zip(first, second, strict=True))
        result['augmentation_reproducible'] = True
        results[ds] = result
    index = c.read(c.MODEL / 'model.safetensors.index.json')['weight_map']
    leaves = ('q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj')
    names = [n for n in index if n.startswith('model.language_model.') and n.endswith('.weight')
             and n.split('.')[-2] in leaves]
    assert names and all('.visual.' not in n for n in names)
    results['lora_index_check'] = dict(target_linear_weights=len(names), leaves=dict(Counter(n.split('.')[-2] for n in names)),
                                      model_loaded=False)
    c.write(out / 'processor_checks.json', results)
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['data', 'processor', 'interfaces'])
    p.add_argument('--out', type=Path, default=c.HERE / 'check/verified')
    a = p.parse_args()
    out = c.local(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.stage == 'data':
        result = data_check(out)
    elif a.stage == 'processor':
        result = processor_check(out)
    else:
        result = {}
        for path in sorted(c.HERE.glob('exp6m_*.py')):
            ast.parse(path.read_text(), filename=str(path))
            result[path.name] = 'syntax passed'
        for name in ('data', 'train', 'infer', 'eval', 'curves', 'check'):
            command = [sys.executable, '-B', str(c.HERE / f'exp6m_{name}.py'), '--help']
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
            assert 'usage:' in completed.stdout
            result[f'{name}_help'] = 'passed'
        for name in ('cpu', 'gpu'):
            subprocess.run(['bash', '-n', str(c.HERE / f'run_exp6m_{name}.sbatch')], check=True)
            result[f'{name}_shell_syntax'] = 'passed'
        c.write(out / 'interfaces.json', result)
    print(c.json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
