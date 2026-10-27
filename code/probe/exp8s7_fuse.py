"""CPU late fusion of matching-seed image/meta train and validation scores.
--final applies the frozen fusion.json (training mean/std, alpha) to test scores; nothing is refit."""
from exp8s7_common import *

ALPHAS = (0., .25, .5, .75, 1.)


def seed_directories(paths):
    result = {}
    for path in paths:
        prefix, separator, seed = path.name.rpartition('-s')
        if not prefix or not separator or not seed.isdigit():
            raise ValueError(f'{path}: require a method-sSEED directory')
        seed = int(seed)
        if seed in result:
            raise ValueError(f'Duplicate seed {seed}')
        result[seed] = path.resolve()
    return result


def logit(score):
    probability = np.clip(score.astype(np.float64), 1e-6, 1-1e-6)
    return np.log(probability)-np.log1p(-probability)


def main():
    p = parser(__doc__)
    p.add_argument('--img-scores', type=Path, nargs='+', required=True)
    p.add_argument('--meta-scores', type=Path, nargs='+', required=True)
    p.add_argument('--run-name', required=True, help='Output prefix: NAME-a0.25-s1020 etc.')
    p.add_argument('--alphas', type=float, nargs='+', help='--final: which frozen alphas to score')
    p.add_argument('--final', action='store_true')
    a = setup(p)
    if a.final:
        return final(a, p)
    if Path(a.run_name).name != a.run_name:
        p.error('--run-name must be a single directory name')
    images, metadata = seed_directories(a.img_scores), seed_directories(a.meta_scores)
    if images.keys() != metadata.keys():
        p.error('Image and metadata seed sets must match')
    units = {split: load_units(a, split) for split in ('train', 'val')}
    tr = units['train']
    retained = (tr['label'] >= 0) & ~((tr['label'] == 1) & (tr['majority'] == 3))
    if not retained.any():
        raise ValueError('No retained training candidates')
    destinations = {(seed, alpha): local(a.out / a.dataset / 'scores' / f'{a.run_name}-a{alpha:g}-s{seed}')
                    for seed in images for alpha in ALPHAS}
    for dest in destinations.values():
        if dest.exists():
            raise FileExistsError(f'{dest}: choose a new fusion run name')
    for seed in sorted(images):
        standardized, normalization = [], {}
        for branch, folder in (('img', images[seed]), ('meta', metadata[seed])):
            values = {split: logit(align_scores(folder / f'{split}.npz', z)) for split, z in units.items()}
            mean, std = float(values['train'][retained].mean()), float(values['train'][retained].std())
            if std == 0:
                raise ValueError(f'{folder}: constant retained training logits cannot be standardized')
            normalization[branch] = dict(directory=str(folder), mean=mean, std=std, variance=std**2)
            standardized.append({split: (value-mean)/std for split, value in values.items()})
        for alpha in ALPHAS:
            dest = destinations[seed, alpha]
            for split, z in units.items():
                score = alpha*standardized[0][split]+(1-alpha)*standardized[1][split]
                save_scores(dest / f'{split}.npz', z, score)
            write(dest / 'fusion.json', dict(seed=seed, alpha=alpha, normalization=normalization,
                  clip=1e-6, retained_train=int(retained.sum()), ign3=True, limit=a.limit,
                  normalization_scope='retained training candidates; population standard deviation',
                  score='alpha*z_img+(1-alpha)*z_meta; not a probability'))
            print(dest, flush=True)


def final(a, p):
    if a.limit or not a.alphas:
        p.error('--final needs --alphas and no --limit')
    images, metadata = seed_directories(a.img_scores), seed_directories(a.meta_scores)
    if images.keys() != metadata.keys():
        p.error('Image and metadata seed sets must match')
    z = load_units(a, 'test')
    for seed in sorted(images):
        for alpha in a.alphas:
            dest = local(a.out / a.dataset / 'scores' / f'{a.run_name}-a{alpha:g}-s{seed}')
            frozen = json.loads((dest / 'fusion.json').read_text())
            if (dest / 'test.npz').exists():
                raise FileExistsError(f'{dest}: test has already been scored')
            norm = frozen['normalization']
            for branch, folder in (('img', images[seed]), ('meta', metadata[seed])):
                if Path(norm[branch]['directory']) != folder:
                    raise ValueError(f'{folder}: differs from frozen {norm[branch]["directory"]}')
            parts = [(logit(align_scores(folder / 'test.npz', z))-norm[b]['mean'])/norm[b]['std']
                     for b, folder in (('img', images[seed]), ('meta', metadata[seed]))]
            save_scores(dest / 'test.npz', z, frozen['alpha']*parts[0]+(1-frozen['alpha'])*parts[1])
            print(dest / 'test.npz', flush=True)


if __name__ == '__main__':
    main()
