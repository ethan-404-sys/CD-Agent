"""Initialize frozen input inventory, then compute routed U5 units in image shards."""
from exp8s3_common import *


def initialize(args):
    rule = read_json(args.rule)
    if rule['method'] not in ('base', 'a', 'b', 'ab') or rule['tau'] not in (.1, .2):
        raise ValueError('Unsupported frozen rule')
    if rule['A_max'] not in (None, 5000, 20000, 50000) or rule['prefilter_novel_lt'] not in (None, 100):
        raise ValueError('Unexpected area/prefilter setting')
    protocol = read_json(root(args.dataset) / 'protocol.json')
    images = {s: protocol[s + '_images'] for s in ('train', 'val')}
    images['test'] = b40.image_list(args.dataset)
    if set(images['train']) & set(images['val']):
        raise ValueError('Training/validation overlap')
    if len(protocol['features']) != (19 if args.dataset == 'second' else 15):
        raise ValueError('Unexpected V1 features')
    if args.limit:
        # Real two-class subsets, required because the first SYSU training images have no positives.
        for split in ('train', 'val'):
            chosen = []
            for name in images[split]:
                rows = read_json(pool_dir(args.dataset, split) / 'images' / (Path(name).stem + '.json'))['units']['U5_x4']['details']
                eligible = [r for r in rows if (r['area']-r['residual_area'])/r['area'] < rule['tau']
                            and r['residual_area'] >= (rule['prefilter_novel_lt'] or 0)]
                positives = [r for r in eligible if r['residual_gt']/max(1,r['residual_area']) >= .5]
                if (len(positives) >= 4 and
                        any(r['residual_gt']/max(1,r['residual_area']) < .1 for r in eligible)):
                    # Keep the ign3 smoke variant trainable using genuine non-cell3 positives.
                    if split == 'train':
                        gt, base, _, cell, _ = pixels(args.dataset, split, name)
                        objects, _ = pool_objects(args.dataset, split, name)
                        non3 = 0
                        for row in positives:
                            obj = objects[row['unit_id']]
                            x0,y0,x1,y1 = obj['bbox']
                            new = obj['mask'] & ~base[y0:y1,x0:x1]
                            counts = [(new & (cell[y0:y1,x0:x1] == c)).sum() for c in CELLS]
                            non3 += CELLS[int(np.argmax(counts))] != 3
                        if non3 < 4:
                            continue
                    chosen.append(name)
                if len(chosen) == args.limit:
                    break
            if len(chosen) != args.limit:
                raise ValueError('Cannot find requested real smoke images with both routed labels')
            images[split] = chosen
        images['test'] = images['test'][:args.limit]
    value = dict(version=1, dataset=args.dataset, limit=args.limit, partial=bool(args.limit),
                 rule=rule, rule_path=str(args.rule.resolve()), rule_text=json.dumps(rule, sort_keys=True),
                 images=images, methods=list(METHODS), feature_names=protocol['features'],
                 upstream_protocol=str(root(args.dataset) / 'protocol.json'),
                 limitations=protocol.get('limitations', []),
                 smoke_selection='first N images with >=4 novel-pixel routed positives and >=1 negative; train also >=4 non-cell3 positives; test first N' if args.limit else None,
                 u5_mapping='U5 stays whole; all methods use own r; oversized units counted; prefilter excludes from all stages')
    path = args.out / args.dataset / 'manifest.json'
    if path.exists() and read_json(path) != value:
        raise ValueError('Run already initialized with a different rule/inventory; use a new --out')
    write_json(path, value)
    print(json.dumps({s: len(v) for s, v in images.items()}), flush=True)


def units(args):
    rule = manifest(args)['rule']
    for split in SPLITS if args.split == 'all' else (args.split,):
        for name in names(args, split, True):
            dest = unit_path(args, split, name)
            if dest.exists():
                raise FileExistsError(f'{dest}: already processed')
            gt, base, prob, cell, baseline = pixels(args.dataset, split, name)
            objects, shape = pool_objects(args.dataset, split, name)
            details = read_json(pool_dir(args.dataset, split) / 'images' / (Path(name).stem + '.json'))['units']['U5_x4']['details']
            if tuple(shape) != gt.shape or len(objects) != len(details):
                raise ValueError('Mask inventory mismatch')
            x, old_labels = features(args.dataset, split, name, objects, prob, details)
            novel, truth, r, majority, cell_truth = [], [], [], [], []
            tolerated = 0
            for i, (obj, row) in enumerate(zip(objects, details)):
                if row['unit_id'] != i or row['bbox'] != obj['bbox'] or row['area'] != obj['area']:
                    raise ValueError(f'{name}/{i}: metadata/mask mismatch')
                x0, y0, x1, y1 = obj['bbox']
                new = obj['mask'] & ~base[y0:y1, x0:x1]
                n = int(new.sum())
                t = int((new & gt[y0:y1, x0:x1]).sum())
                c = np.array([(new & (cell[y0:y1, x0:x1] == k)).sum() for k in CELLS], np.int64)
                # 池缓存的检测器掩码与本次重算有个别像素差异（每单元最多差 1 像素），容许每单元差 ≤ max(5 像素, 新增面积 1%)，计数备查（SECOND 训练 / 验证实测最多差 3 像素）。
                off = max(abs(n - row['residual_area']), abs(t - row['residual_gt']))
                if c.sum() != t or off > max(5, .01 * row['residual_area']):
                    raise ValueError(f'{name}/{i}: cached novel truth/baseline mismatch')
                tolerated += off > 0
                novel.append(n); truth.append(t); r.append((obj['area']-n)/obj['area'])
                cell_truth.append(c)
                majority.append(CELLS[int(c.argmax())] if t else 0)
            novel, truth, r = np.asarray(novel, np.int64), np.asarray(truth, np.int64), np.asarray(r)
            precision = np.divide(truth, novel, out=np.zeros(len(novel)), where=novel > 0)
            labels = np.where(precision >= .5, 1, np.where(precision < .1, 0, -1)).astype(np.int8)
            pool_labels = np.asarray([{'positive':1,'negative':0,'middle':-1}[row['label']] for row in details], np.int8)
            if not np.array_equal(pool_labels, old_labels):
                raise ValueError(f'{name}: saved V1 labels differ from pool metadata')
            routed = r < rule['tau']
            keep = routed & (novel >= (rule['prefilter_novel_lt'] or 0))
            ids = np.flatnonzero(keep).astype(np.int32)
            area = np.array([o['area'] for o in objects])
            arrays = dict(image=name, rule=manifest(args)['rule_text'], id=ids, x=x[keep], label=labels[keep],
                          precision=precision[keep], majority=np.asarray(majority, np.int8)[keep],
                          novel=novel[keep], truth=truth[keep], r=r[keep],
                          cell_truth=np.asarray(cell_truth, np.int64).reshape(-1, 4)[keep], baseline=baseline,
                          cell_totals=np.array([(cell == c).sum() for c in CELLS], np.int64),
                          total_units=len(objects), routed=int(routed.sum()), dropped=int((routed & ~keep).sum()),
                          old_label=old_labels[keep], relabeled=int(((labels != old_labels) & keep).sum()),
                          tolerated_px_mismatch=tolerated,
                          oversized=int((area > rule['A_max']).sum()) if rule['A_max'] else 0,
                          oversized_fill=int(((area > rule['A_max']) & keep).sum()) if rule['A_max'] else 0)
            if split != 'train':
                z = archive(root(args.dataset) / 'evaluation' / split / 'scores' / (Path(name).stem + '.npz'))
                if not np.array_equal(z['unit_id'], np.arange(len(objects))) or not np.array_equal(z['label'], old_labels):
                    raise ValueError('Saved M0 score unit keys differ')
                if not np.array_equal(z['detector'], x[:, 0]):
                    raise ValueError('D features differ from saved detector scores')
                arrays['m0'] = z['full'][keep]
            save_npz(dest, arrays)
            print(f'{split}/{name}: {len(ids)} fill units', flush=True)


def main():
    p = parser(__doc__)
    p.add_argument('stage', choices=('init', 'units'))
    p.add_argument('--rule', type=Path, default=STAGING / 'batch42/run/frozen_rule.json')
    p.add_argument('--split', choices=(*SPLITS, 'all'), default='all')
    shard_arguments(p)
    args = setup(p.parse_args()); check_shard(args)
    start = time.monotonic()
    (initialize if args.stage == 'init' else units)(args)
    timing(args, args.stage, start)

if __name__ == '__main__':
    main()
