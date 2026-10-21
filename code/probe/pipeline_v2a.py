"""Three-image recall scoring and two real V2 generation examples (v2, one GPU).

Uses the Renderer and score_one functions used by exp6v2_eval. Never trains.
All recall sources, including label=None, are scored; no prediction pieces.
"""
from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import numpy as np
import torch

from common import OUT
from exp6b_common import save_npz, source_of, write_json
from exp6v2_common import (INSTRUCTIONS, MARKER, PROMPT, input_batch, load_model,
                          read_adapter, score_one)
from exp6v2_data import Renderer
from pipeline_select import DEFAULT_OUT, read_json, selected_rows

V2_ROOT = OUT / "exp6/v2"


def metadata_for(root, variant):
    folder = root / variant
    meta = read_adapter(folder / "adapter", variant)
    thresholds = read_json(folder / "eval/sample/validation_thresholds.json")
    if thresholds["run_id"] != meta["run_id"]:
        raise ValueError(f"V2{variant}: adapter and validation threshold runs differ")
    return meta, thresholds


def choose_examples(rows, scores, threshold):
    """Prefer a positive/correct + negative/wrong pair, then the reverse."""
    buckets = {(label, correct): [] for label in (0, 1) for correct in (False, True)}
    for row in rows:
        if row["label"] in (0, 1):
            delta = scores[(row["image"], row["piece_id"])]["logit_diff"]
            correct = int(delta >= threshold) == row["label"]
            buckets[(row["label"], correct)].append(row)
    def rank(row):
        delta = scores[(row["image"], row["piece_id"])]["logit_diff"]
        return (-abs(delta - threshold), row["image"], row["piece_id"])
    for pos_correct, neg_correct in ((True, False), (False, True)):
        if buckets[(1, pos_correct)] and buckets[(0, neg_correct)]:
            return [min(buckets[(1, pos_correct)], key=rank), min(buckets[(0, neg_correct)], key=rank)]
    result = []
    for label in (1, 0):
        available = buckets[(label, True)] + buckets[(label, False)]
        if not available:
            raise ValueError("Three images do not contain both labeled classes")
        result.append(min(available, key=rank))
    return result


@torch.no_grad()
def generate_example(processor, model, images, variant, limit):
    batch = input_batch(processor, {}, variant, images)
    generated = model.generate(**batch, max_new_tokens=limit, do_sample=False, use_cache=True,
                               pad_token_id=processor.tokenizer.eos_token_id)
    tokens = generated[0, batch["input_ids"].shape[1]:]
    raw = processor.tokenizer.decode(tokens, skip_special_tokens=True)
    full = (MARKER if variant in ("a", "c") else "") + raw
    answer = full.rsplit(MARKER, 1)[-1].lstrip() if MARKER in full else ""
    label = 1 if answer.startswith("是") else 0 if answer.startswith("否") else None
    return dict(raw_generated_text=raw, response_with_prefix=full, generated_label=label,
                generated_tokens=int(tokens.numel()), reached_token_limit=tokens.numel() >= limit,
                prompt=PROMPT + INSTRUCTIONS[variant],
                input_order=["Image 1 (T1, earlier):", "t1", "Image 2 (T2, later):", "t2"],
                assistant_prefix=MARKER if variant in ("a", "c") else "",
                model_input_text=processor.tokenizer.decode(batch["input_ids"][0], skip_special_tokens=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_OUT / "selection.json")
    parser.add_argument("--v2-root", type=Path, default=V2_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT / "v2")
    args = parser.parse_args()
    selection = read_json(args.selection)
    by_image = selected_rows(selection)
    rows = [r for name in sorted(by_image) for r in by_image[name] if r["kind"] == "recall"]
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "complete.json").exists():
        raise FileExistsError("Completed V2 output exists; use a fresh --out")
    meta_a, threshold_a = metadata_for(args.v2_root, "a")
    threshold = threshold_a["recall_80"]["threshold"]
    renderer = Renderer()
    started = time.monotonic()
    print(f"{len(rows)} recall pieces; inference estimate {0.44 * len(rows) / 60:.1f} min + loading/rendering", flush=True)
    processor, model = load_model(meta_a["model"], args.v2_root / "a/adapter")
    journal = args.out / "a_scores.jsonl"
    results = []
    with journal.open("w") as handle:
        for index, row in enumerate(rows):
            result = score_one(processor, model, {}, "a", meta_a["config"]["max_new_tokens"],
                               renderer.render("second", row))
            record = dict(image=row["image"], piece_id=row["piece_id"], source=source_of(row),
                          label=row["label"], **result)
            results.append(record)
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            if (index + 1) % 100 == 0:
                handle.flush()
                print(f"V2a {index + 1}/{len(rows)}", flush=True)
    score_lookup = {(r["image"], r["piece_id"]): r for r in results}
    save_npz(args.out / "a_scores.npz", image=np.asarray([r["image"] for r in results]),
             piece_id=np.asarray([r["piece_id"] for r in results], np.int32),
             score=np.asarray([r["score"] for r in results], np.float64),
             logit_diff=np.asarray([r["logit_diff"] for r in results], np.float64),
             run_id=meta_a["run_id"])
    chosen = choose_examples(rows, score_lookup, threshold)
    examples = []
    for row in chosen:
        tag = f"{Path(row['image']).stem}_{row['piece_id']}"
        folder = args.out / "examples" / tag
        folder.mkdir(parents=True, exist_ok=True)
        images = renderer.render("second", row)
        for view, image in zip(("t1", "t2"), images):
            image.save(folder / f"{view}.png")
        examples.append(dict(tag=tag, image=row["image"], piece_id=row["piece_id"],
                             source=source_of(row), label=row["label"], gt_frac=row["gt_frac"],
                             area=row["area"], crop=row["crop"], variants={}))
    model_meta = {}
    for variant in ("a", "b", "c"):
        if variant != "a":
            del model, processor
            gc.collect()
            torch.cuda.empty_cache()
            meta, thresholds = metadata_for(args.v2_root, variant)
            processor, model = load_model(meta["model"], args.v2_root / variant / "adapter")
        else:
            meta, thresholds = meta_a, threshold_a
        limit = meta["config"]["max_new_tokens"]
        model_meta[variant] = dict(run_id=meta["run_id"], threshold=thresholds["recall_80"]["threshold"],
                                  threshold_source=f"exp6/v2/{variant}/eval/sample/validation_thresholds.json",
                                  train_samples=meta["train_samples"], validation_samples=meta["validation_samples"],
                                  rank=meta["config"]["rank"], alpha=meta["config"]["alpha"], max_new_tokens=limit)
        for row, example in zip(chosen, examples):
            images = renderer.render("second", row)
            score = (score_lookup[(row["image"], row["piece_id"])] if variant == "a" else
                     score_one(processor, model, {}, variant, limit, images))
            generation = generate_example(processor, model, images, variant, limit)
            generation.update(logit_diff=score["logit_diff"], score=score["score"],
                              scoring_raw=score["raw"], scoring_status=score["status"],
                              threshold=model_meta[variant]["threshold"],
                              threshold_label=int(score["logit_diff"] >= model_meta[variant]["threshold"]))
            generation["threshold_correct"] = generation["threshold_label"] == row["label"]
            generation["generation_correct"] = (None if generation["generated_label"] is None else
                                                generation["generated_label"] == row["label"])
            example["variants"][variant] = generation
            folder = args.out / "examples" / example["tag"]
            (folder / f"{variant}_raw.txt").write_text(generation["raw_generated_text"], encoding="utf-8")
            (folder / f"{variant}_prompt.txt").write_text(generation["prompt"], encoding="utf-8")
            (folder / f"{variant}_model_input.txt").write_text(generation["model_input_text"], encoding="utf-8")
            write_json(folder / f"{variant}.json", generation)
    rule = ("在三图全部有标签补漏切块中，优先选 V2a 验证 recall-80 阈值下的正例答对、负例答错；"
            "再尝试正例答错、负例答对；否则各取一正一负。各桶取离阈值最远者，"
            "同分按图名、piece_id 升序。此处答对指阈值分类，生成答案另行报告；仅作展示。")
    write_json(args.out / "examples.json", dict(rule=rule, examples=examples))
    write_json(args.out / "complete.json", dict(selection=selection, models=model_meta,
               images=sorted(by_image), pieces=len(rows), unlabeled=sum(r["label"] is None for r in rows),
               seconds=time.monotonic() - started,
               scope="all recall B/G0.05/C1/C2/H1 including unlabeled; no pred", score_scale="logit_diff"))


if __name__ == "__main__":
    main()
