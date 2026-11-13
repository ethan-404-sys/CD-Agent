"""Render a separate 19-image review from completed audits; no scoring/construction.

The original 17-page review, all candidate manifests and all GT stay unchanged.
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from revise_t5_review import page
from revise_whu_official import write


def build(a):
    cases=json.loads((a.source/'review_selection.json').read_text())
    for c in cases:
        if 'WHU official_positive' in c['title']:
            pixels=c['qc']['official_pixels']
            c['title']=c['title'].replace('official_positive','tiny_official_residual')+f' ({pixels} px)'
            c['reason']=f'Official GT contains only {pixels} positive pixels. This is a tiny annotation-residual diagnostic, not a representative building-change positive. Preserve the official mask; pending human review, no automatic relabelling.'
            c['qc']['review_note']='tiny_official_residual_requires_visual_review; source GT preserved'
    audit=json.loads((a.source/'whu/audit_images.json').read_text())
    used={Path(c['t2']).name for c in cases if '/WHU-CD/' in c['t2']}
    available=[r for r in audit if r['official_pixels']>=2000 and r['name'] not in used and r['split']=='val']
    chosen=[]
    for mode in ('substantial_change_with_disagreement','substantial_change_with_agreement'):
        ordered=sorted(available,key=lambda r:r['old_vs_official']['iou'],reverse=mode.endswith('_agreement'))
        r=next(r for r in ordered if r['name'] not in {x['name'] for x in chosen});chosen.append(r);name=r['name']
        cases.append(dict(title='WHU '+mode+' '+name,t1=str(a.whu/'img1'/name),t2=str(a.whu/'img2'/name),
            old_gt=str(a.run/'whu/gt/c0'/name),new_gt=str(a.source/'gt/c0_official'/name),official_gt=str(a.whu/'label_unch0chi1'/name),
            instruction='Find newly built or demolished buildings.',
            reason=f'Visible-scale official change ({r["official_pixels"]} pixels); selected from the completed frozen val audit for review, not for new evaluation. Legacy-versus-official IoU={r["old_vs_official"]["iou"]:.4f}.',qc=r))
    for c in cases:
        if Path(c['t2']).name == 'train_250.png':
            c['reason'] += ' ' + 'Visual review: train_250 has a large roof in both dates with roof/ancillary-structure changes. Its official 146444-pixel positive mask may conflict with the historical building_rebuild_excluded scope. Treat it as an official-label versus task-definition diagnostic, not confirmed pure construction/demolition; human review required.'
    assert len(cases)==19 and len({(c['t1'],c['t2']) for c in cases})==19 and len({c['t2'] for c in cases})==19
    a.output.mkdir(parents=True,exist_ok=True);page(SimpleNamespace(output=a.output),cases)
    summary=json.loads((a.output/'review_summary.json').read_text());summary.update(source_review=str(a.source/'index.html'),
        independent_image_pairs=19,independent_t2_images=19,annotation='pages03/04 clarify tiny official residuals; pages18/19 add substantial official positives',
        source_gt_or_candidates_changed=False,new_scoring_runs=0)
    write(a.output/'review_summary.json',summary)
    print(json.dumps(dict(summary=summary,added=[dict(name=r['name'],official_pixels=r['official_pixels'],iou=r['old_vs_official']['iou']) for r in chosen])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--whu',type=Path,required=True);build(p.parse_args())
