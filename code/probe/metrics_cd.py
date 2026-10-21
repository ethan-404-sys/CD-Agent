"""Binary change metrics; exp5-env, NumPy/SciPy only, no model or data I/O.

512x512 image: estimated <100 MB scratch, <1 s per mask (not measured).
Boundary = mask minus 3x3 erosion, outside image is background. BF uses
Euclidean nearest-boundary distance <=1/2/4/5 pixels, independently in both
directions (many-to-one). Boundary IoU uses INNER bands mask minus d iterations
of 3x3 erosion, including the image frame: d=max(1, round(.02*hypot(H,W)))
(14 for 512 squared), also d=3. Thus bands use the square-grid/Chebyshev metric.

Objects are binary 8-connected components, without semantic-transition splits.
A GT object is hit if >=50% is covered by the prediction union; a predicted
object is correct if >=50% overlaps the GT union. No one-to-one assignment.
Buckets: <100, [100,1000], (1000,5000], >5000 (no duplicated endpoints).

Perimeter counts exposed unit pixel edges (4-neighbour, including holes/frame).
Hull perimeter is the Euclidean perimeter of the convex hull of all foreground
pixel squares in the image (SciPy ConvexHull.area in 2D). Roughness = perimeter /
hull perimeter; it includes disconnected fragments, so read with fragment count.
Empty mask: both perimeters 0, roughness None. One pixel: 4/4=1. Dataset
roughness = sum(perimeters)/sum(hull perimeters), not mean image roughness.
All zero-denominator precision/recall/F1/IoU values are 0, matching common.py;
empty size buckets and all-empty roughness are None.

New false objects = max(0, number of zero-GT-overlap components after minus
that number before), per image. This is NET count increase, not spatial identity
tracking; deletion-induced fragmentation can increase it. Both counts and the
signed change are retained. Omitting before leaves this metric unavailable.
All counts are additive. Ratios are derived only after summation for micro
metrics; per-image means/quantiles are separately labelled as distributions.

Split/merge use positive pixel overlap; merge_rate_50 requires >=50% of each
GT object covered by a single predicted component. F1 and IoU are monotonically
equivalent under the same aggregation. With before supplied, retain added and
deleted pixels plus delta F1/Boundary IoU; harmful editing is delta F1 < -.005
among actually edited images. No edited images means an unavailable rate.
"""
from __future__ import annotations

import json

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull

CONNECTIVITY = np.ones((3, 3), bool)
BUCKETS = ("lt100", "100_to_1000", "1000_to_5000", "gt5000")


def boundary(mask):
    mask = np.asarray(mask, bool)
    return mask & ~ndimage.binary_erosion(mask, structure=CONNECTIVITY, border_value=0)


def ratio(n, d, empty=0.0):
    return float(n / d) if d else empty


def pixel_counts(pred, gt):
    return dict(tp=int(np.count_nonzero(pred & gt)), fp=int(np.count_nonzero(pred & ~gt)),
                fn=int(np.count_nonzero(~pred & gt)))


def pixel_f1(counts):
    return ratio(2 * counts["tp"], 2 * counts["tp"] + counts["fp"] + counts["fn"])


def component_counts(mask, other):
    labels, n = ndimage.label(mask, structure=CONNECTIVITY)
    areas = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    overlap = np.bincount(labels[other], minlength=n + 1)[1:]
    return areas, overlap


def perimeters(mask):
    if not mask.any():
        return 0, 0.0
    padded = np.pad(mask, 1)
    perimeter = int(np.count_nonzero(padded[1:] != padded[:-1])
                    + np.count_nonzero(padded[:, 1:] != padded[:, :-1]))
    points = np.argwhere(boundary(mask))
    corners = np.concatenate([points + offset for offset in ((0, 0), (0, 1), (1, 0), (1, 1))])
    hull = ConvexHull(np.unique(corners, axis=0))
    return perimeter, float(hull.area)


def metric_values(c):
    """Derive ratios from a single image's counts or their dataset sum."""
    tp, fp, fn = (c[k] for k in ("tp", "fp", "fn"))
    result = dict(pixel_precision=ratio(tp, tp + fp), pixel_recall=ratio(tp, tp + fn),
                  pixel_f1=pixel_f1(c), pixel_iou=ratio(tp, tp + fp + fn))
    for d in (1, 2, 4, 5):
        p = ratio(c[f"bf{d}_pred_matched"], c["pred_boundary"])
        r = ratio(c[f"bf{d}_gt_matched"], c["gt_boundary"])
        result.update({f"bf{d}_precision": p, f"bf{d}_recall": r, f"bf{d}_f1": ratio(2*p*r, p+r)})
    for tag in ("diag", "3"):
        result[f"biou_{tag}"] = ratio(c[f"biou_{tag}_intersection"], c[f"biou_{tag}_union"])
    p = ratio(c["pred_objects_correct"], c["pred_objects"])
    r = ratio(c["gt_objects_hit"], c["gt_objects"])
    result.update(object_precision=p, object_recall=r, object_f1=ratio(2*p*r, p+r))
    result.update(split_rate=ratio(c['gt_split'], c['gt_objects']),
                  merge_rate=ratio(c['pred_merged'], c['pred_objects']),
                  merge_rate_50=ratio(c['pred_merged_50'], c['pred_objects']))
    for bucket in BUCKETS:
        result[f"object_recall_{bucket}"] = ratio(c[f"gt_hit_{bucket}"], c[f"gt_total_{bucket}"], None)
    for tag in ("pred", "gt"):
        result[f"{tag}_roughness"] = ratio(c[f"{tag}_perimeter"], c[f"{tag}_hull_perimeter"], None)
    return result


def image_metrics(pred, gt, before=None):
    """Return JSON-compatible additive counts, derived ratios and band width.

    See module docstring for distances, roughness, buckets and empty conventions.
    Inputs are matching 2-D binary arrays; nonzero entries are foreground.
    """
    pred, gt = np.asarray(pred, bool), np.asarray(gt, bool)
    if pred.ndim != 2 or pred.shape != gt.shape or not pred.size:
        raise ValueError("pred and gt must be nonempty matching 2-D arrays")
    c = dict(images=1, **pixel_counts(pred, gt))
    pb, gb = boundary(pred), boundary(gt)
    c.update(pred_boundary=int(pb.sum()), gt_boundary=int(gb.sum()))
    pd = ndimage.distance_transform_edt(~pb) if pb.any() else np.full(pred.shape, np.inf)
    gd = ndimage.distance_transform_edt(~gb) if gb.any() else np.full(gt.shape, np.inf)
    for d in (1, 2, 4, 5):
        c[f"bf{d}_pred_matched"] = int(np.count_nonzero(pb & (gd <= d)))
        c[f"bf{d}_gt_matched"] = int(np.count_nonzero(gb & (pd <= d)))
    diag_d = max(1, int(round(0.02 * np.hypot(*pred.shape))))
    for tag, d in (("diag", diag_d), ("3", 3)):
        pband = pred & ~ndimage.binary_erosion(pred, CONNECTIVITY, iterations=d, border_value=0)
        gband = gt & ~ndimage.binary_erosion(gt, CONNECTIVITY, iterations=d, border_value=0)
        c[f"biou_{tag}_intersection"] = int(np.count_nonzero(pband & gband))
        c[f"biou_{tag}_union"] = int(np.count_nonzero(pband | gband))
    pa, po = component_counts(pred, gt)
    ga, go = component_counts(gt, pred)
    # Any positive overlap counts for split/merge. The stricter merge version
    # requires each of at least two GT objects to be >=50% covered by THIS CC.
    pl, pn = ndimage.label(pred, CONNECTIVITY)
    gl, gn = ndimage.label(gt, CONNECTIVITY)
    both = pred & gt
    pairs, overlaps = np.unique(pl[both].astype(np.int64) * (gn + 1) + gl[both],
                                return_counts=True)
    pi, gi = pairs // (gn + 1), pairs % (gn + 1)
    c.update(gt_split=int((np.bincount(gi, minlength=gn + 1)[1:] >= 2).sum()),
             pred_merged=int((np.bincount(pi, minlength=pn + 1)[1:] >= 2).sum()),
             pred_merged_50=int((np.bincount(pi[2 * overlaps >= ga[gi - 1]],
                                            minlength=pn + 1)[1:] >= 2).sum()))
    hits = 2 * go >= ga
    c.update(pred_objects=len(pa), gt_objects=len(ga), pred_objects_correct=int((2*po >= pa).sum()),
             gt_objects_hit=int(hits.sum()), pred_fragments_lt20=int((pa < 20).sum()),
             gt_fragments_lt20=int((ga < 20).sum()), pred_false_objects=int((po == 0).sum()))
    takes = (ga < 100, (ga >= 100) & (ga <= 1000), (ga > 1000) & (ga <= 5000), ga > 5000)
    for bucket, take in zip(BUCKETS, takes):
        c[f"gt_hit_{bucket}"] = int((hits & take).sum())
        c[f"gt_total_{bucket}"] = int(take.sum())
    for tag, mask in (("pred", pred), ("gt", gt)):
        c[f"{tag}_perimeter"], c[f"{tag}_hull_perimeter"] = perimeters(mask)
    c.update(before_images=0, before_false_objects=0, false_objects_change=0, new_false_objects=0)
    c.update(added_pixels=0, deleted_pixels=0, edited_images=0, harmful_images=0)
    deltas = {}
    if before is not None:
        before = np.asarray(before, bool)
        if before.shape != gt.shape:
            raise ValueError("before and gt shapes differ")
        _, bo = component_counts(before, gt)
        old = int((bo == 0).sum())
        c.update(before_images=1, before_false_objects=old,
                 false_objects_change=c["pred_false_objects"] - old,
                 new_false_objects=max(0, c["pred_false_objects"] - old))
        old_metrics = image_metrics(before, gt)['metrics']
        current_metrics = metric_values(c)
        deltas = {f'delta_{key}': current_metrics[key] - old_metrics[key]
                  for key in ('pixel_f1', 'biou_diag', 'biou_3')}
        added, deleted = int((pred & ~before).sum()), int((before & ~pred).sum())
        c.update(added_pixels=added, deleted_pixels=deleted,
                 edited_images=int(added + deleted > 0),
                 harmful_images=int(added + deleted > 0 and deltas['delta_pixel_f1'] < -0.005))
    values = metric_values(c)
    values.update(pred_fragments=c["pred_objects"], pred_fragments_lt20=c["pred_fragments_lt20"],
                  gt_fragments=c["gt_objects"], gt_fragments_lt20=c["gt_fragments_lt20"],
                  new_false_objects=c["new_false_objects"] if before is not None else None)
    values.update(added_pixels=c['added_pixels'] if before is not None else None,
                  deleted_pixels=c['deleted_pixels'] if before is not None else None)
    return dict(counts=c, metrics=values, boundary_iou_diag_d=diag_d, **deltas)


def distribution(values):
    values = np.asarray([v for v in values if v is not None], np.float64)
    if not len(values):
        return dict(n=0, mean=None, p10=None, p50=None, p90=None, min=None, max=None)
    q = np.quantile(values, [0.1, 0.5, 0.9])
    return dict(n=len(values), mean=float(values.mean()), p10=float(q[0]), p50=float(q[1]),
                p90=float(q[2]), min=float(values.min()), max=float(values.max()))


def aggregate(records):
    """Micro ratios from additive A-D counts; separately return image distributions."""
    records = list(records)
    if not records:
        raise ValueError("aggregate requires at least one image")
    keys = records[0]["counts"].keys()
    totals = {key: sum(r["counts"][key] for r in records) for key in keys}
    if totals["before_images"] not in (0, totals["images"]):
        raise ValueError("before must be supplied for all images or none")
    result = dict(images=len(records), counts=totals, micro=metric_values(totals),
                distributions={key: distribution(r["metrics"][key] for r in records)
                               for key in records[0]["metrics"]},
                boundary_iou_diag_d=distribution(r["boundary_iou_diag_d"] for r in records))
    if totals['before_images']:
        result.update(harmful_edit_rate=ratio(totals['harmful_images'], totals['edited_images'], None),
                      edited_images=totals['edited_images'], harmful_images=totals['harmful_images'])
        for source, destination in (('delta_pixel_f1', 'delta_f1'),
                                    ('delta_biou_diag', 'delta_biou_diag'),
                                    ('delta_biou_3', 'delta_biou_3')):
            result[destination] = delta_distribution(records, source)
    return result


def delta_distribution(records, key):
    values = np.asarray([r[key] for r in records])
    edited = [r for r in records if r['counts']['edited_images']]
    return dict(**distribution(values), improve_fraction=float(np.mean(values > 0)),
                decline_fraction=float(np.mean(values < 0)), unchanged_fraction=float(np.mean(values == 0)),
                harmful_edit_rate=ratio(sum(r[key] < -0.005 for r in edited), len(edited), None),
                worst5=[dict(image=r.get('image'), **{key: r[key]}, delta=r[key])
                        for r in sorted(records, key=lambda r: (r[key], r.get('image', '')))[:5]])


def six_numbers(pred, gt, before, *, dataset_decline_fraction=None):
    """Single-image five values plus externally supplied full-test decline fraction.

    Three masks cannot determine a dataset statistic. The sixth value is None
    until the caller passes summary['rows'][row]['delta_f1']['decline_fraction']
    via dataset_decline_fraction. Never substitute this image's decline flag.
    """
    m = image_metrics(pred, gt, before)["metrics"]
    return dict(pixel_f1=m["pixel_f1"], pixel_iou=m["pixel_iou"], boundary_f1_d2=m["bf2_f1"],
                object_f1=m["object_f1"], new_false_objects=m["new_false_objects"],
                decline_fraction=dataset_decline_fraction)


def self_test():
    """Small synthetic definition checks; not a substitute for the real-data run."""
    gt = np.zeros((512, 512), bool)
    gt[64:448, 64:448] = True
    saw = gt.copy()
    for x in range(68, 444, 8):
        saw[64:68, x:x+4] = False
    clean, jagged = image_metrics(gt, gt, gt), image_metrics(saw, gt, gt)
    a, b = clean["metrics"], jagged["metrics"]
    assert b["pred_roughness"] > a["pred_roughness"]
    assert b["bf2_f1"] < a["bf2_f1"]
    assert abs(a["pixel_f1"] - b["pixel_f1"]) < 0.01
    empty = np.zeros((8, 8), bool)
    single = empty.copy()
    single[0, 0] = True
    assert image_metrics(empty, empty)["metrics"]["pred_roughness"] is None
    one = image_metrics(single, single, empty)
    assert one["metrics"]["pred_roughness"] == 1
    assert one["metrics"]["bf2_f1"] == one["metrics"]["biou_3"] == 1
    assert image_metrics(single, empty, empty)["counts"]["new_false_objects"] == 1
    diagonal = np.eye(8, dtype=bool)
    assert image_metrics(diagonal, diagonal)["counts"]["pred_objects"] == 1
    merged = aggregate([clean, jagged])
    assert merged["counts"]["tp"] == clean["counts"]["tp"] + jagged["counts"]["tp"]
    assert six_numbers(single, single, empty)["decline_fraction"] is None
    split_gt = np.zeros((20, 20), bool)
    split_gt[4:16, 3:17] = True
    split_pred = split_gt.copy()
    split_pred[:, 9:11] = False
    assert image_metrics(split_pred, split_gt)['metrics']['split_rate'] == 1
    merged_objects = image_metrics(split_gt, split_pred)['metrics']
    assert merged_objects['merge_rate'] == merged_objects['merge_rate_50'] == 1
    thin = np.zeros_like(split_gt)
    thin[8, 3:17] = True
    thin_m = image_metrics(thin, split_pred)['metrics']
    assert thin_m['merge_rate'] == 1 and thin_m['merge_rate_50'] == 0
    changed = image_metrics(empty, single, single)
    changed['image'] = 'deleted'
    noedit = image_metrics(single, single, single)
    noedit['image'] = 'same'
    edits = aggregate([changed, noedit])
    assert edits['edited_images'] == 1 and edits['harmful_edit_rate'] == 1
    assert edits['distributions']['deleted_pixels']['mean'] == 0.5
    assert edits['delta_biou_diag']['decline_fraction'] == 0.5
    assert aggregate([noedit])['harmful_edit_rate'] is None
    for d in (1, 2, 4, 5):
        assert one['metrics'][f'bf{d}_f1'] == 1
    shifted = np.zeros_like(split_gt)
    shifted[4:16, 6:20] = True
    shifted_metrics = image_metrics(shifted, split_gt)['metrics']
    assert shifted_metrics['bf1_f1'] < shifted_metrics['bf4_f1']
    assert abs(b['pixel_f1'] - 2*b['pixel_iou']/(1+b['pixel_iou'])) < 1e-12
    return dict(status="passed", square=a, sawtooth=b)


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2, allow_nan=False))
