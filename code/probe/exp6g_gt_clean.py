"""Clean SECOND ground-truth figure (t1, t2, binary change, both semantic maps, zoom); exp5-env, CPU, seconds."""
import json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from matplotlib.patches import Patch, Rectangle
from PIL import Image

SECOND = Path("/guisongxia01/pangchao/wangyihan/datasets/lim1/SECOND")
CLASSES = json.load(open(SECOND / "cls_gray.json"))
RGB = json.load(open(SECOND / "cls_rgb.json"))
PALETTE = np.zeros((7, 3))
for k, v in CLASSES.items():
    PALETTE[v] = np.array(RGB[k]) / 255

def overlay(img, sem, alpha=0.55):
    out = img.astype(float) / 255
    m = sem > 0
    out[m] = (1 - alpha) * out[m] + alpha * PALETTE[sem[m]]
    return out

def zoom(a, b, box, height):
    x0, x1, y0, y1 = box
    gap = np.ones((y1 - y0, 4, 3))
    strip = np.concatenate([a[y0:y1, x0:x1], gap, b[y0:y1, x0:x1]], axis=1)
    k = height / strip.shape[0]
    rows = np.minimum((np.arange(height) / k).astype(int), strip.shape[0] - 1)
    cols = np.minimum((np.arange(int(round(strip.shape[1] * k))) / k).astype(int), strip.shape[1] - 1)
    return strip[rows][:, cols]

def window(ax, box):
    x0, x1, y0, y1 = box
    ax.add_patch(Rectangle((x0 - .5, y0 - .5), x1 - x0, y1 - y0, fill=False, ec="white", lw=1.0, ls="--"))

def main(name, box, out):
    t1 = np.asarray(Image.open(SECOND / "img1" / name).convert("RGB"))
    t2 = np.asarray(Image.open(SECOND / "img2" / name).convert("RGB"))
    gt = np.asarray(Image.open(SECOND / "label_unch0chi255" / name))
    s1 = np.asarray(Image.open(SECOND / "slabel1" / name)); s2 = np.asarray(Image.open(SECOND / "slabel2" / name))
    o1, o2 = overlay(t1, s1), overlay(t2, s2)
    z = zoom(o1, o2, box, 512)
    panels = [("t1", t1, "t1 (before)"), ("t2", t2, "t2 (after)"), ("change", gt, "binary change (label_unch0chi255)"),
              ("slabel1_on_t1", o1, "slabel1 on t1"), ("slabel2_on_t2", o2, "slabel2 on t2"),
              ("zoom", z, f"zoom x {box[0]}-{box[1]}, y {box[2]}-{box[3]}: slabel1 on t1 | slabel2 on t2")]
    ratio = z.shape[1] / 512
    fig = plt.figure(figsize=(5 * (2 + ratio), 11.2))
    gs = GridSpec(2, 1, figure=fig, left=0.01, right=0.99, top=0.93, bottom=0.07, hspace=0.1)
    top = GridSpecFromSubplotSpec(1, 3, gs[0], wspace=0.03)
    bottom = GridSpecFromSubplotSpec(1, 3, gs[1], width_ratios=[1, 1, ratio], wspace=0.03)
    axes = [fig.add_subplot(top[i]) for i in range(3)] + [fig.add_subplot(bottom[i]) for i in range(3)]
    for i, (ax, (_, im, title)) in enumerate(zip(axes, panels)):
        ax.imshow(im, cmap="gray" if im.ndim == 2 else None, interpolation="nearest")
        if i < 3: window(ax, box)
        ax.set_title(title, fontsize=11); ax.set_xticks([]); ax.set_yticks([])
    fig.legend(handles=[Patch(color=PALETTE[v], label=k) for k, v in CLASSES.items() if v > 0],
               loc="lower center", ncol=6, fontsize=11, frameon=False)
    fig.suptitle(f"{Path(name).stem}  SECOND ground truth", fontsize=14)
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{Path(name).stem}.png", dpi=150); plt.close(fig)
    sub = out / f"{Path(name).stem}_panels"; sub.mkdir(exist_ok=True)
    for key, im, _ in panels:
        arr = im if im.dtype == np.uint8 else (np.clip(im, 0, 1) * 255).astype(np.uint8)
        Image.fromarray(arr).save(sub / f"{key}.png")
    x0, x1, y0, y1 = box
    def stats(s):
        w = s[y0:y1, x0:x1]; inv = {v: k for k, v in CLASSES.items()}
        return {inv[c]: int((w == c).sum()) for c in np.unique(w)}
    print(name, "window slabel1", stats(s1), "slabel2", stats(s2), "changed px", int((gt[y0:y1, x0:x1] > 0).sum()))

if __name__ == "__main__":
    out = Path(sys.argv[1])
    for spec in sys.argv[2:]:
        name, *b = spec.split(":"); main(name, tuple(map(int, b)), out)
