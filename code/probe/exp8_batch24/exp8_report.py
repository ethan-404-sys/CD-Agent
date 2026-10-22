"""Freeze the joint four-scorer comparison, then produce one common U5/U0 table."""
from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from exp8_bootstrap import bootstrap, evaluation_module
import exp8_config as cfg


def definitions(root, limit):
    units = {k: cfg.rt.read_json(cfg.joint_path(root / k, limit)) for k in ('u5', 'u0')}
    a, b = [x['protocol'] for x in units.values()]
    for key in ('train_images', 'val_images', 'batch19', 'batch21', 'u0_test', 'pool_freeze', 'limit'):
        if a[key] != b[key]:
            raise ValueError(f'U5/U0 protocols disagree: {key}')
    if a['limit'] != limit or a['val_images'] != cfg.names('val', limit):
        raise ValueError('Comparison split differs')
    scorers = {**units['u5']['scorers'], **units['u0']['scorers']}
    if tuple(scorers) != ('full', 'clean', 'detector', 'u0'):
        raise ValueError('Expected U5 full/clean/detector and newly trained U0')
    if units['u5']['baseline'] != units['u0']['baseline']:
        raise ValueError('U5/U0 baseline counts differ')
    return units, scorers


def freeze(args):
    units, scorers = definitions(args.out, args.limit)
    path = cfg.joint_path(args.out, args.limit)
    if path.exists():
        existing = cfg.rt.read_json(path)
        if existing['units'] != units:
            raise ValueError('Joint comparison already frozen with different runs')
        return
    order = tuple(scorers)
    primary = max(order, key=lambda k: (scorers[k]['pixel_f1'], scorers[k]['pixel_iou'], -order.index(k)))
    cfg.b20.write_fixed(path, dict(scheme_id=str(uuid.uuid4()), units=units, scorers=scorers,
                                 primary=primary, partial=bool(args.limit),
                                 selection=['pixel_f1', 'pixel_iou'], tie_rule=list(order),
                                 limitations=cfg.LIMITATIONS))


def report(args):
    if args.limit and args.split == 'test':
        raise ValueError('Smoke cannot read test results')
    units, _ = definitions(args.out, args.limit)
    frozen = cfg.rt.read_json(cfg.joint_path(args.out, args.limit))
    if frozen['units'] != units:
        raise ValueError('Comparison no longer matches frozen runs')
    summaries = {k: cfg.rt.read_json(args.out / k / 'evaluation' / args.split / 'summary.json')
                 for k in units}
    for kind, row in summaries.items():
        if (row['scheme_id'] != units[kind]['scheme_id'] or row['partial'] != bool(args.limit)
                or row['images'] != cfg.names(args.split, args.limit)):
            raise ValueError(f'{kind}: incomplete or different evaluation')
    a, b = summaries['u5'], summaries['u0']
    if a['baseline'] != b['baseline']:
        raise ValueError('U5 and U0 must use identical baseline/GT and A–E metrics')
    combined = dict(scheme_id=frozen['scheme_id'], primary=frozen['primary'],
                    split=args.split, images=a['images'], partial=bool(args.limit), baseline=a['baseline'],
                    scorers={**a['scorers'], **b['scorers']}, limitations=cfg.LIMITATIONS,
                    inference_seconds=a['inference_seconds'] + b['inference_seconds'],
                    unit_schemes={k: v['scheme_id'] for k, v in units.items()},
                    image_records={k: str(args.out / k / 'evaluation' / args.split / 'images') for k in units})
    dest = args.out / 'evaluation' / args.split
    cfg.rt.write_json(dest / 'summary.json', combined)
    bootstrap('u5')
    evaluation_module().write_report(dest, combined, frozen)
    path = dest / 'report.md'
    text = path.read_text(encoding='utf-8')
    text = text.replace('逐图原值在 `images/*.json`',
                        '逐图原值在输出根目录 `u5/evaluation/<split>/images/` 与 `u0/evaluation/<split>/images/`')
    path.write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'report'))
    parser.add_argument('--out', type=Path, default=cfg.OUT)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--split', choices=('val', 'test'), default='val')
    args = parser.parse_args()
    if args.limit < 0 or args.limit and args.out.resolve() == cfg.OUT.resolve():
        parser.error('Smoke requires positive limit and separate output root')
    {'freeze': freeze, 'report': report}[args.action](args)


if __name__ == '__main__':
    main()
