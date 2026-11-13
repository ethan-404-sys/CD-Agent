"""Prepare a full review snapshot on a Slurm CPU node; import into existing UI DB."""
from __future__ import annotations

import argparse
import json
import uuid
from contextlib import closing
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import construct_review_store as store
from construct_review_qc import assets_for, batch_qc, load_mask, source_paths
from task_data_api import paint, outline
from task_data_import import read_image


def load_manifests(paths):
    tasks, sources, seen = [], [], set()
    for path in paths:
        path = Path(path).resolve()
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            task = json.loads(line)
            meta = task.setdefault('meta', {})
            ident = meta.get('review_id')
            if not ident:
                raise ValueError(f'{path}: meta.review_id is required')
            if ident in seen:
                raise ValueError(f'Duplicate review_id: {ident}')
            seen.add(ident)
            # Imported status is never a human decision in this review database.
            meta['review_status'] = 'pending_review'
            if 'review_status' in task:
                task['review_status'] = 'pending_review'
            for key in ('image_t1', 'image_t2', 'gt_mask', 'init_mask'):
                if task.get(key):
                    task[key] = str((path.parent / task[key]).resolve())
            for key in ('reference_mask', 'candidate_mask', 'ignore_mask'):
                if meta.get(key):
                    meta[key] = str((path.parent / meta[key]).resolve())
            for source in meta.get('source_annotations', []):
                if isinstance(source, dict) and source.get('path'):
                    source['path'] = str((path.parent / source['path']).resolve())
            tasks.append(task)
            sources.append(str(path))
    return tasks, sources


def render_task(task, directory):
    """Reuse task_data paint; source images untouched, PNG previews <=512px."""
    assets = assets_for(task)
    result = {}
    directory.mkdir(parents=True, exist_ok=True)
    for key in ('t1', 't2', 'overlay', 'reference'):
        if key == 'reference' and not assets.get('reference_mask'):
            continue
        image = read_image(assets['t1' if key == 't1' else 't2'], 'RGB')
        if key == 'overlay':
            image = paint(image, load_mask(assets['gt']), fill=True)
        elif key == 'reference':
            ref = load_mask(assets['reference_mask'])
            rgb = np.array(image)
            rgb[ref] = (rgb[ref] * .55 + np.array([0, 205, 255]) * .45).astype(np.uint8)
            rgb[outline(ref)] = [0, 220, 255]
            image = paint(Image.fromarray(rgb), load_mask(assets['gt']), fill=True)
            relation = task['meta'].get('relation_type', '')
            params = task['meta'].get('relation_params', {})
            # Text is ASCII for the default Pillow font; full parameters are shown in HTML.
            label = relation + (' ' + str(params.get('direction', '')) if relation == 'direction' else '')
            if relation == 'near':
                label += ' <= ' + str(params.get('distance_pixels', '?')) + ' px'
            ImageDraw.Draw(image).text((8, 8), label, fill='white', stroke_width=2, stroke_fill='black')
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        target = directory / (key + '.png')
        image.save(target)
        result[key] = str(target.resolve())
    return result


def build(manifests, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tasks, sources = load_manifests(manifests)
    reports, summary = batch_qc(tasks)
    prior_path = output / 'review_bundle.json'
    prior = ({r['payload']['meta']['review_id']: r for r in json.loads(prior_path.read_text(encoding='utf-8'))['records']}
             if prior_path.exists() else {})
    records = []
    for index, (task, source, qc) in enumerate(zip(tasks, sources, reports)):
        assets = {}
        observations = store.observe(list(assets_for(task).values()) + source_paths(task))
        old = prior.get(task['meta']['review_id'])
        try:
            if old and old['payload'] == task and old['observations'] == observations and all(Path(p).is_file() for p in old['assets'].values()):
                assets = old['assets']
            else:
                assets = render_task(task, output / 'images' / uuid.uuid4().hex)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            qc['findings'].append(dict(code='preview', severity='hard_error', message=str(exc)))
            qc['passed'] = False
        records.append(dict(payload=task, source=source, qc=qc, assets=assets, observations=observations))
    summary['passed'] = sum(r['qc']['passed'] for r in records)
    bundle = dict(format_version=1, full_snapshot=True, summary=summary, records=records)
    (output / 'review_bundle.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'qc_report.json').write_text(json.dumps(dict(summary=summary, tasks=[dict(review_id=t['meta']['review_id'], **q) for t, q in zip(tasks, reports)]), ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def import_bundle(db, bundle):
    if bundle.get('format_version') != 1 or bundle.get('full_snapshot') is not True:
        raise ValueError('Expected a complete review snapshot bundle')
    seen = set()
    for record in bundle['records']:
        ident = record['payload']['meta']['review_id']
        if ident in seen:
            raise ValueError('Duplicate review_id: ' + ident)
        seen.add(ident)
        if store.observe(record['observations']) != record['observations']:
            raise ValueError('Source changed after QC; rebuild bundle: ' + ident)
    db.execute('UPDATE construct_task SET active=0')
    changed = 0
    for record in bundle['records']:
        _, fresh = store.import_task(db, **record)
        changed += fresh
    return dict(total=len(seen), new_revisions=changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build_cmd = commands.add_parser('build', help='Slurm CPU: QC and preview preparation')
    build_cmd.add_argument('--manifest', action='append', required=True, type=Path)
    build_cmd.add_argument('--output', required=True, type=Path)
    import_cmd = commands.add_parser('import', help='Import full snapshot, preserve old versions and reviews')
    import_cmd.add_argument('--bundle', required=True, type=Path)
    import_cmd.add_argument('--db', required=True)
    export_cmd = commands.add_parser('export')
    export_cmd.add_argument('--db', required=True)
    export_cmd.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'build':
        result = build(args.manifest, args.output)
    elif args.command == 'import':
        with closing(store.connect(args.db)) as db, db:
            result = import_bundle(db, json.loads(args.bundle.read_text(encoding='utf-8')))
    else:
        with closing(store.connect(args.db)) as db:
            result = store.export(db)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'approved_tasks.jsonl').write_text(''.join(json.dumps(t, ensure_ascii=False) + '\n' for t in result['tasks']), encoding='utf-8')
        (args.output / 'export_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        result = dict(approved=result['total'], excluded=len(result['excluded']))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
