"""One scorer per job; original V1 optimization, all-split scores, and cluster-bootstrap rankings."""
from exp8s3_common import *
import copy
import pickle
import random
import torch
from torch import nn
from sklearn.ensemble import HistGradientBoostingClassifier
from exp6b_common import classifier


def metrics(y, score, pos_weight=1.):
    if not len(y):
        return dict(loss=None, auc=None, ap=None)
    score = np.asarray(score, np.float64)
    s = np.clip(score, 1e-7, 1-1e-7)
    loss = float(-np.mean(pos_weight*y*np.log(s) + (1-y)*np.log1p(-s)))
    if len(np.unique(y)) < 2:
        return dict(loss=loss, auc=None, ap=None)
    auc, ap = b40.Ranking(y, score).evaluate(np.ones(len(y)))
    return dict(loss=loss, auc=auc, ap=ap)


def mlp(dim, deep=False):
    if not deep:
        return classifier(dim, .15)
    layers = []
    for width in (512, 256, 128, 64):
        layers.extend([nn.Linear(dim, width), nn.ReLU(), nn.Dropout(.15)])
        dim = width
    return nn.Sequential(*layers, nn.Linear(dim, 1))


@torch.inference_mode()
def predict(model, x, mean, std, batch_size):
    model.eval()
    scores = np.empty(len(x), np.float32)
    for start in range(0, len(x), batch_size):
        v = torch.from_numpy((x[start:start+batch_size]-mean)/std)
        scores[start:start+batch_size] = model(v).squeeze(1).sigmoid().numpy()
    return scores


def selection(args, data, kind, seed):
    tp, fp, fn = data['baseline']
    f = float(2*tp/max(1, 2*tp+fp+fn))
    threshold = f/2 if kind == 'M1-pF' else .25 if kind == 'M1-p25' else .5
    if threshold <= .1:
        raise ValueError('Training F1/2 overlaps the fixed negative range')
    y = np.where(data['precision'] >= threshold, 1, np.where(data['precision'] < .1, 0, -1)).astype(np.int8)
    take = y >= 0
    if kind == 'M1-ign3':
        take &= ~((y == 1) & (data['majority'] == 3))
    pos = np.flatnonzero(take & (y == 1))
    fraction = .25 if kind == 'M1-25' else .5 if kind == 'M1-50' else 1.
    # Same seeded permutation makes the 25%, 50%, 100% positive subsets nested.
    order = np.random.default_rng(seed).permutation(pos)
    retained = int(np.ceil(len(order)*fraction))
    take[order[retained:]] = False
    npos, nneg = int((take & (y == 1)).sum()), int((take & (y == 0)).sum())
    if min(npos, nneg) == 0:
        raise ValueError(f'{args.dataset}/{kind}: no positive or negative training examples')
    return take, y, dict(train_detector_f1=f, positive_threshold=threshold, positive_fraction=fraction,
                         positive=npos, negative=nneg, pos_weight=nneg/npos)


def train(args):
    dest = args.out / args.dataset / 'models' / args.method
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise FileExistsError(f'{dest}: training already started; use a new run directory')
    kind, seed = method_info(args.method)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    started = time.monotonic()
    record = dict(method=args.method, seed=seed, checkpoint_saved=False,
                  early_stopping='validation AP on unchanged >=50% positive / <10% negative labels')
    model, mean, std = None, None, None
    if kind not in ('M0', 'D', 'O'):
        tr, va = assemble(args, 'train'), assemble(args, 'val')
        take, y, selected = selection(args, tr, kind, seed)
        save_npz(dest / 'training_selection.npz', dict(id=tr['id'][take], image_id=tr['image_id'][take], label=y[take]))
        record.update(selected)
        vtake = va['label'] >= 0
        if len(np.unique(va['label'][vtake])) != 2:
            raise ValueError('Validation must contain both labels for AP early stopping')
        tx, ty = tr['x'][take], y[take]
        vx, vy = va['x'][vtake], va['label'][vtake]
        pw = selected['pos_weight']
        best, best_iteration, best_model = -1., 0, None
        if kind == 'M3':
            model = HistGradientBoostingClassifier(learning_rate=.1, max_iter=1, max_leaf_nodes=31,
                       min_samples_leaf=20, l2_regularization=1., early_stopping=False,
                       warm_start=True, random_state=seed)
            epochs = min(args.hgb_iterations, args.epochs) if args.limit else args.hgb_iterations
            patience = args.patience if args.limit else args.hgb_patience
            weights = np.where(ty == 1, pw, 1.)
            record.update(estimator='HistGradientBoostingClassifier', learning_rate=.1,
                          max_leaf_nodes=31, min_samples_leaf=20, l2_regularization=1., patience=patience)
        else:
            values = tx.astype(np.float64)
            mean = values.mean(0).astype(np.float32)
            std = values.std(0).astype(np.float32); std[std < 1e-6] = 1.
            del values
            model = mlp(tx.shape[1], kind == 'M2')
            optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
            criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pw))
            epochs, patience = args.epochs, args.patience
            record.update(hidden=[512, 256, 128, 64] if kind == 'M2' else [512, 256], dropout=.15,
                          lr=1e-3, weight_decay=1e-4, optimizer='AdamW', batch_size=args.batch_size,
                          patience=patience, normalization='retained supervised training units')
        with (dest / 'train_log.jsonl').open('w') as handle:
            for epoch in range(1, epochs+1):
                if kind == 'M3':
                    model.set_params(max_iter=epoch)
                    model.fit(tx, ty, sample_weight=weights)
                    ts, vs = model.predict_proba(tx)[:, 1], model.predict_proba(vx)[:, 1]
                    optimization_loss = None
                else:
                    model.train(); total = 0.
                    order = rng.permutation(len(tx))
                    for start in range(0, len(tx), args.batch_size):
                        idx = order[start:start+args.batch_size]
                        xt = torch.from_numpy((tx[idx]-mean)/std)
                        yt = torch.from_numpy(ty[idx].astype(np.float32))
                        optimizer.zero_grad(set_to_none=True)
                        loss = criterion(model(xt).squeeze(1), yt)
                        if not torch.isfinite(loss):
                            raise ValueError('Nonfinite training loss')
                        loss.backward(); optimizer.step()
                        total += float(loss.detach())*len(idx)
                    optimization_loss = total/len(tx)
                    ts = predict(model, tx, mean, std, args.batch_size)
                    vs = predict(model, vx, mean, std, args.batch_size)
                tm, vm = metrics(ty, ts, pw), metrics(vy, vs, pw)
                if vm['ap'] > best:
                    best, best_iteration = vm['ap'], epoch
                    best_model = copy.deepcopy(model) if kind == 'M3' else copy.deepcopy(model.state_dict())
                row = dict(epoch=epoch, train=tm, val=vm, train_optimization_loss=optimization_loss,
                           best_epoch=best_iteration, seconds=time.monotonic()-started)
                handle.write(json.dumps(row, allow_nan=False)+'\n'); handle.flush()
                print(f'{args.method} {epoch}: val AP={vm["ap"]:.6f}', flush=True)
                if epoch-best_iteration >= patience:
                    break
        if kind == 'M3':
            model = best_model
        else:
            model.load_state_dict(best_model)
        record.update(best_epoch=best_iteration, best_val_ap=best, epochs_run=epoch)
        if not args.limit:
            if kind == 'M3':
                with (dest / 'model.pkl').open('wb') as h:
                    pickle.dump(model, h)
            else:
                torch.save(dict(state_dict=model.state_dict(), mean=mean, std=std, config=record), dest / 'model.pt')
            record['checkpoint_saved'] = True
        del tr, va, tx, ty, vx, vy
    elif kind == 'M0':
        checkpoint = torch.load(root(args.dataset) / 'models/full/model.pt', map_location='cpu', weights_only=False)
        model = mlp(checkpoint['input_dim'])
        model.load_state_dict(checkpoint['state_dict'])
        mean, std = (checkpoint[k].cpu().numpy() for k in ('mean', 'std'))
        record['source'] = str(root(args.dataset) / 'models/full/model.pt')
        record['train_score_source'] = 'frozen V1 checkpoint inference; no upstream train score archive'
        record['val_test_score_source'] = 'existing evaluation scores/full, copied exactly'
    for split in SPLITS:
        data = assemble(args, split)
        if kind == 'D':
            score = data['x'][:, 0]
        elif kind == 'O':
            score = data['precision']
        elif kind == 'M0' and split != 'train':
            score = np.concatenate([p['m0'] for p in load_parts(args, split)])
        elif kind == 'M3':
            score = model.predict_proba(data['x'])[:, 1] if len(data['x']) else np.empty(0)
        else:
            score = predict(model, data['x'], mean, std, args.batch_size)
        if score.shape != data['id'].shape or not np.isfinite(score).all():
            raise ValueError('Missing/nonfinite predictions')
        save_npz(score_path(args, args.method, split), dict(score=score, id=data['id'],
                 image_id=data['image_id'], images=np.array(names(args, split))))
    record['seconds'] = time.monotonic()-started
    write_json(dest / 'training.json', record)


def bootstrap(y, score, image_id, image_count, replicates):
    result = dict(positive=int((y == 1).sum()), negative=int((y == 0).sum()),
                  auc=None, ap=None, auc_ci95=None, ap_ci95=None, bootstrap_replicates=replicates,
                  bootstrap_valid=0, bootstrap_seed=20261024)
    if len(np.unique(y)) < 2:
        return dict(result, status='single_class')
    rank = b40.Ranking(y, score)
    auc, ap = rank.evaluate(np.ones(len(y)))
    result.update(auc=auc, ap=ap, status='ok')
    rng = np.random.default_rng(20261024)
    samples = []
    for _ in range(replicates):
        counts = np.bincount(rng.integers(image_count, size=image_count), minlength=image_count)
        pair = rank.evaluate(counts[image_id])
        if np.isfinite(pair).all():
            samples.append(pair)
    result['bootstrap_valid'] = len(samples)
    result['bootstrap_degenerate'] = replicates-len(samples)
    if samples:
        ci = np.percentile(samples, [2.5, 97.5], axis=0)
        result.update(auc_ci95=ci[:, 0].tolist(), ap_ci95=ci[:, 1].tolist())
    return result


def ranking(args):
    result = {}
    for split in ('val', 'test'):
        data = assemble(args, split)
        score = load_scores(args, args.method, split, data)['score']
        result[split] = {}
        for group in ('fill', 'cell2'):
            take = data['label'] >= 0 if group == 'fill' else ((data['label'] == 0) | ((data['label'] == 1) & (data['majority'] == 2)))
            result[split][group] = bootstrap(data['label'][take], score[take], data['image_id'][take],
                                            len(names(args, split)), args.bootstrap)
    write_json(args.out / args.dataset / 'ranking' / (args.method + '.json'), result)


def main():
    p = parser(__doc__)
    p.add_argument('stage', choices=('train', 'rank'))
    p.add_argument('--method', choices=METHODS, required=True)
    p.add_argument('--epochs', type=int, default=50)
    p.add_argument('--patience', type=int, default=7)
    p.add_argument('--batch-size', type=int, default=2048)
    p.add_argument('--hgb-iterations', type=int, default=300)
    p.add_argument('--hgb-patience', type=int, default=20)
    p.add_argument('--bootstrap', type=int, default=2000)
    args = setup(p.parse_args())
    if min(args.epochs, args.patience, args.batch_size, args.hgb_iterations, args.hgb_patience, args.bootstrap) < 1:
        p.error('Iteration counts and batch size must be positive')
    if not args.limit and args.bootstrap != 2000:
        p.error('Full runs require 2000 cluster bootstrap replicates')
    start = time.monotonic()
    (train if args.stage == 'train' else ranking)(args)
    timing(args, args.stage, start)

if __name__ == '__main__':
    main()
