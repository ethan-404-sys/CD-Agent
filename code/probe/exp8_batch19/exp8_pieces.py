"""6a-compatible SYSU B/G0.05 and prediction pieces, using the historical SYSU SLIC scale 972."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import exp8_runtime as rt
import exp8_data as data
import exp6a_pieces as sixa
import exp6_pool_io as pool_io


def configure(out, geom):
    """Bind the existing 6a implementation to this isolated output tree."""
    sixa.POOL = pool_io.POOL = Path(out) / 'exp6/pool'
    sixa.DETECTOR['sysu_val'] = 'schanger_small'
    sixa.geometry = lambda dataset: (geom['median_area'], geom)
    sixa.load_prob = lambda dataset, detector, name: rt.probability(dataset, name, out)
    sixa.load_pred = lambda dataset, detector, name: rt.prediction(dataset, name, out)

    def candidates(dataset, name, prob, pred):
        with rt.np.load(rt.sam3_path(dataset, name, out), allow_pickle=False) as z:
            a, b = z['cls_t1'], z['cls_t2']
            confidence = z['conf_t1'].astype(rt.np.float32) * z['conf_t2'].astype(rt.np.float32)
            if a.shape != prob.shape or b.shape != prob.shape:
                raise ValueError(f'{name}: SAM3/probability shape mismatch')
            binary = (a > 0) & (b > 0) & (a != b)
        for source, mask, score in [('B', binary, confidence), ('G0.05', prob > .05, prob)]:
            for component, value in sixa.components(mask, score):
                yield source, None, component, value

    sixa.candidates = candidates


def recall_units(name, base, geom):
    """Read this image's 6a masks without materializing the full JSONL."""
    row = rt.read_json(base / 'images' / (Path(name).stem + '.json'))
    if row['config']['geometry'] != geom:
        raise ValueError(f'{name}: 6a/U0 SLIC geometry mismatch')
    pool_io.POOL = base.parent
    masks = pool_io.load_piece_masks('sysu_val', name)
    units = []
    for record in row['records']:
        if record['kind'] != 'recall':
            continue
        box, mask = masks[record['piece_id']]
        units.append(dict(bbox=box, mask=mask, area=int(mask.sum()),
                          sources=[record['source']], members=[record['cand_id']],
                          old_piece_id=record['piece_id']))
    return units


def merge(args, geom, config):
    base = args.out / 'exp6/pool/sysu_val'
    names = data.image_names()[:args.limit or None]
    groups, images = sixa.empty_groups('sysu_val'), []
    summary_path = base / 'pieces_summary.json'
    summary_path.unlink(missing_ok=True)
    temp = base / 'pieces.jsonl.tmp'
    with temp.open('w') as handle:
        for name in names:
            row = rt.read_json(base / 'images' / (Path(name).stem + '.json'))
            if row['config'] != config or row['image'] != name:
                raise ValueError(f'{name}: incompatible piece checkpoint')
            masks = pool_io.load_piece_masks('sysu_val', name)
            records = row['records']
            if list(masks) != list(range(1, len(records) + 1)):
                raise ValueError(f'{name}: missing or unordered masks')
            if row['summary']['pieces'] != len(records):
                raise ValueError(f'{name}: incorrect piece count')
            for i, record in enumerate(records, 1):
                box, mask = masks[i]
                if (record['image'] != name or record['piece_id'] != i
                        or box != record['bbox'] or int(mask.sum()) != record['area']):
                    raise ValueError(f'{name}/{i}: record/mask mismatch')
                sixa.count_record(groups, record)
                handle.write(json.dumps(record, allow_nan=False) + '\n')
            images.append(row['summary'])
    temp.replace(base / 'pieces.jsonl')
    report = sixa.summary('sysu_val', groups, images, 0, 1)
    report['config'].update(config, recall_sources=['B', 'G0.05'], c2_policy='not_used')
    report.pop('h1_missing_images')
    rt.write_json(summary_path, report)
    if not args.limit:
        from exp8_verifier import open_pool, verifier_split
        pool = open_pool(args.out)
        train, val = verifier_split(args.out)
        rt.write_json(base / 'compatibility.json', dict(
            images=len(pool.images), pieces=len(pool), verifier_train_images=len(train),
            verifier_val_images=len(val), status='passed', checks=['6b Pool metadata and image partition']))
    print(f'Merged {len(names)} images / {report["pieces"]} pieces', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    rt.add_arguments(parser)
    parser.add_argument('--merge', action='store_true')
    args = parser.parse_args()
    if args.dataset != 'sysu_val' or args.limit < 0:
        parser.error('Pieces require sysu_val and nonnegative --limit')
    geom = rt.load_scale(args.out, 'slic', allow_partial=bool(args.limit))
    configure(args.out, geom)
    config = dict(dataset='sysu_val', geometry=geom, partial=bool(args.limit),
                  sources=['B', 'G0.05'], prediction_pieces=True, revision=rt.REVISION)
    base = args.out / 'exp6/pool/sysu_val'
    (base / 'masks').mkdir(parents=True, exist_ok=True)
    if args.merge:
        merge(args, geom, config)
        return
    for name in rt.subset(data.image_names(), args):
        path = base / 'images' / (Path(name).stem + '.json')
        if path.is_file():
            row = rt.read_json(path)
            if row['config'] != config or row['image'] != name:
                raise ValueError(f'{path}: settings changed; use a new output directory')
            if not (base / 'masks' / (Path(name).stem + '.npz')).is_file():
                raise FileNotFoundError(f'{name}: missing 6a masks')
            continue
        records, summary = sixa.process(name, 'sysu_val')
        rt.write_json(path, dict(config=config, image=name, records=records, summary=summary))
        print(f'6a completed {name}: {len(records)} pieces', flush=True)


if __name__ == '__main__':
    main()
