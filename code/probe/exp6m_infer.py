"""基座加 LoRA 的单词概率推理；最终测试由固定登记文件限制为一次方案。"""
from __future__ import annotations
import argparse
from pathlib import Path
import time

import exp6m_common as c
import exp6m_model as m


def checkpoint_config(adapter):
    info = c.read(adapter / 'checkpoint.json')
    config = c.read(Path(info['run']) / 'config.json')
    if config['dataset'] != info['dataset']:
        raise ValueError('检查点数据集不符')
    return info, config


def configuration(a, data):
    info, training = checkpoint_config(a.adapter)
    if training['dataset'] != a.dataset:
        raise ValueError('不能跨数据集使用 LoRA')
    name, system = c.prompt(a.dataset)
    if training['prompt_text'] != system or training['question'] != c.QUESTION:
        raise ValueError('当前提示与训练时不同')
    if a.final:
        if a.frozen is None:
            raise ValueError('--final 必须指定 frozen.json')
        frozen = c.read(a.frozen)
        chosen = frozen['selected']
        if frozen['dataset'] != a.dataset or str(a.adapter.resolve()) != chosen['adapter']:
            raise ValueError('最终测试 LoRA 与验证冻结不符')
        if training != frozen['training_config']:
            raise ValueError('训练配置与冻结快照不符')
        if a.context_limit != chosen['context_limit'] or a.batch != chosen['batch']:
            raise ValueError('测试输入长度或批量配置与验证冻结不同')
        expected = c.rows(c.B49 / 'run' / f'exp6k_test_{a.dataset}.json')
        if data != expected:
            raise ValueError('最终测试必须完整使用原 exp6k 1500 条清单与原权重')
    elif any(r['split'] == 'test' for r in data):
        raise ValueError('测试清单只能配合 --final 与验证冻结运行')
    return dict(dataset=a.dataset, final=a.final, adapter=str(a.adapter.resolve()),
                train_run=info['run'], model=training['model'], manifest=str(a.manifest.resolve()),
                prompt_name=name, prompt_text=system, question=c.QUESTION, sample_order=data,
                batch=a.batch, context_limit=a.context_limit,
                frozen=c.read(a.frozen) if a.final else None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', choices=['sysu', 'second'], required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--adapter', type=Path, help='checkpoint-xxxxxx 目录；dry-run 可省略')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--batch', type=int, default=1)
    p.add_argument('--context-limit', type=int, default=8192)
    p.add_argument('--final', action='store_true')
    p.add_argument('--frozen', type=Path)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.batch < 1 or a.context_limit < 1:
        p.error('batch/context-limit 必须为正')
    if not a.dry_run:
        c.require_slurm()
        if a.adapter is None:
            p.error('模型推理必须指定 --adapter')
    data = c.rows(a.manifest)
    c.validate_manifest(data, a.dataset, 'test' if a.final else None, images=True)
    if any(r['split'] == 'test' for r in data) and not a.final:
        p.error('测试清单必须使用 --final')
    out = c.local(a.out)
    with c.lock(out / '.writer.lock', blocking=False):
        if a.dry_run:
            model_path = c.MODEL
            config = configuration(a, data) if a.adapter else None
            if config:
                model_path = config['model']
            processor, tokens = m.load_processor(model_path)
            _, system = c.prompt(a.dataset)
            c.write(out / 'dry_run.json', dict(candidates=len(data), final=a.final,
                        adapter_checked=a.adapter is not None, config=config,
                        checks=m.dry_check(processor, tokens, data, system, a.context_limit)))
            print('dry-run 通过；未加载模型、未登记最终测试')
            return
        config = configuration(a, data)
        if (out / 'config.json').exists() and c.read(out / 'config.json') != config:
            raise ValueError('续跑配置或清单不同')
        if a.final:
            c.claim_final(config, out)
        c.write(out / 'config.json', config)
        done = c.journal(out / 'predictions.jsonl')
        completed = {r['cid'] for r in done}
        if len(completed) != len(done) or not completed <= {r['cid'] for r in data}:
            raise ValueError('推理日志重复或含清单外样本')
        pending = [r for r in data if r['cid'] not in completed]
        if pending:
            import torch
            started = time.monotonic()
            processor, tokens = m.load_processor(config['model'])
            model = m.load_model(config['model'], adapter=a.adapter)
            torch.cuda.synchronize()
            c.append(out / 'loads.jsonl', dict(seconds=time.monotonic()-started, timestamp=c.now()))
            for start in range(0, len(pending), a.batch):
                subset = pending[start:start+a.batch]
                torch.cuda.synchronize()
                begin = time.monotonic()
                scores, losses, length = m.score_batch(model, processor, tokens, subset,
                                             config['prompt_text'], a.context_limit)
                torch.cuda.synchronize()
                elapsed = time.monotonic()-begin
                for row, score, ce in zip(subset, scores, losses, strict=True):
                    c.append(out / 'predictions.jsonl', dict(cid=row['cid'], image=row['image'],
                        unit_id=row['unit_id'], p_yes=score, loss=ce, answer='yes' if score > .5 else 'no',
                        seconds=elapsed/len(subset), batch_seconds=elapsed, batch_size=len(subset),
                        input_tokens=length, timestamp=c.now()))
        predictions = c.journal(out / 'predictions.jsonl')
        c.write(out / 'status.json', dict(complete=len(predictions) == len(data), candidates=len(predictions),
                    seconds_per_candidate=sum(r['seconds'] for r in predictions)/len(predictions),
                    inference_seconds=sum(r['seconds'] for r in predictions)))


if __name__ == '__main__':
    main()
