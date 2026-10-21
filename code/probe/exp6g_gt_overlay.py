"""2x3 full-image GT overlays for selected 6g audit pieces (exp5-env, CPU, seconds)."""
import json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
from PIL import Image
from skimage import measure
from exp6_pool_io import load_piece_masks

SECOND = Path("/guisongxia01/pangchao/wangyihan/datasets/lim1/SECOND")
CLASSES = json.load(open(SECOND / "cls_gray.json"))
RGB = json.load(open(SECOND / "cls_rgb.json"))
PALETTE = np.zeros((7, 3)); [PALETTE.__setitem__(v, np.array(RGB[k]) / 255) for k, v in CLASSES.items()]

def overlay(img, sem, alpha=0.55):
    out = img.astype(float) / 255
    m = sem > 0
    out[m] = (1 - alpha) * out[m] + alpha * PALETTE[sem[m]]
    return out

def outline(ax, mask, color="red"):
    for c in measure.find_contours(mask.astype(float), 0.5):
        ax.plot(c[:, 1], c[:, 0], color=color, lw=1.2)

def draw(sample, tag, out):
    name = sample["image"]
    t1 = np.asarray(Image.open(SECOND / "img1" / name).convert("RGB"))
    t2 = np.asarray(Image.open(SECOND / "img2" / name).convert("RGB"))
    gt = np.asarray(Image.open(SECOND / "label_unch0chi255" / name))
    s1 = np.asarray(Image.open(SECOND / "slabel1" / name)); s2 = np.asarray(Image.open(SECOND / "slabel2" / name))
    (x0, y0, x1, y1), local = load_piece_masks("second", name)[sample["piece_id"]]
    piece = np.zeros(gt.shape[:2], bool); piece[y0:y1, x0:x1] = local
    cx0, cy0, cx1, cy1 = sample["crop"]
    cand = t2.astype(float) / 255; cand[piece] = 0.45 * cand[piece] + 0.55 * np.array([1, 0, 0])
    panels = [(t1, "t1"), (t2, "t2"), (gt, "binary change GT (label_unch0chi255)"),
              (overlay(t1, s1), "slabel1 on t1"), (overlay(t2, s2), "slabel2 on t2"), (cand, "candidate piece on t2")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 11.8))
    for ax, (im, title) in zip(axes.flat, panels):
        ax.imshow(im, cmap="gray" if im.ndim == 2 else None, vmin=0, vmax=255 if im.ndim == 2 else None)
        outline(ax, piece)
        ax.add_patch(Rectangle((cx0 - .5, cy0 - .5), cx1 - cx0, cy1 - cy0, fill=False, ec="yellow", lw=1.2, ls="--"))
        ax.set_title(title, fontsize=11); ax.set_xticks([]); ax.set_yticks([])
    handles = [Patch(color=PALETTE[v], label=k) for k, v in CLASSES.items() if v > 0]
    handles += [Patch(fill=False, ec="red", label="candidate piece"), Patch(fill=False, ec="yellow", ls="--", label="crop (near view)")]
    fig.legend(handles=handles, loc="lower center", ncol=8, fontsize=10, frameon=False)
    fig.suptitle(f"{tag}  {name}  piece {sample['piece_id']}  |  gt_frac={sample['gt_frac']:.3f}  area={sample['area']} px  "
                 f"source={sample['source']}  V1 score={sample['none_score']:.4f}  ({sample['stratum']})", fontsize=12)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.06, wspace=0.03, hspace=0.12)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{tag}_{Path(name).stem}_p{sample['piece_id']}.png"
    fig.savefig(path, dpi=110); plt.close(fig); print(path)

if __name__ == "__main__":
    repo, out = Path(sys.argv[1]), Path(sys.argv[2])
    samples = json.load(open(repo / "audit/samples.json"))["samples"]
    h = (repo / "audit/label_audit.html").read_text()
    i = h.find("const ids = ["); ids = json.loads(h[i + 12:h.find("]", i) + 1])
    by = {s["id"]: s for s in samples}
    for n in (206, 215, 123):
        draw(by[ids[n - 1]], f"web{n:03d}", out)
    for name in ("test_5.png", "test_900.png", "test_759.png"):
        for s in samples:
            if s["image"] == name: draw(s, "named", out)
