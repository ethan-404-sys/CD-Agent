"""Train full/clean V1; smoke records curves and validation scores but no checkpoint."""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import uuid

import exp8_config as cfg
from exp8_features import Features, Pool, cleaned_selection, prepare
import numpy as np
import torch
from torch import nn

old, rt = cfg.old, cfg.rt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cfg.arguments(parser)
    parser.add_argument('--variant', choices=('full', 'clean'), required=True)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--patience', type=int, default=7)
    parser.add_argument('--batch-size', type=int, default=2048)
    parser.add_argument('--seed', type=int, default=old.SEED)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size) < 1:
        parser.error('epochs, patience, batch-size must be positive')
    config = cfg.protocol(args)
    cfg.b20.write_fixed(args.out / 'protocol.json', config)
    dest = args.out / 'models' / args.variant
    dest.mkdir(parents=True, exist_ok=True)
    if any((dest / name).exists() for name in ('model.pt', 'train_log.jsonl', 'training.json')):
        raise FileExistsError(f'{dest}: training already started; use a new output root')
    for split in ('train', 'val'):
        prepare(split, args)
    train_pool, val_pool = Pool('train', args), Pool('val', args)
    train_features, val_features = Features(train_pool), Features(val_pool)
    train = train_pool.label >= 0
    cleaning = None
    if args.variant == 'clean':
        train, cleaning = cleaned_selection(train_pool, args)
    val = val_pool.label >= 0
    positive, negative = int((train_pool.label[train] == 1).sum()), int((train_pool.label[train] == 0).sum())
    if min(positive, negative) == 0 or len(np.unique(val_pool.label[val])) != 2:
        raise ValueError('Train and validation must each contain both labels')
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    rng = np.random.default_rng(args.seed)
    mean, std = old.normalization(train_features, train, args.batch_size)
    model = old.classifier(train_features.input_dim, .15).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    pos_weight = negative / positive
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight, device=args.device))
    best_ap, best_epoch, best_state = -1., 0, None
    run_id = str(uuid.uuid4())
    start = time.monotonic()
    with (dest / 'train_log.jsonl').open('w') as log:
        for epoch in range(1, args.epochs + 1):
            model.train()
            total, count = 0., 0
            for idx, x in train_features.batches(train, args.batch_size, rng):
                tensor = torch.from_numpy((x - mean) / std).to(args.device)
                labels = torch.from_numpy(train_pool.label[idx].astype(np.float32)).to(args.device)
                optimizer.zero_grad(set_to_none=True)
                loss = criterion(model(tensor).squeeze(1), labels)
                if not torch.isfinite(loss):
                    raise ValueError('Non-finite training loss')
                loss.backward()
                optimizer.step()
                total += float(loss.detach()) * len(idx)
                count += len(idx)
            metrics = {}
            for key, pool, features, take in (('train', train_pool, train_features, train),
                                              ('val', val_pool, val_features, val)):
                scores = old.predict(model, features, take, mean, std, args.batch_size, args.device)
                metrics[key] = old.binary_metrics(pool.label[take], scores[take])
                metrics[key]['loss'] = cfg.weighted_bce(pool.label[take], scores[take], pos_weight)
            if metrics['val']['ap'] > best_ap:
                best_ap, best_epoch = metrics['val']['ap'], epoch
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            row = dict(epoch=epoch, train_optimization_loss=total / count, **metrics,
                       best_epoch=best_epoch, seconds=time.monotonic() - start)
            log.write(json.dumps(row, allow_nan=False) + '\n')
            log.flush()
            print(f'{args.variant} epoch {epoch}: train loss={total/count:.5f}, val AP={metrics["val"]["ap"]:.5f}', flush=True)
            if epoch - best_epoch >= args.patience:
                break
    model.load_state_dict(best_state)
    scores = old.predict(model, val_features, np.ones(len(val_pool), bool), mean, std,
                         args.batch_size, args.device)
    old.save_npz(dest / 'validation_scores.npz', **val_pool.score_arrays(scores), run_id=run_id)
    if not args.limit:
        checkpoint = dict(run_id=run_id, state_dict=best_state, mean=torch.from_numpy(mean),
                          std=torch.from_numpy(std), input_dim=train_features.input_dim,
                          protocol=config, best_epoch=best_epoch)
        temporary = dest / 'model.tmp.pt'
        torch.save(checkpoint, temporary)
        temporary.replace(dest / 'model.pt')
    rt.write_json(dest / 'training.json', dict(run_id=run_id, variant=args.variant, protocol=config,
                  cleaning=cleaning, seed=args.seed, best_epoch=best_epoch, best_val_ap=best_ap,
                  epochs_run=epoch, patience=args.patience, batch_size=args.batch_size,
                  lr=1e-3, weight_decay=1e-4, dropout=.15, optimizer='AdamW',
                  positive=positive, negative=negative, pos_weight=pos_weight,
                  train_monitor='all supervised training units, eval mode',
                  checkpoint_saved=not bool(args.limit), seconds=time.monotonic() - start))


if __name__ == '__main__':
    main()
