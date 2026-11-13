"""Offline WHU v2 audit. Run on Slurm; no APIs; never alter historical masks/scores.

C0 uses native label_unch0chi1. Frozen legacy selections remain identifiable and
are not advertised as an unbiased official-positive sample. C1 is audited, not
silently intersected or relabelled. Its original direction labels stay pending.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import numpy as np
from PIL import Image
from scipy import ndimage as ndi


def read(path):
    with Image.open(path) as im:
        return np.asarray(im) > 0


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def counts(p, g):
    return dict(tp=int((p & g).sum()), fp=int((p & ~g).sum()), fn=int((~p & g).sum()))


def metrics(c):
    t, f, n = (c[k] for k in ('tp', 'fp', 'fn'))
    return dict(c, f1=2*t/(2*t+f+n) if 2*t+f+n else 1., iou=t/(t+f+n) if t+f+n else 1.,
                precision=t/(t+f) if t+f else float(not n), recall=t/(t+n) if t+n else float(not f))


def components(mask, official):
    lab, n = ndi.label(mask, np.ones((3, 3)))
    area = np.bincount(lab.ravel(), minlength=n+1)
    hit = np.bincount(lab[official].ravel(), minlength=n+1)
    rows=[]
    for i, sl in enumerate(ndi.find_objects(lab), 1):
        rows.append(dict(id=i, pixels=int(area[i]), official_overlap=int(hit[i]),
                         official_fraction=float(hit[i]/area[i]),
                         bbox=[sl[1].start, sl[0].start, sl[1].stop, sl[0].stop],
                         qc='outside_official' if not hit[i] else 'partial_official' if hit[i] != area[i] else 'inside_official'))
    return rows


def build(a):
    out=a.output; out.mkdir(parents=True, exist_ok=True)
    frozen={s:(a.run/'whu'/f'names_{name}.txt').read_text().split() for s,name in [('val','val128'),('dev','dev60')]}
    selected=set(sum(frozen.values(), []))
    test={Path(n).name for n in (a.dataset/'list/test.txt').read_text().split()}
    names=sorted(p.name for p in (a.dataset/'img1').glob('*.png') if p.name not in test and not p.name.startswith('test_'))
    population=[]; rows=[]
    for name in names:
        b1=read(a.dataset/'slabel1'/name); b2=read(a.dataset/'slabel2'/name)
        official=read(a.dataset/'label_unch0chi1'/name); old=b1 ^ b2
        population.append(dict(name=name, legacy_positive=bool(old.any()), official_positive=bool(official.any())))
        if name not in selected: continue
        dest=out/'gt/c0_official'/name; dest.parent.mkdir(parents=True,exist_ok=True)
        Image.fromarray(official.astype('uint8')*255).save(dest)
        directions={k:components(m,official) for k,m in [('appeared',b2 & ~b1),('disappeared',b1 & ~b2)]}
        ol,n=ndi.label(official,np.ones((3,3))); votes=[]
        for i in range(1,n+1):
            region=ol==i; new=int((region & b2 & ~b1).sum()); gone=int((region & b1 & ~b2).sum()); unchanged=int((region & ~(b1 ^ b2)).sum())
            votes.append(dict(id=i,pixels=int(region.sum()),appeared_pixels=new,disappeared_pixels=gone,undirected_pixels=unchanged,
                              qc='mixed_direction' if new and gone else 'direction_supported_with_gaps' if unchanged else 'direction_supported'))
        row=dict(name=name,split='val' if name in frozen['val'] else 'dev',old_pixels=int(old.sum()),official_pixels=int(official.sum()),
                 old_vs_official=metrics(counts(old,official)),directions=directions,official_components=votes,
                 c1_status='pending_review',c0_status='pending_review',old_gt_matches_source=bool(np.array_equal(old,read(a.run/'whu/gt/c0'/name))))
        rows.append(row)
    write(out/'whu/audit_images.json',rows);write(out/'whu/population.json',population)
    manifests=[]
    for split in frozen:
        suffix='' if split=='val' else '_dev'
        for direction,task in [('any','T1.1xC0'),('appeared','T1.1xC1'),('disappeared','T1.1xC1')]:
            source=a.run/'whu/tasks'/f'{task}_{direction}{suffix}.jsonl'
            revised=[]
            for line in source.read_text().splitlines():
                r=json.loads(line); oldpath=r['gt_mask']
                if direction=='any':r['gt_mask']=str(out/'gt/c0_official'/r['id'])
                r['meta']=dict(r.get('meta',{}),data_version='whu_official_v2_20261113',review_status='pending_review',
                               legacy_gt_mask=oldpath,legacy_manifest=str(source),selection='legacy_slabel_xor_positive_frozen',
                               gt_definition='native_label_unch0chi1' if direction=='any' else 'legacy_direction_requires_component_review')
                revised.append(r)
            target=out/'whu/tasks'/source.name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(''.join(json.dumps(r)+'\n' for r in revised));manifests.append(str(target))
    # Only WHU-named configurations and fixed/rule outputs are searched.
    score=[]; missing=[]
    for config in sorted(a.run.glob('*whu*')):
        if not config.is_dir() or config.name=='whu':continue
        for finalpath in sorted(config.glob('T1.1xC*/**/final.json')):
            session=finalpath.parent; inp=json.loads((session/'input.json').read_text()); final=json.loads(finalpath.read_text())
            name=Path(inp['source_images'][0]).name
            if not (a.dataset/'label_unch0chi1'/name).exists():continue
            p=session/(final.get('mask_path') or 'MISSING')
            if not p.exists():missing.append(dict(session=str(session),reason='missing_final_mask'));continue
            direction=inp.get('change_direction','any'); pred=read(p)
            old=read(a.run/'whu/gt'/dict(any='c0',appeared='new',disappeared='demolished')[direction]/name)
            official=read(a.dataset/'label_unch0chi1'/name)
            score.append(dict(config=config.name,split=session.parent.name,direction=direction,name=name,prediction=str(p),
                              historical_eval=final.get('eval'),old=metrics(counts(pred,old)),
                              official=metrics(counts(pred,official)) if direction=='any' else None,
                              c1_pred_outside_official=int((pred & ~official).sum()) if direction!='any' else None))
    for method in ('fixed','rule'):
        for direction,kind in [('any','c0'),('appeared','new'),('disappeared','demolished')]:
            root=a.run/'whu'/method/direction
            if method=='rule':root=root/'masks'
            for p in sorted(root.glob('*.png')):
                old=read(a.run/'whu/gt'/kind/p.name); official=read(a.dataset/'label_unch0chi1'/p.name);pred=read(p)
                score.append(dict(config=method,split='val',direction=direction,name=p.name,prediction=str(p),old=metrics(counts(pred,old)),
                                  official=metrics(counts(pred,official)) if direction=='any' else None,
                                  c1_pred_outside_official=int((pred & ~official).sum()) if direction!='any' else None))
    write(out/'whu/rescored_predictions.json',score);write(out/'whu/missing_predictions.json',missing)
    groups=defaultdict(list)
    for r in score:groups[(r['config'],r['split'],r['direction'])].append(r)
    aggregates=[]
    for (config,split,direction),rr in groups.items():
        d=dict(config=config,split=split,direction=direction,n=len(rr))
        for version in ('old','official'):
            if rr[0][version] is None:continue
            d[version]=metrics({k:sum(r[version][k] for r in rr) for k in ('tp','fp','fn')})
            d[version]['macro_f1']=float(np.mean([r[version]['f1'] for r in rr]))
        aggregates.append(d)
    write(out/'whu/rescore_summary.json',aggregates)
    summary=dict(status='pending_review',api_calls=0,manifests=manifests,population=dict(images=len(population),
                 legacy_positive=sum(r['legacy_positive'] for r in population),official_positive=sum(r['official_positive'] for r in population),
                 official_positive_excluded_by_legacy=sum(r['official_positive'] and not r['legacy_positive'] for r in population)),splits={})
    for split in frozen:
        rr=[r for r in rows if r['split']==split]
        summary['splits'][split]=dict(images=len(rr),official_empty_old_nonempty=sum(r['official_pixels']==0 and r['old_pixels']>0 for r in rr),
            old_vs_official=metrics({k:sum(r['old_vs_official'][k] for r in rr) for k in ('tp','fp','fn')}),
            c1_components={qc:sum(c['qc']==qc for r in rr for cs in r['directions'].values() for c in cs) for qc in ('outside_official','partial_official','inside_official')},
            old_gt_mismatch=sum(not r['old_gt_matches_source'] for r in rr))
    summary['rescored_predictions']=len(score);summary['config_groups']=len(aggregates);summary['missing_predictions']=len(missing)
    write(out/'whu/summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    build(p.parse_args())
