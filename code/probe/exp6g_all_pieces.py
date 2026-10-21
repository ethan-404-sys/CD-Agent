"""All 6a pool pieces of one SECOND test image, full 512x512 views (exp5-env, CPU, seconds)."""
import json, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
from PIL import Image
from skimage import measure
from exp6_pool_io import POOL, load_piece_masks

SECOND = Path("/guisongxia01/pangchao/wangyihan/datasets/lim1/SECOND")
FACETS = ["B", "G0.05", "C1", "C2", "H1", "pred"]

def facet(r):
    if r["kind"] == "pred": return "pred"
    return r["subsource"] if r["source"] == "C" else r["source"]

def full(bbox, local):
    m = np.zeros((512, 512), bool); x0, y0, x1, y1 = bbox; m[y0:y1, x0:x1] = local; return m

def contour(ax, mask, color, lw=0.6):
    for c in measure.find_contours(np.pad(mask, 1).astype(float), 0.5):
        ax.plot(c[:, 1] - 1, c[:, 0] - 1, color=color, lw=lw)

def mark(ax, r):
    x0, y0, x1, y1 = r["bbox"]
    ax.add_patch(Rectangle((x0 - 2.5, y0 - 2.5), x1 - x0 + 4, y1 - y0 + 4, fill=False, ec="red", lw=3))

def panel(t2, masks, title, out, gt=None, colors=None, legend=None, target=None, rng=None):
    fig, ax = plt.subplots(figsize=(8.6, 9.2))
    img = t2.astype(float) / 255
    over = img.copy()
    for i, m in enumerate(masks):
        col = colors[i] if colors is not None else rng.random(3)
        over[m] = 0.5 * over[m] + 0.5 * np.asarray(col)
    ax.imshow(over)
    for i, m in enumerate(masks):
        contour(ax, m, "black" if colors is None else "black", 0.5)
    if gt is not None: contour(ax, gt, "white", 1.4)
    if target is not None: mark(ax, target)
    ax.set_title(title, fontsize=11); ax.set_xticks([]); ax.set_yticks([]); ax.set_xlim(-0.5, 511.5); ax.set_ylim(511.5, -0.5)
    if legend: ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=4, fontsize=9, frameon=False)
    fig.tight_layout(); fig.savefig(out, dpi=110); plt.close(fig)

def main(name, target_id, out):
    out.mkdir(parents=True, exist_ok=True)
    rows = [r for r in map(json.loads, open(POOL / "second/pieces.jsonl")) if r["image"] == name]
    masks = load_piece_masks("second", name)
    t2 = np.asarray(Image.open(SECOND / "img2" / name).convert("RGB"))
    gt = np.asarray(Image.open(SECOND / "label_unch0chi255" / name)) > 0
    full_masks = {r["piece_id"]: full(*masks[r["piece_id"]]) for r in rows}
    target = next(r for r in rows if r["piece_id"] == target_id)
    tgt = lambda rs: target if target in rs else None
    rng = np.random.default_rng(0)
    gt_leg = [Patch(facecolor="#444444", ec="white", lw=2, label="GT change contour (white)"), Patch(fill=False, ec="red", lw=3, label=f"audit piece {target_id}")]
    for f in FACETS:
        rs = [r for r in rows if facet(r) == f]
        panel(t2, [full_masks[r["piece_id"]] for r in rs], f"{name}  source {f}: {len(rs)} pieces (random colours)",
              out / f"source_{f}.png", target=tgt(rs), rng=rng, legend=gt_leg[1:] if tgt(rs) else None)
    panel(t2, [full_masks[r["piece_id"]] for r in rows], f"{name}  all sources: {len(rows)} pieces", out / "all_sources.png",
          target=target, rng=rng, legend=gt_leg[1:])
    cands = defaultdict(lambda: np.zeros((512, 512), bool))
    for r in rows: cands[(facet(r), r["cand_id"])] |= full_masks[r["piece_id"]]
    panel(t2, list(cands.values()), f"{name}  before SLIC split: {len(cands)} candidates (pieces merged by source + cand_id)",
          out / "before_split.png", gt=gt, target=target, rng=rng, legend=gt_leg)
    panel(t2, [full_masks[r["piece_id"]] for r in rows], f"{name}  after SLIC split: {len(rows)} pieces", out / "after_split.png",
          gt=gt, target=target, rng=rng, legend=gt_leg)
    lab = {1: (0.1, 0.8, 0.1), 0: (0.9, 0.1, 0.1), None: (0.6, 0.6, 0.6)}
    order = sorted(rows, key=lambda r: {None: 0, 0: 1, 1: 2}[r["label"]])
    panel(t2, [full_masks[r["piece_id"]] for r in order], f"{name}  labels: green >=50% GT, red <10% GT, grey 10-50% (not trained)",
          out / "labels.png", gt=gt, colors=[lab[r["label"]] for r in order], target=target,
          legend=[Patch(color=lab[1], label="positive"), Patch(color=lab[0], label="negative"), Patch(color=lab[None], label="middle (10-50%)")] + gt_leg)
    table = defaultdict(list)
    for r in rows: table[(facet(r), r["split"], r["label"])].append(r["area"])
    lines = ["| 来源 | 切分 | 标签 | 切块数 | 面积 P10 | P50 | P90 |", "|---|---|---|---:|---:|---:|---:|"]
    names = {1: "正", 0: "负", None: "中间"}
    for f in FACETS:
        for s in (False, True):
            for l in (1, 0, None):
                a = table.get((f, s, l))
                if a:
                    q = np.percentile(a, [10, 50, 90])
                    lines.append(f"| {f} | {'是' if s else '否'} | {names[l]} | {len(a)} | {q[0]:.0f} | {q[1]:.0f} | {q[2]:.0f} |")
        a = [r["area"] for r in rows if facet(r) == f]
        q = np.percentile(a, [10, 50, 90])
        lines.append(f"| **{f} 小计** | | | **{len(a)}** | {q[0]:.0f} | {q[1]:.0f} | {q[2]:.0f} |")
    a = [r["area"] for r in rows]; q = np.percentile(a, [10, 50, 90])
    lines.append(f"| **全部** | | | **{len(a)}** | {q[0]:.0f} | {q[1]:.0f} | {q[2]:.0f} |")
    (out / "table.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]))
