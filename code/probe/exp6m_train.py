"""单 GPU LoRA、单词元损失、验证早停、完整曲线与优化步续跑。"""
from __future__ import annotations
import argparse
import math
from pathlib import Path
import random
import shutil
import time

import exp6m_common as c
import exp6m_model as m


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', choices=['sysu', 'second'], required=True)
    p.add_argument('--train', type=Path, required=True)
    p.add_argument('--val', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', type=Path, default=c.MODEL)
    p.add_argument('--rank', type=int, default=16)
    p.add_argument('--alpha', type=int, default=32)
    p.add_argument('--dropout', type=float, default=.05)
    p.add_argument('--lr', type=float, default=1e-4)
    p.add_argument('--warmup', type=float, default=.03)
    p.add_argument('--batch', type=int, default=1)
    p.add_argument('--grad-accum', type=int, default=32)
    p.add_argument('--epochs', type=int, default=3)
    p.add_argument('--seed', type=int, default=c.SEED)
    p.add_argument('--eval-every', type=int, default=100)
    p.add_argument('--save-every', type=int, default=50)
    p.add_argument('--patience', type=int, default=3)
    p.add_argument('--max-steps', type=int, default=0)
    p.add_argument('--stop-at', type=int, default=0,
                   help='到此步评估、保存并退出；学习率仍按完整 epochs 计划，之后可 --resume 续跑')
    p.add_argument('--context-limit', type=int, default=8192)
    p.add_argument('--max-grad-norm', type=float, default=1.)
    p.add_argument('--gradient-checkpointing', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--timing', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='只加载 processor，使用真实图片检查模板与答案掩码')
    return p


def evaluate(model, processor, tokens, data, config, step, epoch):
    import numpy as np
    model.eval()
    predictions, losses = [], []
    for start in range(0, len(data), config['batch']):
        subset = data[start:start+config['batch']]
        scores, ce, _ = m.score_batch(model, processor, tokens, subset, config['prompt_text'], config['context_limit'])
        predictions.extend(dict(cid=r['cid'], p_yes=float(s), loss=float(l))
                           for r, s, l in zip(subset, scores, ce, strict=True))
        losses.extend(ce)
    scores = [r['p_yes'] for r in predictions]
    metrics = c.upstream_eval().ranking(data, scores, n_boot=0)
    weights = np.array([r['weight'] for r in data])
    rates = {}
    for stratum in c.STRATA:
        ids = [i for i, r in enumerate(data) if r['stratum'] == stratum]
        rates[stratum] = float(np.average(np.asarray(scores)[ids] > .5, weights=weights[ids])) if ids else None
    log = dict(step=step, epoch=epoch, val_loss=float(np.average(losses, weights=weights)),
               val_auc2=metrics['primary_auc'], val_ap2=metrics['primary_ap'],
               all_auc=metrics['all_auc'], all_ap=metrics['all_ap'], yes_rates=rates, timestamp=c.now())
    model.train()
    model.get_base_model().model.visual.eval()
    return log, predictions


def save_checkpoint(out, model, optimizer, scheduler, state):
    import numpy as np
    import torch
    step = state['step']
    destination = out / f'checkpoint-{step:06d}'
    temp = out / f'.checkpoint-{step:06d}.tmp'
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir()
    model.save_pretrained(temp, safe_serialization=True)
    payload = dict(state, optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(),
                   python_rng=random.getstate(), numpy_rng=np.random.get_state(),
                   torch_rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all())
    torch.save(payload, temp / 'training_state.pt')
    c.write(temp / 'checkpoint.json', dict(step=step, run=str(out), dataset=state['dataset']))
    if destination.exists():
        raise ValueError(f'检查点已存在: {destination}')
    temp.replace(destination)
    c.write(out / 'latest.json', dict(checkpoint=str(destination), step=step))
    return destination


def train(a, config, train_rows, val_rows):
    import numpy as np
    import torch
    from transformers import get_cosine_schedule_with_warmup
    out = c.local(a.out)
    random.seed(a.seed)
    np.random.seed(a.seed)
    torch.manual_seed(a.seed)
    torch.cuda.manual_seed_all(a.seed)
    processor, tokens = m.load_processor(a.model)
    resume_path = Path(c.read(out / 'latest.json')['checkpoint']) if a.resume else None
    model = m.load_model(a.model, adapter=resume_path, train=True, rank=a.rank,
                         alpha=a.alpha, dropout=a.dropout, checkpointing=a.gradient_checkpointing)
    params = [p for p in model.parameters() if p.requires_grad]
    c.write(out / 'model_info.json', dict(trainable_parameters=sum(p.numel() for p in params),
                 trainable_names=[name for name, p in model.named_parameters() if p.requires_grad],
                 answer_tokens=tokens, torch=torch.__version__))
    optimizer = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.)
    per_epoch = math.ceil(len(train_rows) / (a.batch * a.grad_accum))
    total = a.max_steps if a.timing else min(a.epochs * per_epoch, a.max_steps or a.epochs * per_epoch)
    scheduler = get_cosine_schedule_with_warmup(optimizer, math.ceil(total*a.warmup), total)
    state = dict(step=0, dataset=a.dataset, best_ap=None, best_step=None, bad_evals=0, stopped=False)
    if resume_path:
        loaded = torch.load(resume_path / 'training_state.pt', map_location='cpu', weights_only=False)
        optimizer.load_state_dict(loaded.pop('optimizer'))
        scheduler.load_state_dict(loaded.pop('scheduler'))
        random.setstate(loaded.pop('python_rng'))
        np.random.set_state(loaded.pop('numpy_rng'))
        torch.set_rng_state(loaded.pop('torch_rng'))
        torch.cuda.set_rng_state_all(loaded.pop('cuda_rng'))
        state = loaded
        # 日志中尚未纳入已保存状态的优化步重做，曲线不保留重复步。
        for name in ('train_log.jsonl', 'val_log.jsonl'):
            c.write_rows(out / name, [r for r in c.journal(out / name) if r['step'] <= state['step']])
        if state['best_step'] is None:
            (out / 'best.json').unlink(missing_ok=True)
        else:
            c.write(out / 'best.json', dict(checkpoint=str(out/f'checkpoint-{state["best_step"]:06d}'),
                                           step=state['best_step'], val_ap2=state['best_ap']))
    torch.cuda.reset_peak_memory_stats()
    wall_start = time.monotonic()
    while state['step'] < total and not state['stopped']:
        epoch_index, within = divmod(state['step'], per_epoch)
        order = list(range(len(train_rows)))
        random.Random(a.seed + epoch_index).shuffle(order)
        start = within * a.batch * a.grad_accum
        group = order[start:start+a.batch*a.grad_accum]
        torch.cuda.synchronize()
        t0 = time.monotonic()
        optimizer.zero_grad(set_to_none=True)
        total_loss = 0.
        used_lr = float(optimizer.param_groups[0]['lr'])
        for offset in range(0, len(group), a.batch):
            ids = group[offset:offset+a.batch]
            subset = [train_rows[j] for j in ids]
            seeds = [a.seed + epoch_index * len(train_rows) + j for j in ids]
            batch, _ = m.pack(processor, tokens, subset, config['prompt_text'], a.context_limit,
                              answers=True, aug_seeds=seeds)
            batch = batch.to('cuda:0')
            loss = m.answer_loss(model, batch)
            fraction = len(ids)/len(group)
            (loss * fraction).backward()
            total_loss += float(loss.detach()) * fraction
        norm = torch.nn.utils.clip_grad_norm_(params, a.max_grad_norm, error_if_nonfinite=True)
        optimizer.step()
        scheduler.step()
        torch.cuda.synchronize()
        seconds = time.monotonic()-t0
        state['step'] += 1
        step = state['step']
        epoch = epoch_index + min(start+len(group), len(train_rows))/len(train_rows)
        c.append(out / 'train_log.jsonl', dict(step=step, epoch=epoch, loss=total_loss, lr=used_lr,
                     grad_norm=float(norm), samples_per_sec=len(group)/seconds, step_seconds=seconds,
                     samples=len(group), gpu_mem_gib=torch.cuda.max_memory_allocated()/1024**3, timestamp=c.now()))
        stop_here = bool(a.stop_at) and step >= a.stop_at
        need_eval = not a.timing and (step % a.eval_every == 0 or step == total or stop_here)
        improved = False
        if need_eval:
            val_log, pred = evaluate(model, processor, tokens, val_rows, config, step, epoch)
            if val_log['val_ap2'] is None:
                raise ValueError('验证② AP 不可计算')
            c.append(out / 'val_log.jsonl', val_log)
            improved = state['best_ap'] is None or val_log['val_ap2'] > state['best_ap']
            if improved:
                state.update(best_ap=val_log['val_ap2'], best_step=step, bad_evals=0)
            else:
                state['bad_evals'] += 1
            state['stopped'] = state['bad_evals'] >= a.patience
        if not a.timing and (need_eval or step % a.save_every == 0 or step == total):
            checkpoint = save_checkpoint(out, model, optimizer, scheduler, state)
            if need_eval:
                prediction_dir = out / f'validation-{step:06d}'
                c.write(prediction_dir / 'config.json', dict(dataset=a.dataset, final=False,
                    adapter=str(checkpoint), train_run=str(out), model=config['model'],
                    manifest=config['val'], sample_order=val_rows, prompt_text=config['prompt_text'],
                    context_limit=a.context_limit, question=c.QUESTION, batch=a.batch))
                c.write_rows(prediction_dir / 'predictions.jsonl', pred)
                c.write(prediction_dir / 'metrics.json', val_log)
            if improved:
                c.write(out / 'best.json', dict(checkpoint=str(checkpoint), step=step, val_ap2=state['best_ap']))
        if step % 10 == 0:
            print(f'step={step}/{total} loss={total_loss:.5f} seconds={seconds:.3f}', flush=True)
        if stop_here and not state['stopped']:
            print(f'stop-at {a.stop_at}: checkpoint saved, schedule total {total}', flush=True)
            break
    if a.timing:
        logs = c.rows(out / 'train_log.jsonl')
        measured = [r['step_seconds'] for r in logs if r['step'] > 10]
        c.write(out / 'timing.json', dict(steps=len(logs), warmup_steps=10, measured_steps=len(measured),
                     seconds_per_step=float(np.mean(measured)), step_seconds=measured,
                     median_seconds=float(np.median(measured)), peak_gpu_gib=torch.cuda.max_memory_allocated()/1024**3,
                     effective_batch=a.batch*a.grad_accum, train_count=len(train_rows),
                     steps_per_epoch=per_epoch, epochs=a.epochs,
                     training_gpu_hours_estimate=float(np.mean(measured))*per_epoch*a.epochs/3600,
                     estimate_excludes_validation=True, elapsed_seconds=time.monotonic()-wall_start))
    paused = bool(a.stop_at) and state['step'] < total and not state['stopped']
    c.write(out / 'status.json', dict(state, complete=not paused, paused_at=state['step'] if paused else None, timing=a.timing, elapsed_seconds=time.monotonic()-wall_start))


def main():
    p = parser()
    a = p.parse_args()
    if min(a.batch, a.grad_accum, a.epochs, a.rank, a.alpha, a.eval_every, a.save_every, a.patience) < 1:
        p.error('训练计数参数必须为正')
    if not 0 <= a.dropout < 1 or not 0 <= a.warmup < 1 or a.lr <= 0 or a.max_steps < 0 or a.context_limit < 1 or a.max_grad_norm <= 0:
        p.error('训练超参数无效')
    if a.stop_at < 0 or (a.timing and a.stop_at):
        p.error('--stop-at 须为非负，且不用于 timing')
    if a.timing and (a.max_steps <= 10 or a.resume):
        p.error('timing 要求 --max-steps > 10 且不支持 resume；不保存 checkpoint')
    if not a.timing and a.val is None:
        p.error('正式训练必须提供 --val')
    if not a.dry_run:
        c.require_slurm()
    train_rows = c.rows(a.train)
    val_rows = c.rows(a.val) if a.val else []
    c.validate_manifest(train_rows, a.dataset, 'train', images=not a.dry_run)
    n_pos = sum(r['label'] for r in train_rows)
    ratio = (len(train_rows)-n_pos)/n_pos if n_pos else None
    if ratio not in (1., 3., 5.):
        raise ValueError(f'训练清单实际负正比例为 {ratio}；必须先完成1/3/5比例采样')
    stats_path = a.train.with_suffix('.stats.json')
    sampling = c.read(stats_path) if stats_path.exists() else None
    if sampling and sampling.get('ratio_satisfied') is False:
        raise ValueError('采样存在负例缺口，不启动训练；先调整采样参数')
    if val_rows:
        c.validate_manifest(val_rows, a.dataset, 'val', images=not a.dry_run)
    name, system = c.prompt(a.dataset)
    config = {k: str(v.resolve()) if isinstance(v, Path) else v for k, v in vars(a).items()
              if k not in ('resume', 'dry_run', 'stop_at', 'eval_every')}
    config.update(prompt_name=name, prompt_text=system, question=c.QUESTION,
                  train_count=len(train_rows), val_count=len(val_rows),
                  negative_positive_ratio=ratio, sampling=sampling,
                  train_sample_order=train_rows, val_sample_order=val_rows,
                  scheduler='cosine', dtype='bfloat16', effective_batch=a.batch*a.grad_accum,
                  optimizer='AdamW', weight_decay=0., adam_betas=[.9, .999], adam_eps=1e-8,
                  augmentation='同步水平/垂直翻转、均匀0/90/180/270度旋转；0.5交换两期；无颜色增强')
    out = c.local(a.out)
    with c.lock(out / '.writer.lock', blocking=False):
        if a.dry_run:
            processor, tokens = m.load_processor(a.model)
            c.write(out / 'dry_run.json', dict(config=config,
                       checks=m.dry_check(processor, tokens, train_rows, system, a.context_limit)))
            print('dry-run 通过；未加载模型')
            return
        if a.resume:
            if c.read(out / 'config.json') != config:
                raise ValueError('续跑配置或清单改变')
        elif (out / 'config.json').exists():
            raise ValueError('训练目录已存在；使用 --resume 或新目录')
        c.write(out / 'config.json', config)
        # 分段训练：每段的评估间隔与停止步单独记录（不进入续跑一致性检查）。
        c.append(out / 'phases.jsonl', dict(resume=a.resume, eval_every=a.eval_every, stop_at=a.stop_at, timestamp=c.now()))
        train(a, config, train_rows, val_rows)


if __name__ == '__main__':
    main()
