"""Experiment 6g offline audit page: CPU, conda_envs/exp5-env.

Estimated <1 GB RAM, 3–15 minutes for 1,280 JPEGs on one CPU (not measured).
Uses the V2 renderer and saved crop boxes; never loads ground-truth images.
"""
from __future__ import annotations

import argparse
import io
import json
import random
from collections import Counter
from pathlib import Path

from exp6b_common import ROOT, SEED, write_json
from exp6v2_data import Renderer, SIDE


HTML = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>两期影像审核</title>
<style>
body{font:16px system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;
background:#f4f5f6;color:#20252a}button{padding:12px 18px;margin:5px;font:inherit;
cursor:pointer}button[aria-pressed=true]{background:#c5e5ff;border:2px solid #12589a}
.pair{display:flex;gap:12px}.pair figure{margin:12px 0;flex:1;min-width:0}
img{display:block;width:100%;max-width:384px;height:auto;background:#ddd}
progress{width:100%}#warning{color:#9b3000}#status{min-height:1.5em}
@media(max-width:500px){button{padding:10px}.pair{gap:6px}}
</style>
<h1>两期影像审核</h1>
<p>请判断红色轮廓内的地物是否发生真实变化。仅成像或色调差异请选择“没变”；
证据不足请选择“看不清”。可切换上下文查看周围地物。</p>
<div id="progressText" aria-live="polite"></div><progress id="progress"></progress>
<div class="pair"><figure><figcaption>t1</figcaption><img id="left" alt="t1 影像"></figure>
<figure><figcaption>t2</figcaption><img id="right" alt="t2 影像"></figure></div>
<div><button id="context" aria-pressed="false">显示上下文</button></div>
<div id="choices">
<button data-choice="changed">1 · 变了</button>
<button data-choice="unchanged">2 · 没变</button>
<button data-choice="unclear">3 · 看不清</button>
<button data-choice="skip">0 · 跳过</button></div>
<p id="status" aria-live="polite"></p>
<div><button id="previous">上一个</button><button id="next">下一个</button>
<button id="export">导出结果</button><button id="import">导入结果继续</button>
<input id="file" type="file" accept=".json,application/json" hidden></div>
<p id="warning" role="alert"></p>
<p>快捷键：1 / 2 / 3 / 0；左右方向键切换。建议结束时导出结果留存。</p>
<script>
"use strict";
const ids = __IDS__;
const options = {changed:"变了",unchanged:"没变",unclear:"看不清",skip:"跳过"};
const storageKey = "exp6g-audit-" + ids[0];
const idSet = new Set(ids);
const el = id => document.getElementById(id);
let answers = {}, index = 0, context = false;
function validAnswers(value) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("结果应为 JSON 对象");
  const result = {};
  for (const [id, item] of Object.entries(value)) {
    if (!idSet.has(id)) throw new Error("结果包含另一批审核的编号");
    if (!item || !Object.hasOwn(options,item.choice) ||
        typeof item.timestamp !== "string" || !Number.isFinite(Date.parse(item.timestamp)))
      throw new Error("结果包含无效选项或时间戳");
    result[id] = {choice:item.choice,timestamp:item.timestamp};
  }
  return result;
}
try {
  const saved = localStorage.getItem(storageKey);
  if (saved) answers = validAnswers(JSON.parse(saved));
} catch (error) {
  el("warning").textContent = "浏览器无法恢复本地记录："+error.message+
    "。请导入上次导出的结果，并定期导出。";
}
function save() {
  try {localStorage.setItem(storageKey,JSON.stringify(answers));}
  catch (error) {
    el("warning").textContent = "本地自动保存不可用："+error.message+
      "。当前记录保存在本页内存中，关闭前务必导出；下次可导入继续。";
  }
}
function firstMissing() {
  const found = ids.findIndex(id => !Object.hasOwn(answers,id));
  index = found < 0 ? ids.length - 1 : found;
}
function render() {
  const id = ids[index], item = answers[id];
  const prefix = "crops/" + id + (context ? "_context_" : "_");
  el("left").src = prefix + "t1.jpg";
  el("right").src = prefix + "t2.jpg";
  el("context").textContent = context ? "显示近景" : "显示上下文";
  el("context").setAttribute("aria-pressed",String(context));
  const done = ids.filter(id => Object.hasOwn(answers,id)).length;
  el("progress").max = ids.length; el("progress").value = done;
  el("progressText").textContent =
    `第 ${index+1} / ${ids.length} 个；已处理 ${done} 个（含跳过）`;
  el("status").textContent = item ? "当前选择："+options[item.choice] : "当前尚未作答";
  if (done === ids.length) el("status").textContent += "。已全部处理，请导出结果。";
  document.querySelectorAll("[data-choice]").forEach(button =>
    button.setAttribute("aria-pressed",String(item?.choice === button.dataset.choice)));
  el("previous").disabled = index === 0;
  el("next").disabled = index === ids.length-1;
}
function choose(choice) {
  answers[ids[index]] = {choice,timestamp:new Date().toISOString()};
  save();
  if (index < ids.length-1) index++;
  render();
}
function move(delta) { index = Math.max(0,Math.min(ids.length-1,index+delta)); render(); }
document.querySelectorAll("[data-choice]").forEach(button =>
  button.onclick = () => choose(button.dataset.choice));
el("previous").onclick = () => move(-1); el("next").onclick = () => move(1);
el("context").onclick = () => {context = !context; render();};
document.addEventListener("keydown",event => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey ||
      ["INPUT","TEXTAREA"].includes(event.target.tagName)) return;
  const mapping = {"1":"changed","2":"unchanged","3":"unclear","0":"skip"};
  if (Object.hasOwn(mapping,event.key)) {event.preventDefault(); choose(mapping[event.key]);}
  if (event.key === "ArrowLeft") {event.preventDefault(); move(-1);}
  if (event.key === "ArrowRight") {event.preventDefault(); move(1);}
});
el("export").onclick = () => {
  const blob = new Blob([JSON.stringify(answers,null,2)+"\n"],{type:"application/json"});
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = "audit_answers.json"; link.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
};
el("import").onclick = () => el("file").click();
el("file").onchange = async () => {
  const file = el("file").files[0]; if (!file) return;
  try {
    answers = {...answers,...validAnswers(JSON.parse(await file.text()))};
    save(); firstMissing(); render();
  } catch (error) {el("warning").textContent = "导入失败："+error.message;}
  el("file").value = "";
};
for (const image of [el("left"),el("right")]) image.onerror = () => {
  el("warning").textContent = "图片读取失败，请确认 HTML 与完整的 crops 文件夹放在同一目录。";
};
firstMissing(); render();
</script></html>
"""


def context_box(bbox, crop, shape):
    """Extend each bbox side by three bbox lengths, square, shift inside image."""
    x0, y0, x1, y1 = bbox
    height, width = shape
    side = min(max(7 * max(x1 - x0, y1 - y0), crop[2] - crop[0],
                   crop[3] - crop[1]), height, width)
    left = max(0, min(int((x0 + x1 - side) / 2), width - side))
    top = max(0, min(int((y0 + y1 - side) / 2), height - side))
    return [left, top, left + side, top + side]


def jpeg_bytes(image, budget, initial, minimum):
    for quality in range(initial, minimum - 1, -1):
        handle = io.BytesIO()
        image.save(handle, format="JPEG", quality=quality, optimize=True, subsampling=0)
        data = handle.getvalue()
        if len(data) <= budget:
            return data, quality
    raise ValueError(f"JPEG exceeds {budget} bytes at quality {minimum}; "
                     "review --min-quality or reduce the audit size")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "label_noise/audit/samples.json")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--quality", type=int, default=85)
    parser.add_argument("--min-quality", type=int, default=35)
    args = parser.parse_args()
    if not 1 <= args.min_quality <= args.quality <= 95:
        parser.error("Require 1 <= min-quality <= quality <= 95")
    manifest = json.loads(args.samples.read_text())
    samples = manifest["samples"]
    ids = [row["id"] for row in samples]
    if not ids or len(set(ids)) != len(ids) or any(
        len(value) != 32 or any(c not in "0123456789abcdef" for c in value) for value in ids
    ):
        raise ValueError("Expected unique opaque IDs from exp6g_sample.py")
    random.Random(args.seed).shuffle(ids)
    html = HTML.replace("__IDS__", json.dumps(ids)).encode("utf-8")
    out = args.samples.parent
    page, crops = out / "label_audit.html", out / "crops"
    if page.exists() or crops.exists():
        raise FileExistsError("Page/crops already exist; use a fresh audit directory")
    # Decimal MB; reserve metadata/report space as well as the HTML and manifest.
    limit = 40_000_000
    budget = (limit - len(html) - args.samples.stat().st_size - 64_000) // (4 * len(samples))
    if budget <= 0:
        raise ValueError("Audit metadata alone exceeds the size budget")
    crops.mkdir()
    renderer = Renderer()
    sizes, qualities = [], Counter()
    for number, row in enumerate(sorted(samples, key=lambda r: (r["dataset"], r["image"], r["id"])), 1):
        near = renderer.render(row["dataset"], row)
        context = context_box(row["bbox"], row["crop"], renderer.rgb[0].shape[:2])
        wide = renderer.render(row["dataset"], {**row, "crop": context})
        for suffix, image in zip(("t1", "t2", "context_t1", "context_t2"), near + wide):
            data, quality = jpeg_bytes(image, budget, args.quality, args.min_quality)
            (crops / f"{row['id']}_{suffix}.jpg").write_bytes(data)
            sizes.append(len(data))
            qualities[quality] += 1
        if number % 40 == 0:
            print("rendered", number, "/", len(samples), flush=True)
    page.write_bytes(html)
    total = sum(sizes) + len(html) + args.samples.stat().st_size
    report_path = out / "page_report.json"
    write_json(report_path, dict(
        side=SIDE, images=len(sizes), jpeg_bytes=sum(sizes),
        mean_jpeg_bytes=sum(sizes) / len(sizes), max_jpeg_bytes=max(sizes),
        per_image_budget_bytes=budget, quality_counts=dict(sorted(qualities.items())),
        bytes_before_report=total, budget_bytes=limit, page_seed=args.seed,
        context="bbox extended by 3 widths/heights on each side; square long side 7x; "
                "at least V2 crop size; clipped/shifted within the image",
        images_mode="relative JPEG files: avoids base64 expansion and supports offline file://",
    ))
    actual = total + report_path.stat().st_size
    if actual > limit:
        raise ValueError(f"Generated audit is {actual} bytes, exceeding {limit}")
    print(page, f"bundle={actual / 1_000_000:.3f} MB; limit=40 MB", flush=True)


if __name__ == "__main__":
    main()
