"""Single-GPU bf16 forward scoring, sharded append-only resume, no generation/training."""
from __future__ import annotations
import argparse
import fcntl
import json
import time
from exp6j_common import *


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', choices=MODEL_PATHS, required=True)
    p.add_argument('--stage', choices=('step1', 'step2'), required=True)
    p.add_argument('--datasets', nargs='+', choices=('levir', 'whu', 'sysu', 'second'))
    p.add_argument('--view', choices=('near', 'context'), default='near')
    p.add_argument('--packing', choices=('multi', 'sheet'), default='multi')
    p.add_argument('--out', type=Path, default=RUN)
    shard_args(p)
    args = p.parse_args()
    args.out = local(args.out)
    check_shard(args)
    if args.stage == 'step1' and args.view != 'near':
        p.error('step1 reuses the original near inputs')
    datasets = args.datasets or (['levir', 'whu', 'sysu', 'second'] if args.stage == 'step1' else ['sysu', 'second'])
    if args.stage == 'step2' and not set(datasets) <= {'sysu', 'second'}:
        p.error('step2 datasets must be sysu/second')
    from exp6j_models import load_model
    import torch
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('One visible bf16-capable GPU is required')
    if torch.cuda.device_count() != 1:
        raise RuntimeError('Allocate exactly one GPU per Slurm job')
    started = time.perf_counter()
    engine = load_model(args.model)
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started
    for dataset in datasets:
        rows = samples(args.out, args.stage, dataset)
        prompt = prompt_for(dataset, args.stage, args.view)
        folder = local(prediction_dir(args.out, args.stage, dataset, args.model, args.view, args.packing))
        folder.mkdir(parents=True, exist_ok=True)
        config = dict(model=args.model, stage=args.stage, dataset=dataset, view=args.view,
                      packing=args.packing, shards=args.shards, prompt=prompt, samples=rows,
                      engine=engine.metadata, readout='contextual_forced_prefix_forward_v1')
        with (folder / 'config.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = folder / 'config.json'
            if path.exists() and read_json(path) != config:
                raise ValueError(f'{folder}: run configuration changed; use a different --out')
            if not path.exists():
                write_json(path, config)
        assigned = rows[args.shard::args.shards]
        chosen = assigned[:args.limit or None]
        path = folder / f'part-{args.shard:03d}.jsonl'
        with (folder / f'part-{args.shard:03d}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            done = resume_rows(path)
            if not set(done) <= set(indexed(assigned)):
                raise ValueError('Journal contains IDs outside this shard')
            with path.open('a', buffering=1) as f:
                for r in chosen:
                    if r['cid'] in done:
                        continue
                    torch.cuda.reset_peak_memory_stats()
                    torch.cuda.synchronize()
                    t = time.perf_counter()
                    images = input_images(r, args.view, args.packing)
                    score = engine.score(images, prompt)
                    seconds = time.perf_counter() - t
                    value = dict(cid=r['cid'], **score, seconds=seconds, peak_gib=engine.peak_memory())
                    f.write(json.dumps(value, allow_nan=False) + '\n')
                    f.flush()
                    done[r['cid']] = value
                    print(dataset, args.model, r['cid'], f'{seconds:.2f}s', flush=True)
        write_json(folder / f'timing-{args.shard:03d}.json', dict(
            model_load_seconds=load_seconds, completed=len(done), expected=len(assigned),
            complete=len(done) == len(assigned), seconds=sum(r['seconds'] for r in done.values()),
            peak_gib=max((r['peak_gib'] for r in done.values()), default=0)))

if __name__ == '__main__':
    main()
