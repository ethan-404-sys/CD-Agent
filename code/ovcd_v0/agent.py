"""OVCD-v0 orchestrated agent loop (CPU), replaying cached MLLM answers.

State per image: current mask M_t, the stage-A queue (components of M0), the stage-B queue (T7
suspicious regions), and the used budget.  The program walks A -> B -> C:

  A  for each component in suspicion order: T2 zoom -> T3 verify (1 call) -> T4 delete if P(yes) < tau_a
  B  for each of the first b_max candidates not already covered by M_t: T2 -> T3 (1 call); if
     P(yes) >= tau_b: T5 box (1 call) -> SAM box on T1 and on T2, union added to M_t; if the box is
     unusable, or no budget is left for T5, add the candidate's own mask (fallback, logged)
  C  for each component of M_t at the start of C: T6 SAM box prompt on T1 and T2 with the component
     bbox, keep the output with the higher IoU to the component, accept it if that IoU >= c_gate (no
     MLLM call).  The result is (M_t minus every accepted old component) | (every accepted new mask),
     which does not depend on the visiting order; each step logs the cumulative state.

Stage A may use at most round(a_share * k) calls; stage B gets the rest of k.  An item the loop
needs but has no cached answer for is an error, never a silent skip.  Every step is logged (step,
stage, action, item, bbox, images sent, raw answer, P(yes), mask op, image F1 before/after).
"""
from __future__ import annotations
import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from common import RUN, components, image_f1, load_gt, load_mask_png, read_jsonl, write_json
import items as I
import render


@dataclass
class Config:
    tag: str = 'r1'              # item / call cache tag
    k: int = 20                  # MLLM calls per image
    a_share: float = 0.5         # fraction of k stage A may use
    a_order: str = 'score_asc'   # stage-A suspicion order (attribute + direction)
    tau_a: float = 0.5           # delete when P(yes) < tau_a
    b_order: str = 'queue'       # stage-B order ('queue' = order written by T7)
    tau_b: float = 0.5           # add when P(yes) >= tau_b
    b_cover: float = 0.5         # skip a candidate already covered by M_t above this fraction
    b_max: int = 20              # stage-B queue = the first b_max T7 candidates (all have cached answers)
    use_t5: bool = True          # False: add the candidate mask directly (no box call)
    c_mode: str = 'none'         # none | box
    c_gate: float = 0.0          # replace only when IoU(new, old) >= c_gate
    stages: str = 'ABC'


def sort_items(its, order):
    if order == 'queue':
        return list(its)
    key, direction = order.rsplit('_', 1)
    return sorted(its, key=lambda it: it[key], reverse=direction == 'desc')


class Image:
    def __init__(self, split, name, m0, cfg_tag, gt=None, prompter=None):
        self.split, self.name = split, name
        self.m0 = m0
        self.gt = gt if gt is not None else load_gt(split, name)
        self.store = I.Store(cfg_tag, split, name)
        calls = read_jsonl(RUN / 'calls' / cfg_tag / split / (Path(name).stem + '.jsonl'))
        self.calls = {r['uid']: r for r in calls}
        self.prompter = prompter
        self._sam = {}

    def call(self, uid):
        if uid not in self.calls:
            raise KeyError(f'{self.split}/{self.name}: no cached MLLM answer for item {uid}')
        return self.calls[uid]

    def sam_box(self, t, box):
        key = (t, tuple(round(float(v), 1) for v in box))
        if key not in self._sam:
            self._sam[key] = self.prompter.box(self.split, self.name, t, box)
        return self._sam[key]


def run(img: Image, cfg: Config, log=True):
    t_start = time.time()
    M = img.m0.copy()
    gt = img.gt
    used = 0
    traj = []
    f1 = image_f1(M, gt)
    snap = {'M0': M.copy()}

    def step(**kw):
        nonlocal f1
        new = image_f1(M, gt)
        if log:
            kw.update(step=len(traj) + 1, f1_before=f1, f1_after=new, calls_used=used)
            traj.append(kw)
        f1 = new

    # ---- stage A: verify existing components, delete false alarms
    if 'A' in cfg.stages:
        cap = int(round(cfg.a_share * cfg.k))
        for it in sort_items(img.store.meta['A'], cfg.a_order):
            if used >= cap:
                break
            c = img.call(it['uid'])
            used += 1
            v = c['verify']
            op = 'keep'
            if v['p_yes'] < cfg.tau_a:
                M &= ~img.store.mask(it['uid'])
                op = 'T4_delete'
            step(stage='A', action='T2+T3' + ('+T4' if op != 'keep' else ''), uid=it['uid'],
                 bbox=it['bbox'], images=c['images'], raw=v['raw'], p_yes=v['p_yes'],
                 type=v.get('type'), op=op, mllm_seconds=v['seconds'])
    snap['A'] = M.copy()
    t_a = time.time()

    # ---- stage B: suspicious regions, add misses
    box_fail = box_try = 0
    if 'B' in cfg.stages:
        for it in sort_items(img.store.meta['B'][:cfg.b_max], cfg.b_order):
            if used >= cfg.k:
                break
            cm = img.store.mask(it['uid'])
            if (cm & M).sum() > cfg.b_cover * cm.sum():
                continue  # already in the mask: no call
            c = img.call(it['uid'])
            used += 1
            v = c['verify']
            if v['p_yes'] < cfg.tau_b:
                step(stage='B', action='T2+T3', uid=it['uid'], bbox=it['bbox'], images=c['images'],
                     raw=v['raw'], p_yes=v['p_yes'], type=v.get('type'), op='skip', mllm_seconds=v['seconds'])
                continue
            add, how, braw = cm, 'candidate_mask', None
            if cfg.use_t5 and used < cfg.k:
                used += 1
                box_try += 1
                braw = c['box']['raw']
                bn = c['box']['box']
                if bn is not None:
                    fb = render.crop_to_full(bn, c['crop'])
                    m1, _ = img.sam_box(0, fb)
                    m2, _ = img.sam_box(1, fb)
                    add, how = m1 | m2, 'T5_box_sam_union'
                else:
                    box_fail += 1
                    how = 'T5_failed_fallback_candidate'
            elif cfg.use_t5:
                how = 'no_budget_for_T5_fallback_candidate'
            M_before = M.copy()
            M |= add
            step(stage='B', action='T2+T3+T5' if braw is not None else 'T2+T3+add', uid=it['uid'],
                 bbox=it['bbox'], images=c['images'], raw=v['raw'], p_yes=v['p_yes'], type=v.get('type'),
                 box_raw=braw, op=how, added_px=int((add & ~M_before).sum()),
                 mllm_seconds=v['seconds'] + (c['box']['seconds'] if braw is not None else 0))
    snap['B'] = M.copy()
    t_b = time.time()

    # ---- stage C: SAM re-segmentation of each kept component (order-independent)
    if 'C' in cfg.stages and cfg.c_mode == 'box':
        entry = M.copy()
        drop = np.zeros_like(M)
        keep_new = np.zeros_like(M)
        lab, comps = components(entry)
        for comp in comps:
            old = lab == comp['id']
            x0, y0, x1, y1 = comp['bbox']
            box = [x0, y0, x1 - 1, y1 - 1]
            best = None
            for t in (0, 1):
                m, s = img.sam_box(t, box)
                iou = (m & old).sum() / max((m | old).sum(), 1)
                if best is None or iou > best[0]:
                    best = (iou, t, m, s)
            iou, t, m, s = best
            op = 'T6_replace' if iou >= cfg.c_gate else 'T6_reject'
            if op == 'T6_replace':
                drop |= old
                keep_new |= m
                M = (entry & ~drop) | keep_new
            step(stage='C', action='T6', uid=f"C:{comp['id']}", bbox=comp['bbox'], op=op, sam_time=t,
                 iou_old_new=float(iou), sam_score=s)
    snap['C'] = M.copy()
    stats = dict(calls=used, box_try=box_try, box_fail=box_fail,
                 seconds=dict(A=t_a - t_start, B=t_b - t_a, C=time.time() - t_b))
    return snap, traj, stats


def main(a):
    """Write final masks + trajectories for one named config (used for the chosen configs)."""
    from common import names, MCI, save_mask_png
    from sam_tools import Prompter
    cfg = Config(**json.loads(a.config))
    prompter = Prompter('cpu')
    out = RUN / 'agent' / a.name / a.split
    for name in names(a.split)[a.shard::a.shards]:
        m0 = load_mask_png(MCI / a.split / 'm0' / name)
        img = Image(a.split, name, m0, cfg.tag, prompter=prompter)
        snap, traj, stats = run(img, cfg)
        for st, m in snap.items():
            save_mask_png(out / 'masks' / st / name, m)
        write_json(out / 'traj' / (Path(name).stem + '.json'),
                   dict(name=name, split=a.split, config=asdict(cfg), stats=stats, steps=traj))
        print(name, stats, flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--name', required=True)
    p.add_argument('--config', required=True, help='JSON of Config fields')
    p.add_argument('--split', choices=['val', 'test'], required=True)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    main(p.parse_args())
