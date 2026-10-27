"""Train on train/val caches; --final only scores test with an existing frozen model."""
from exp8s7_common import *
import random
import time
import torch
from torch import nn
from torch.nn import functional as F


class Inputs:
    def __init__(self, a, split, z, mean, std):
        self.z, self.mean, self.std = z, mean, std
        self.mode, self.regions = a.inputs, a.regions
        self.head = a.head
        self.vectors = None
        self.dim = z['x'].shape[1] if self.mode in ('meta', 'img+meta') else 0
        if self.mode in ('img', 'img+meta'):
            folder = (a.features_dir or a.out / a.dataset / 'features') / split
            index = c.archive(folder / 'index.npz')
            for k in ('images', 'image_id', 'id'):
                if not np.array_equal(index[k], z[k]):
                    raise ValueError(f'{split}: feature {k} mismatch')
            self.vectors = np.load(folder / 'vectors.npy', mmap_mode='r', allow_pickle=False)
            if self.vectors.shape != (len(z['id']), 2, 3, 1024):
                raise ValueError('Feature dimensions differ')
            self.dim += (2 if self.head == 'sym' else 4) * 1024 * len(self.regions)

    def batch(self, indices):
        blocks = []
        if self.vectors is not None:
            v = self.vectors[indices][:, :, self.regions].astype(np.float32).reshape(len(indices), 2, -1)
            f1, f2 = v[:, 0], v[:, 1]
            if self.head == 'sym':
                blocks.extend([(f1+f2)/2, np.abs(f2-f1)])
            else:
                blocks.extend([f1, f2, f2-f1, np.abs(f2-f1)])
        if self.mode in ('meta', 'img+meta'):
            blocks.append((self.z['x'][indices]-self.mean)/self.std)
        x = np.concatenate(blocks, axis=1).astype(np.float32)
        if not np.isfinite(x).all():
            raise ValueError('Nonfinite model inputs')
        return torch.from_numpy(x)


class SymHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.mean_projection = nn.Sequential(nn.LayerNorm(1024), nn.Linear(1024, 64))
        self.diff_projection = nn.Sequential(nn.LayerNorm(1024), nn.Linear(1024, 64))
        self.classifier = nn.Sequential(nn.Linear(384, 128), nn.ReLU(), nn.Dropout(.2), nn.Linear(128, 1))

    def forward(self, x):
        m, d = x.reshape(-1, 2, 3, 1024).unbind(1)
        projected = torch.cat([self.mean_projection(m), self.diff_projection(d)], dim=-1)
        return self.classifier(projected.flatten(1))


def model_for(dim, kind, head='default'):
    if head == 'sym':
        return SymHead()
    if kind == 'm1':
        # Exactly batch43 exp8s3_train.mlp -> exp6b_common.classifier.
        return nn.Sequential(nn.Linear(dim, 512), nn.ReLU(), nn.Dropout(.15),
                             nn.Linear(512, 256), nn.ReLU(), nn.Dropout(.15), nn.Linear(256, 1))
    return nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, 512), nn.ReLU(), nn.Dropout(.2),
                         nn.Linear(512, 512), nn.ReLU(), nn.Dropout(.2), nn.Linear(512, 1))


class ShuffledPool:
    def __init__(self, indices, rng):
        self.indices, self.rng = indices, rng
        self.order = np.empty(0, dtype=np.int64)
        self.position = 0

    def draw(self, count):
        blocks = []
        while count:
            if self.position == len(self.order):
                self.order = self.rng.permutation(self.indices)
                self.position = 0
            size = min(count, len(self.order)-self.position)
            blocks.append(self.order[self.position:self.position+size])
            self.position += size
            count -= size
        return np.concatenate(blocks)


def training_batches(selected, batch_size, rng, pools=None):
    if pools is None:
        order = rng.permutation(selected)
        for start in range(0, len(order), batch_size):
            yield order[start:start+batch_size]
    else:
        for _ in range((len(selected)+batch_size-1)//batch_size):
            yield rng.permutation(np.concatenate([pools[0].draw(64), pools[1].draw(192)]))


def loss_fn(logits, target, pw, focal):
    bce = F.binary_cross_entropy_with_logits(logits, target, pos_weight=logits.new_tensor(pw), reduction='none')
    if focal:
        probability = logits.sigmoid()
        pt = torch.where(target == 1, probability, 1-probability)
        bce *= (1-pt).pow(focal)
    return bce.mean()


@torch.inference_mode()
def predict(model, inputs, a):
    model.eval()
    score = np.empty(len(inputs.z['id']), np.float32)
    for start in range(0, len(score), a.batch_size):
        indices = np.arange(start, min(start+a.batch_size, len(score)))
        score[indices] = model(inputs.batch(indices).to(a.device)).squeeze(1).sigmoid().cpu().numpy()
    return score


def score_loss(y, score, pw, focal):
    s = np.clip(score.astype(np.float64), 1e-7, 1-1e-7)
    losses = -(pw*y*np.log(s)+(1-y)*np.log1p(-s))
    if focal:
        losses *= (1-np.where(y == 1, s, 1-s)) ** focal
    return float(losses.mean())


def train_one(a, seed, run_name):
    dest = local(a.out / a.dataset / 'models' / run_name)
    if dest.exists():
        raise FileExistsError(f'{dest}: choose a new run name')
    tr, va = load_units(a, 'train'), load_units(a, 'val')
    take = tr['label'] >= 0
    if a.ign3:
        take &= ~((tr['label'] == 1) & (tr['majority'] == 3))
    selected = np.flatnonzero(take)
    y = tr['label'][selected]
    if len(np.unique(y)) != 2:
        raise ValueError('Training requires both retained label classes')
    vtwo, vtake = cell2_selection(va), va['label'] >= 0
    if len(np.unique(va['label'][vtwo])) != 2:
        raise ValueError('Validation requires cell-2 positives and negatives for early stopping')
    pw = 1. if a.sampling == '1:3' else float((y == 0).sum() / (y == 1).sum())
    mean = tr['x'][selected].mean(0, dtype=np.float64).astype(np.float32)
    std = tr['x'][selected].std(0, dtype=np.float64).astype(np.float32)
    std[std < 1e-6] = 1
    tx, vx = Inputs(a, 'train', tr, mean, std), Inputs(a, 'val', va, mean, std)
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if a.device == 'cuda':
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    rng = np.random.default_rng(seed)
    pools = ([ShuffledPool(selected[y == label], rng) for label in (1, 0)]
             if a.sampling == '1:3' else None)
    model = model_for(tx.dim, a.model, a.head).to(a.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    best, best_epoch, best_state = -1., 0, None
    dest.mkdir(parents=True)
    started = time.monotonic()
    with (dest / 'train_log.jsonl').open('w') as log:
        for epoch in range(1, a.epochs+1):
            model.train(); total = 0.; drawn = 0; positive_draws = 0; steps = 0
            for ix in training_batches(selected, a.batch_size, rng, pools):
                target = torch.from_numpy(tr['label'][ix].astype(np.float32)).to(a.device)
                optimizer.zero_grad(set_to_none=True)
                loss = loss_fn(model(tx.batch(ix).to(a.device)).squeeze(1), target, pw, a.focal_gamma)
                if not torch.isfinite(loss):
                    raise ValueError('Nonfinite training loss')
                loss.backward(); optimizer.step()
                total += float(loss.detach()) * len(ix)
                drawn += len(ix); positive_draws += int((tr['label'][ix] == 1).sum()); steps += 1
            vs = predict(model, vx, a)
            metrics = rank_metrics(va['label'][vtwo], vs[vtwo])
            if metrics['ap'] > best:
                best, best_epoch = metrics['ap'], epoch
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            row = dict(epoch=epoch, train_loss=total/drawn, optimizer_steps=steps, sampled_units=drawn,
                       positive_draws=positive_draws,
                       positive_mean_draws=positive_draws/int((y == 1).sum()),
                       val_loss=score_loss(va['label'][vtake], vs[vtake], pw, a.focal_gamma),
                       val_cell2=metrics, best_epoch=best_epoch, seconds=time.monotonic()-started)
            log.write(json.dumps(row, allow_nan=False)+'\n'); log.flush()
            print(f'{run_name} epoch {epoch}: val cell2 AP={metrics["ap"]:.6f}; '
                  f'positive mean draws={row["positive_mean_draws"]:.4f}; steps={steps}', flush=True)
            if epoch-best_epoch >= a.patience:
                break
    model.load_state_dict(best_state)
    record = dict(run_name=run_name, seed=seed, inputs=a.inputs, regions=a.regions, model=a.model,
                  head=a.head, sampling=a.sampling,
                  features_dir=str(a.features_dir.resolve()) if a.features_dir else None,
                  parameters=sum(p.numel() for p in model.parameters()),
                  ign3=a.ign3, focal_gamma=a.focal_gamma, lr=a.lr, weight_decay=1e-4, batch_size=a.batch_size,
                  patience=a.patience, epochs_run=epoch, best_epoch=best_epoch, best_val_ap=best,
                  early_stopping='val cell2 positives vs all fill negatives AP', positive_weight=pw,
                  retained_positive=int((y == 1).sum()), retained_negative=int((y == 0).sum()),
                  normalization='retained training metadata only', input_dim=tx.dim,
                  seconds=time.monotonic()-started, limit=a.limit, checkpoint_saved=not bool(a.limit))
    if not a.limit:
        torch.save(dict(state_dict=best_state, mean=mean, std=std, config=record), dest / 'model.pt')
    for split, z, inputs in (('train', tr, tx), ('val', va, vx)):
        save_scores(a.out / a.dataset / 'scores' / run_name / f'{split}.npz', z, predict(model, inputs, a))
    write(dest / 'training.json', record)


def final_score(a, run_name):
    # No training and no validation selection on this path.
    if a.limit:
        raise ValueError('Smoke runs do not save checkpoints or score test')
    path = a.out / a.dataset / 'scores' / run_name / 'test.npz'
    if path.exists():
        raise FileExistsError(f'{path}: test has already been scored')
    checkpoint = torch.load(a.out / a.dataset / 'models' / run_name / 'model.pt', map_location='cpu', weights_only=False)
    cfg = checkpoint['config']
    for key in ('inputs', 'regions', 'model'):
        setattr(a, key, cfg[key])
    a.head = cfg.get('head', 'default')
    a.features_dir = Path(cfg['features_dir']) if cfg.get('features_dir') else None
    z = load_units(a, 'test')
    x = Inputs(a, 'test', z, checkpoint['mean'], checkpoint['std'])
    model = model_for(cfg['input_dim'], cfg['model'], a.head).to(a.device)
    model.load_state_dict(checkpoint['state_dict'])
    save_scores(path, z, predict(model, x, a))


def main():
    p = parser(__doc__)
    p.add_argument('--run-name', required=True)
    p.add_argument('--inputs', choices=('img', 'meta', 'img+meta'), default='img+meta')
    p.add_argument('--regions', nargs='+', choices=('inside', 'ring', 'L12'), default=['inside', 'ring', 'L12'])
    p.add_argument('--model', choices=('mlp', 'm1'), default='mlp')
    p.add_argument('--head', choices=('default', 'sym'), default='default')
    p.add_argument('--sampling', choices=('natural', '1:3'), default='natural')
    p.add_argument('--features-dir', type=Path, help='Feature root containing train/ and val/')
    p.add_argument('--ign3', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--focal-gamma', type=float, default=0)
    p.add_argument('--epochs', type=int, default=50)
    p.add_argument('--patience', type=int, default=7)
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--lr', type=float, default=1e-3)
    p.add_argument('--seeds', nargs='+', type=int, default=[1020])
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    p.add_argument('--final', action='store_true')
    a = setup(p)
    local(a.out / a.dataset / 'models' / a.run_name)
    if Path(a.run_name).name != a.run_name:
        p.error('--run-name must be a single directory name')
    if min(a.epochs, a.patience, a.batch_size) < 1 or a.lr <= 0 or a.focal_gamma < 0:
        p.error('Invalid training hyperparameters')
    if a.model == 'm1' and a.inputs != 'meta' and not a.final:
        p.error('The M1 control uses --inputs meta')
    a.regions = sorted(set({'L12': 0, 'inside': 1, 'ring': 2}[r] for r in a.regions))
    if not a.final and a.head == 'sym' and (a.inputs != 'img' or a.model != 'mlp' or a.regions != [0, 1, 2]):
        p.error('--head sym requires --inputs img, --model mlp and all three regions')
    if not a.final and a.sampling == '1:3' and (a.batch_size != 256 or a.focal_gamma != 0):
        p.error('--sampling 1:3 requires batch size 256 and ordinary BCE (--focal-gamma 0)')
    torch.set_num_threads(1)
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
    for seed in a.seeds:
        run_name = a.run_name if len(a.seeds) == 1 else f'{a.run_name}-s{seed}'
        if a.final:
            final_score(a, run_name)
        else:
            train_one(a, seed, run_name)


if __name__ == '__main__':
    main()
