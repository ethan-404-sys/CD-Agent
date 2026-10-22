"""SECOND/SYSU 对象级去误检 MLP；沿用 batch22 的训练与早停。"""
from __future__ import annotations

import argparse
import json
import random
import time
import uuid

import fp_common as c
import numpy as np
import torch
from torch import nn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    c.arguments(parser)
    parser.add_argument('--dataset', choices=('second', 'sysu'), required=True)
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--patience', type=int, default=7)
    parser.add_argument('--batch-size', type=int, default=2048)
    parser.add_argument('--seed', type=int, default=c.old.SEED)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size) < 1:
        parser.error('epochs/patience/batch-size 必须为正')
    c.initialize(args)
    c.require_reference(args)
    dest = args.out / 'models' / args.dataset
    dest.mkdir(parents=True, exist_ok=True)
    if any((dest / p).exists() for p in ('model.pt', 'training.json', 'train_log.jsonl')):
        raise FileExistsError(f'{dest}: 已开始训练；中断后请使用新输出目录')
    pools = {s: c.Pool(args, args.dataset, s) for s in ('train', 'val')}
    features = {s: c.Features(p) for s, p in pools.items()}
    selected = {s: p.label >= 0 for s, p in pools.items()}
    for split, pool in pools.items():
        if len(np.unique(pool.label[selected[split]])) != 2:
            raise ValueError(f'{split}: 监督集合必须同时含正负类别')
    positive = int((pools['train'].label[selected['train']] == 1).sum())
    negative = int((pools['train'].label[selected['train']] == 0).sum())
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    rng = np.random.default_rng(args.seed)
    mean, std = c.old.normalization(features['train'], selected['train'], args.batch_size)
    model = c.old.classifier(c.Features.input_dim, .15)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    weight = negative / positive
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight))
    best_ap, best_epoch, best_state = -1., 0, None
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    with (dest / 'train_log.jsonl').open('w') as log:
        for epoch in range(1, args.epochs + 1):
            model.train()
            total, count = 0., 0
            for idx, x in features['train'].batches(selected['train'], args.batch_size, rng):
                y = torch.from_numpy(pools['train'].label[idx].astype(np.float32))
                optimizer.zero_grad(set_to_none=True)
                loss = criterion(model(torch.from_numpy((x - mean) / std)).squeeze(1), y)
                if not torch.isfinite(loss):
                    raise ValueError('训练 loss 非有限')
                loss.backward()
                optimizer.step()
                total += float(loss.detach()) * len(idx)
                count += len(idx)
            values = {}
            for split, pool in pools.items():
                take = selected[split]
                score = c.old.predict(model, features[split], take, mean, std, args.batch_size, 'cpu')
                values[split] = c.old.binary_metrics(pool.label[take], score[take])
                values[split]['loss'] = c.weighted_bce(pool.label[take], score[take], weight)
            if values['val']['ap'] > best_ap:
                best_ap, best_epoch = values['val']['ap'], epoch
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            row = dict(epoch=epoch, train_optimization_loss=total/count, **values,
                       best_epoch=best_epoch, seconds=time.monotonic() - started)
            log.write(json.dumps(row, allow_nan=False) + '\n')
            log.flush()
            print(f'{args.dataset} epoch={epoch}, val AP={values["val"]["ap"]:.6f}', flush=True)
            if epoch - best_epoch >= args.patience:
                break
    model.load_state_dict(best_state)
    score = c.old.predict(model, features['val'], np.ones(len(pools['val']), bool),
                          mean, std, args.batch_size, 'cpu')
    for name, sl in pools['val'].slices.items():
        c.old.save_npz(dest / 'validation_scores' / (c.Path(name).stem + '.npz'),
                       score=score[sl], run_id=run_id, image=name)
    if not args.limit:
        temporary = dest / 'model.tmp.pt'
        torch.save(dict(state_dict=best_state, mean=torch.from_numpy(mean), std=torch.from_numpy(std),
                        run_id=run_id, protocol=c.protocol(args)), temporary)
        temporary.replace(dest / 'model.pt')
    c.write_json(dest / 'training.json', dict(protocol=c.protocol(args), run_id=run_id,
                 train_images=pools['train'].images, val_images=pools['val'].images,
                 seed=args.seed, epochs_run=epoch, best_epoch=best_epoch, best_val_ap=best_ap,
                 checkpoint_selection='validation AP; batch22 early stopping',
                 patience=args.patience, batch_size=args.batch_size, lr=.001, weight_decay=.0001,
                 dropout=.15, optimizer='AdamW', positive=positive, negative=negative,
                 pos_weight=weight, train_monitor='all supervised units; eval mode',
                 checkpoint_saved=not bool(args.limit), seconds=time.monotonic() - started))


if __name__ == '__main__':
    main()
