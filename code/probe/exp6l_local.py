"""Single-GPU multiround scoring using batch45 model loaders and yes/no token readout."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
import time
import traceback

import exp6l_common as c


class ContextLimit(ValueError):
    def __init__(self, length, limit):
        super().__init__(f'input_tokens={length} exceeds context_limit={limit}; no truncation')
        self.length, self.limit = length, limit


def load_backend(name):
    sys.path.insert(0, str(c.HERE.parent / 'batch45'))
    import exp6j_models as upstream
    from exp6j_common import readout
    # The upstream loader sets cache locations under batch45 on import; keep all new files here.
    for key in ('HF_HOME', 'HF_MODULES_CACHE', 'MPLCONFIGDIR', 'XDG_CACHE_HOME'):
        os.environ[key] = str(c.HERE / '.cache' / key.lower())
    upstream.ROOT = c.HERE
    model = upstream.load_model(name)
    return model, upstream.dynamic_preprocess, readout


def score(model, tiler, readout, msgs, context_limit):
    from PIL import Image
    images, converted = [], []
    for msg in msgs:
        content = msg['content']
        if isinstance(content, list):
            parts = []
            for part in content:
                if part['type'] == 'image':
                    with Image.open(part['path']) as source:
                        im = source.convert('RGB')
                    images.append(im)
                    parts.append(dict(type='image', image=im))
                else:
                    parts.append(dict(type='text', text=part['text']))
            content = parts
        converted.append(dict(role=msg['role'], content=content))
    torch = model.torch
    limit = context_limit or min(int(model.tokenizer.model_max_length),
                                 262144 if model.metadata['backend'] == 'qwen' else 8192)
    with torch.inference_mode():
        if model.metadata['backend'] == 'qwen':
            text = model.processor.apply_chat_template(converted, tokenize=False,
                add_generation_prompt=True, enable_thinking=False) + c.PREFIX
            batch = model.processor(text=[text], images=images, return_tensors='pt', truncation=False)
            length = int(batch['input_ids'].shape[1])
            if length > limit:
                raise ContextLimit(length, limit)
            batch = batch.to('cuda:0')
            logits = model.model(**batch, use_cache=False, logits_to_keep=1).logits[0, -1]
            result = readout(logits, model.tokens)
            result['image_grid_thw'] = batch['image_grid_thw'].tolist()
        else:
            conv = model.get_template(model.config.template)
            conv.system_message = msgs[0]['content']
            for msg in msgs[1:]:
                content = msg['content']
                text = '\n'.join('<image>' if part['type'] == 'image' else part['text']
                                 for part in content) if isinstance(content, list) else content
                conv.append_message(conv.roles[0] if msg['role'] == 'user' else conv.roles[1], text)
            conv.append_message(conv.roles[1], None)
            text = conv.get_prompt() + c.PREFIX
            tiles = [tiler(im, max_num=model.max_num, image_size=model.size,
                           use_thumbnail=model.use_thumbnail) for im in images]
            patches = [len(t) for t in tiles]
            for n in patches:
                text = text.replace('<image>', '<img>' + '<IMG_CONTEXT>' * (model.model.num_image_token*n) + '</img>', 1)
            batch = model.tokenizer(text, return_tensors='pt', truncation=False)
            length = int(batch['input_ids'].shape[1])
            if length > limit:
                raise ContextLimit(length, limit)
            batch = batch.to('cuda:0')
            pixels = torch.stack([model.transform(im) for group in tiles for im in group]).to(
                device='cuda:0', dtype=torch.bfloat16)
            features = model.model.extract_feature(pixels)
            embeddings = model.llm.get_input_embeddings()(batch['input_ids']).clone()
            selected = batch['input_ids'] == model.image_token
            visual = features.reshape(-1, embeddings.shape[-1]).to(embeddings.dtype)
            if int(selected.sum()) != visual.shape[0]:
                raise ValueError('Visual features disagree with image token slots')
            embeddings[selected] = visual
            positions = torch.arange(length, device='cuda:0').unsqueeze(0)
            output = model.llm(inputs_embeds=embeddings, attention_mask=batch['attention_mask'],
                               position_ids=positions, use_cache=False, return_dict=True)
            result = readout(output.logits[0, -1], model.tokens)
            result['num_patches_list'] = patches
        torch.cuda.synchronize()
    return dict(**result, input_tokens=length, context_limit=limit,
                answer='yes' if result['p_yes'] > .5 else 'no', readout='token_prob')


def run_configuration(a, get_backend):
    config = c.configuration(a, a.model)
    config.update(context_limit=a.context_limit, shards=a.shards)
    if a.plan is not None and (a.variant, a.k) != a.planned_setting:
        raise ValueError('Plan setting differs from frozen configuration')
    # A shared run config; each shard has an independent lock and result journal.
    out = c.local(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (out / '.config.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (out / 'config.json').exists() and c.read(out / 'config.json') != config:
            raise ValueError('Resume configuration changed')
        c.write(out / 'config.json', config)
    if a.dry_run:
        c.dry_run(config, out, a.limit)
        return 0
    c.claim_final(config, a.out)
    with (out / f'.part-{a.shard:03d}.lock').open('a') as shard_lock:
        fcntl.flock(shard_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            return run_pending(a, config, out, get_backend)
        except Exception as exc:
            if a.plan is not None:
                c.write(out / f'status-{a.shard:03d}.json',
                        dict(complete=False, failures=1, error=str(exc)))
            raise


def run_pending(a, config, out, get_backend):
    path = out / f'part-{a.shard:03d}.jsonl'
    prior = c.journal(path)
    # Context/OOM failures are terminal for this configuration; changed limits need a new run.
    done = {cid for row in prior for cid in row['cids']}
    groups = [g for i, g in enumerate(c.groups(config)) if i % a.shards == a.shard]
    pending = [g for g in groups if g[0]['cid'] not in done]
    if not pending:
        return 2 if any(r['status'] != 'ok' for r in prior) else 0
    model, tiler, readout, loaded = get_backend()
    c.write(out / f'engine-{a.shard:03d}.json', dict(**model.metadata, load_seconds=loaded,
                                                context_limit_override=a.context_limit))
    c.append(out / f'loads-{a.shard:03d}.jsonl', dict(load_seconds=loaded))
    failures = sum(r['status'] != 'ok' for r in prior)
    for group in pending:
        t0 = time.monotonic()
        row = dict(group_key=group[0]['cid'], cids=[group[0]['cid']])
        try:
            result = score(model, tiler, readout, c.messages(config, group), a.context_limit)
            result['cid'] = group[0]['cid']
            row.update(status='ok', results=[result], raw_text=None,
                       usage=dict(input_tokens=result['input_tokens'], output_tokens=0, cached_tokens=0, cost=0))
        except ContextLimit as exc:
            row.update(status='failed', error=str(exc), input_tokens=exc.length, context_limit=exc.limit)
            failures += 1
        except model.torch.cuda.OutOfMemoryError:
            model.torch.cuda.empty_cache()
            row.update(status='failed', error='cuda_out_of_memory')
            failures += 1
        row.update(seconds=time.monotonic()-t0)
        c.append(path, row)
    c.write(out / f'status-{a.shard:03d}.json', dict(complete=not failures, failures=failures,
                                                 peak_gpu_gib=model.peak_memory()))
    return 2 if failures else 0


def plan_arguments(p, a):
    if a.plan is None:
        if a.out_template is not None:
            p.error('--out-template requires --plan')
        if a.dataset is None or a.out is None:
            p.error('Single configuration requires --dataset and --out')
        return [a]
    if not a.out_template:
        p.error('--plan requires --out-template')
    if a.dataset is not None or a.out is not None or a.variant != 'Z1' or a.k:
        p.error('--plan supplies dataset/variant/k/out; do not combine their single-run options')
    settings = {'Z0': ('Z0', 0), 'Z1': ('Z1', 0),
                **{f'F{k}': ('F', k) for k in c.KS if k}}
    runs, outputs = [], set()
    for item in a.plan.split(','):
        dataset, sep, setting = item.strip().partition(':')
        if not sep or dataset not in ('sysu', 'second') or setting not in settings:
            p.error(f'Invalid plan item {item!r}; expected sysu|second:Z0|Z1|F4|F8|F12|F16')
        try:
            out = c.local(a.out_template.format(model=a.model, dataset=dataset, setting=setting))
        except (KeyError, ValueError, IndexError, AttributeError) as exc:
            p.error(f'Invalid --out-template: {exc}')
        if out in outputs:
            p.error(f'Plan output directories must be distinct: {out}')
        outputs.add(out)
        run = argparse.Namespace(**vars(a))
        run.dataset, run.out = dataset, out
        run.variant, run.k = settings[setting]
        run.planned_setting = settings[setting]
        runs.append(run)
    return runs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    c.add_arguments(p, required=False)
    p.add_argument('--model', choices=['qwen', 'dars4b', 'earthdial'], required=True)
    p.add_argument('--plan', help='Ordered configurations, e.g. sysu:Z0,sysu:F4,second:Z1')
    p.add_argument('--out-template', help='Per-configuration output with {model}, {dataset}, {setting}')
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--context-limit', type=int, help='Explicit override, recorded; never truncates inputs')
    a = p.parse_args()
    if a.shards < 1 or not 0 <= a.shard < a.shards:
        p.error('Invalid candidate shard')
    if a.context_limit is not None and a.context_limit < 1:
        p.error('Invalid context limit')
    runs = plan_arguments(p, a)
    if not a.dry_run and not os.environ.get('SLURM_JOB_ID'):
        p.error('Formal inference must run in a Slurm job')
    backend = None

    def get_backend():
        nonlocal backend
        loaded = 0
        if backend is None:
            started = time.monotonic()
            backend = load_backend(a.model)
            loaded = time.monotonic() - started
        else:
            backend[0].torch.cuda.reset_peak_memory_stats()
        return (*backend, loaded)

    if a.plan is None:
        return run_configuration(a, get_backend)
    failed = False
    for run in runs:
        print(f'Running {run.dataset}:{run.variant}{run.k if run.k else ""} -> {run.out}', flush=True)
        try:
            code = run_configuration(run, get_backend)
        except Exception as exc:
            traceback.print_exc()
            # Keep setup errors separate so an incompatible resume cannot overwrite its status.
            c.write(run.out / f'batch-error-{run.shard:03d}.json',
                    dict(complete=False, error=str(exc), dataset=run.dataset,
                         variant=run.planned_setting[0], k=run.planned_setting[1]))
            code = 2
        if code:
            print(f'Failed configuration: {run.out}', file=sys.stderr, flush=True)
            failed = True
    return 2 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
