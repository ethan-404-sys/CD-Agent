"""GPU raw region masks; CPU --plan counts regions before requesting GPU time.

No GT is used by prompts, selection of multiple masks, or date-score selection.
Only O confirmation reads saved oracle labels. CPU composition is in exp7_eval.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from nd_compat import CONNECTIVITY, ndimage

from common import load_prob, load_rgb
from exp6_pool_io import DETECTOR
from exp7_common import (ROOT, PROMPTS, Confirmations, cache_config, cache_dir,
                         exclusive_json, expand, read_json, regions, save_raw, write_json)

SAM_CKPT = '/guisongxia01/pangchao/wangyihan/models/SAM/sam_vit_h_4b8939.pth'


def deepest(mask):
    distance = ndimage.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
    y, x = np.unravel_index(int(distance.argmax()), mask.shape)
    return [int(x), int(y)]


def spread_points(mask, n, values, descending):
    remaining = mask.copy()
    points = []
    for _ in range(min(n, int(mask.sum()))):
        if not remaining.any():
            remaining = mask.copy()
            for x, y in points:
                remaining[y, x] = False
        ranked = np.where(remaining, values, -np.inf if descending else np.inf)
        y, x = np.unravel_index(int(ranked.argmax() if descending else ranked.argmin()), mask.shape)
        points.append([int(x), int(y)])
        seed = np.zeros_like(mask)
        seed[y, x] = True
        remaining &= ~expand(seed, 5)
    return points


def largest_disagreement(mask):
    labels, n = ndimage.label(mask, CONNECTIVITY)
    if n == 0:
        return None
    area = np.bincount(labels.ravel())
    area[0] = 0
    return deepest(labels == int(area.argmax()))


class Segmenter:
    def __init__(self, model):
        import torch
        self.torch, self.name = torch, model
        if model == 'sam3':
            from sam3_tools import build_processor
            self.processor = build_processor(inst_interactivity=True)
            self.model = self.processor.model
            self.low_shape = self.model.inst_interactive_predictor.model.sam_prompt_encoder.mask_input_size
        else:
            from torchange.models.segment_any_change.segment_anything import SamPredictor, sam_model_registry
            self.model = sam_model_registry['vit_h'](checkpoint=SAM_CKPT).to('cuda').eval()
            self.predictor = SamPredictor(self.model)
            self.low_shape = self.model.prompt_encoder.mask_input_size

    def autocast(self):
        from contextlib import nullcontext
        if self.name == 'sam3':
            from sam3_tools import autocast
            return autocast()
        return nullcontext()

    def encode(self, image):
        self.torch.cuda.synchronize()
        start = time.perf_counter()
        with self.torch.inference_mode(), self.autocast():
            if self.name == 'sam3':
                self.state = self.processor.set_image(Image.fromarray(image))
            else:
                self.predictor.set_image(image)
        self.torch.cuda.synchronize()
        return time.perf_counter() - start

    def predict(self, **kwargs):
        if self.name == 'sam3':
            # predict_inst rebuilds decoder views from state, without forward_image.
            # Its cleanup clears predictor._features, not state's backbone tensors.
            masks, scores, logits = self.model.predict_inst(self.state, **kwargs)
        else:
            masks, scores, logits = self.predictor.predict(**kwargs)
        masks = np.asarray(masks)
        if masks.ndim == 4 and masks.shape[0] == 1:
            masks = masks[0]
        return masks > 0, np.asarray(scores).reshape(-1), np.asarray(logits)

    def run(self, local, bbox, crop, prob, prompt):
        """Decode one prompt on the last encoded crop; P7 shares it for both rounds."""
        self.torch.cuda.synchronize()
        start = time.perf_counter()
        with self.torch.inference_mode(), self.autocast():
            x0, y0, _, _ = crop
            box = np.asarray(bbox, np.float32) - np.array([x0, y0, x0, y0], np.float32)
            point = deepest(local)
            kwargs = dict(box=box, multimask_output=False)
            if prompt != 'P1':
                kwargs.update(point_coords=np.asarray([point], np.float32), point_labels=np.array([1]))
            if prompt in ('P3', 'P5'):
                distance = ndimage.distance_transform_edt(np.pad(local, 1))[1:-1, 1:-1]
                pos = spread_points(local, 3, distance, True)
                neg = spread_points(expand(local, 10) & ~local, 2, prob, False)
                kwargs.update(point_coords=np.asarray(pos + neg, np.float32),
                              point_labels=np.asarray([1]*len(pos) + [0]*len(neg)))
                if prompt == 'P5':
                    del kwargs['box']
            if prompt == 'P4':
                # Crop is square, so both backends have no aspect-ratio padding here.
                h, w = self.low_shape
                resized = np.asarray(Image.fromarray(local.astype(np.float32) * 20 - 10).resize(
                    (w, h), Image.Resampling.BILINEAR))
                kwargs['mask_input'] = resized[None]
            if prompt == 'P6':
                kwargs['multimask_output'] = True
            masks, scores, logits = self.predict(**kwargs)
            selected = 0
            if prompt == 'P6':
                inter = (masks & local).sum(axis=(1, 2))
                union = (masks | local).sum(axis=(1, 2))
                selected = int((inter / np.maximum(union, 1)).argmax())
            if prompt == 'P7':
                pos = largest_disagreement(local & ~masks[0])
                neg = largest_disagreement(masks[0] & ~local)
                coords, labels = [point], [1]
                for p, label in ((pos, 1), (neg, 0)):
                    if p is not None:
                        coords.append(p)
                        labels.append(label)
                kwargs.update(point_coords=np.asarray(coords, np.float32), point_labels=np.asarray(labels),
                              mask_input=logits[0:1])
                masks, scores, logits = self.predict(**kwargs)
            mask, score = masks[selected], float(scores[selected])
        self.torch.cuda.synchronize()
        return mask, score, time.perf_counter() - start


def plan(context, args):
    count = sum(sum(1 for _ in regions(context.accepted(name)[1])) for name in context.images)
    # Provisional split, to be replaced with synchronized smoke measurements.
    default_encode, default_decode = (0.05, 0.01) if args.model == 'sam3' else (0.40, 0.01)
    encode = default_encode if args.encode_seconds is None else args.encode_seconds
    decode = default_decode if args.decode_seconds is None else args.decode_seconds
    p7 = 2 * decode if args.p7_decode_seconds is None else args.p7_decode_seconds
    per_prompt = {p: p7 if p == 'P7' else decode for p in args.prompts}
    encode_total = count * len(args.views) * encode
    decode_total = count * len(args.views) * sum(per_prompt.values())
    hours = (encode_total + decode_total) / 3600
    result = dict(dataset=context.dataset, setting=context.setting, combo=context.combo,
                  images=len(context.images), regions=count, model=args.model,
                  prompts=args.prompts, views=args.views,
                  assumed_encode_seconds_per_region=encode,
                  assumed_decode_seconds_per_prompt=per_prompt,
                  estimated_encode_seconds=encode_total, estimated_decode_seconds=decode_total,
                  estimated_gpu_hours=hours, stop_and_report=hours > 12,
                  timing_formula='regions * views * (encode + sum(prompt decode)); P7 decode includes both rounds',
                  per_image_timing='每图秒数 = 编码 + 该提示解码；推理秒/图另加CPU读取和合并；best计两期',
                  timing_source='Provisional defaults: SAM3 .05/.01; ViT-H .40/.01 encode/decode seconds. '
                                'CLI overrides are recorded above; not a measured split. Full split, cold cache; excludes model loading and I/O.')
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = '_'.join(args.prompts + args.views)
    write_json(args.out / f'plan_{context.dataset}_{context.setting}_{context.combo}_{args.model}_{suffix}.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=('second_cv', 'second', 'sysu', 'levir', 'whu'), required=True)
    parser.add_argument('--setting', choices=('O', 'V'), default='O')
    parser.add_argument('--combo', choices=('BG', 'BGH1'), default='BGH1')
    parser.add_argument('--model', choices=('sam3', 'sam_vith'), default='sam3')
    parser.add_argument('--prompts', nargs='+', choices=PROMPTS, default=['P1', 'P2'])
    parser.add_argument('--views', nargs='+', choices=('t1', 't2'), default=['t2'])
    parser.add_argument('--out', type=Path, default=ROOT)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--encode-seconds', type=float, help='Plan: seconds per region/view image encoding')
    parser.add_argument('--decode-seconds', type=float, help='Plan: seconds per P1-P6 prompt, including prompt preparation')
    parser.add_argument('--p7-decode-seconds', type=float, help='Plan: total P7 two-round seconds; default 2 * decode-seconds')
    parser.add_argument('--limit', type=int, default=0, help='Smoke only: use a separate --out')
    args = parser.parse_args()
    if len(set(args.prompts)) != len(args.prompts) or len(set(args.views)) != len(args.views):
        parser.error('Duplicate prompts/views')
    if any(v is not None and (not np.isfinite(v) or v < 0)
           for v in (args.encode_seconds, args.decode_seconds, args.p7_decode_seconds)):
        parser.error('Plan times must be finite and nonnegative')
    context = Confirmations(args.dataset, args.setting, args.combo)
    if args.plan:
        plan(context, args)
        return
    images = context.images[:args.limit or None]
    configs = {}
    for prompt in args.prompts:
        for view in args.views:
            directory = cache_dir(args.out, args.dataset, args.setting, args.combo, args.model, prompt, view)
            directory.mkdir(parents=True, exist_ok=True)
            config = dict(**cache_config(context, args.model, prompt, view), images=images)
            if (directory / 'config.json').exists():
                if read_json(directory / 'config.json') != config:
                    raise ValueError(f'{directory}: incompatible cache configuration')
            else:
                exclusive_json(directory / 'config.json', config)
            configs[prompt, view] = directory, config
    model = Segmenter(args.model)
    start = time.monotonic()
    encode_count, encode_seconds = 0, 0.0
    decode_seconds = {p: 0.0 for p in args.prompts}
    for number, name in enumerate(images, 1):
        pending = {}
        for (prompt, view), (directory, config) in configs.items():
            path = directory / (Path(name).stem + '.npz')
            if path.exists():
                # A file becomes visible only after a complete compressed write.
                with np.load(path, allow_pickle=False) as z:
                    if str(z['config']) != json.dumps(config, sort_keys=True):
                        raise ValueError(f'{path}: incompatible cached image')
                continue
            pending[prompt, view] = path, config
        if not pending:
            continue
        _, union = context.accepted(name)
        pieces = list(regions(union))
        prob = load_prob(args.dataset, DETECTOR[args.dataset], name)
        for view in args.views:
            prompts = [p for p in args.prompts if (p, view) in pending]
            if not prompts:
                continue
            rgb = load_rgb(args.dataset, name, view)
            outputs = {}
            for prompt in prompts:
                _, config = pending[prompt, view]
                outputs[prompt] = dict(crops=np.asarray([r[2] for r in pieces], np.int32).reshape(-1, 4),
                                       config=np.asarray(json.dumps(config, sort_keys=True)),
                                       scores=np.empty(len(pieces)), seconds=np.empty(len(pieces)),
                                       encode_seconds=np.empty(len(pieces)))
            for i, (_, bbox, crop, local) in enumerate(pieces):
                x0, y0, x1, y1 = crop
                encoded = model.encode(rgb[y0:y1, x0:x1])
                encode_count += 1
                encode_seconds += encoded
                for prompt in prompts:
                    mask, score, elapsed = model.run(local, bbox, crop, prob[y0:y1, x0:x1], prompt)
                    if mask.shape != local.shape or not np.isfinite(score):
                        raise ValueError(f'{name}: invalid SAM output shape/score')
                    arrays = outputs[prompt]
                    arrays[f'region_{i}'], arrays[f'mask_{i}'] = local, mask
                    arrays['scores'][i], arrays['seconds'][i] = score, elapsed
                    arrays['encode_seconds'][i] = encoded
                    decode_seconds[prompt] += elapsed
            for prompt, arrays in outputs.items():
                save_raw(pending[prompt, view][0], arrays)
        if number % 20 == 0:
            print(f'{number}/{len(images)} {time.monotonic()-start:.1f}s', flush=True)
    for directory, config in configs.values():
        write_json(directory / 'complete.json', dict(config=config, images=images))
    print(json.dumps(dict(encode_count=encode_count, encode_seconds=encode_seconds,
                          decode_seconds=decode_seconds,
                          inference_seconds=encode_seconds + sum(decode_seconds.values()),
                          wall_seconds=time.monotonic()-start), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
