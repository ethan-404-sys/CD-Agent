"""逐图累积 micro 指标；候选并集避免重复计算新增像素。"""
from __future__ import annotations

from exp8s_common import np, put, overlap, counts, scores


def ratio(a, b):
    return a / b if b else None


def target_stats(objects, targets, union):
    best = np.zeros(len(targets), np.float64)
    fragments = np.zeros(len(targets), np.int64)
    for obj in objects:
        for i, target in enumerate(targets):
            inter = overlap(obj, target)
            best[i] = max(best[i], inter / (obj['area'] + target['area'] - inter))
            fragments[i] += inter >= .1 * target['area']
    hit = best >= .5
    covered = 0
    for obj in targets:
        x0, y0, x1, y1 = obj['bbox']
        covered += int((union[y0:y1, x0:x1] & obj['mask']).sum())
    return dict(targets=len(targets), hit=[int((best >= t).sum()) for t in (.3, .4, .5, .6, .7)],
                fragment_sum=int(fragments[hit].sum()), fragment_objects=int(hit.sum()),
                target_pixels=sum(o['area'] for o in targets), covered_pixels=covered,
                hit_ids=np.flatnonzero(hit).tolist())


def image_stats(objects, gt, base, missed, no_signal, f1):
    union = np.zeros(gt.shape, bool)
    oracle = base.copy()
    exp8 = base.copy()
    positive = 0
    for obj in objects:
        put(union, obj)
        x0, y0, x1, y1 = obj['bbox']
        a = int((gt[y0:y1, x0:x1] & obj['mask']).sum())
        precision = a / obj['area']
        if precision > f1 / 2:
            put(oracle, obj)
            positive += 1
        if precision >= .5:
            put(exp8, obj)
    main = target_stats(objects, missed, union)
    ns = target_stats(objects, no_signal, union)
    return dict(candidates=len(objects), small=sum(o['area'] < 100 for o in objects),
                positive=positive, summed_area=sum(o['area'] for o in objects),
                union_area=int(union.sum()), image_area=gt.size,
                fn_pixels=int((gt & ~base).sum()), fn_covered=int((gt & ~base & union).sum()),
                main=main, no_signal=ns, oracle=counts(oracle, gt), exp8=counts(exp8, gt))


class Accumulator:
    def __init__(self):
        self.counts = []
        self.total = dict(small=0, positive=0, summed_area=0, union_area=0, image_area=0,
                          fn_pixels=0, fn_covered=0)
        self.targets = {kind: dict(targets=0, hit=[0]*5, fragment_sum=0, fragment_objects=0,
                                   target_pixels=0, covered_pixels=0) for kind in ('main', 'no_signal')}
        self.oracle = {kind: dict(tp=0, fp=0, fn=0) for kind in ('oracle', 'exp8')}

    def add(self, row):
        self.counts.append(row['candidates'])
        for key in self.total:
            self.total[key] += row[key]
        for kind, obj in self.targets.items():
            for key in obj:
                if key == 'hit':
                    obj[key] = [a+b for a, b in zip(obj[key], row[kind][key])]
                else:
                    obj[key] += row[kind][key]
        for kind, obj in self.oracle.items():
            for key in obj:
                obj[key] += row[kind][key]

    def finish(self, baseline):
        main, ns = self.targets['main'], self.targets['no_signal']
        total, n = self.total, sum(self.counts)
        f1, iou = scores(baseline)
        result = dict(images=len(self.counts), candidates=n,
                      candidates_mean=float(np.mean(self.counts)), candidates_p90=float(np.quantile(self.counts, .9)),
                      area_fraction=ratio(total['summed_area'], total['image_area']),
                      union_area_fraction=ratio(total['union_area'], total['image_area']),
                      R_obj_03=ratio(main['hit'][0], main['targets']),
                      R_obj_05=ratio(main['hit'][2], main['targets']),
                      AR_obj=ratio(sum(main['hit']), 5*main['targets']),
                      R_pix=ratio(total['fn_covered'], total['fn_pixels']),
                      Frag=ratio(main['fragment_sum'], main['fragment_objects']),
                      small_lt100_fraction=ratio(total['small'], n), Pos_fraction=ratio(total['positive'], n),
                      Pos_percent=ratio(100*total['positive'], n),
                      no_signal_R_obj_05=ratio(ns['hit'][2], ns['targets']),
                      no_signal_R_pix=ratio(ns['covered_pixels'], ns['target_pixels']),
                      baseline_f1=f1, baseline_iou=iou,
                      denominators=dict(candidates=n, missed_objects=main['targets'], fn_pixels=total['fn_pixels'],
                                        no_signal_objects=ns['targets'], no_signal_pixels=ns['target_pixels']))
        for kind, c in self.oracle.items():
            new_f1, new_iou = scores(c)
            result.update({f'{kind}_delta_f1_pp': 100*(new_f1-f1),
                           f'{kind}_delta_iou_pp': 100*(new_iou-iou), f'{kind}_counts': c})
        return result
