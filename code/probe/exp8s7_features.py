"""DINOv3 SAT full-image or SYSU local-crop features with image-level resume."""
from exp8s7_common import *
import time


def feature_dir(a, split):
    return a.out / a.dataset / ('features_crop' if a.crop == 'local' else 'features') / split


def config(a):
    processor = c.read_json(Path(a.model) / 'preprocessor_config.json')
    cfg = dict(model=str(Path(a.model).resolve()), layers=[12, 24], input_size=1024 if a.dataset == 'second' else 768,
                mean=processor['image_mean'], std=processor['image_std'], dtype='bfloat16', storage='float16',
                layout=['T1', 'T2'], regions=['inside-L12', 'inside-L24', 'ring-L24'],
                normalization='model.norm on both selected block outputs',
                ring='distance to novel mask <= 2*sqrt(novel_area/pi), excluding mask; clipped to image',
                pooling='area downsampling, weighted average; empty ring is zero', limit=a.limit)
    if a.crop == 'local':
        cfg.update(crop='local', input_size=512,
                   crop_rule='bbox center; L=min(256,max(64,2*b)); floor origin then shift into image',
                   ring='original-image ring intersected with crop; normalized after area resampling')
    return cfg


def crop_box(bbox, shape):
    x0, y0, x1, y1 = map(int, bbox)
    height, width = shape
    if (height, width) != (256, 256):
        raise ValueError('SYSU local crops require original 256x256 images')
    length = min(256, max(64, 2*max(x1-x0, y1-y0)))
    left = min(max((x0+x1-length)//2, 0), width-length)
    top = min(max((y0+y1-length)//2, 0), height-length)
    return left, top, left+length, top+length


def crop_groups(a, z, rows, shape):
    groups = {}
    for position, j in enumerate(rows):
        box = crop_box(z['bbox'][j], shape) if a.crop == 'local' else (0, 0, shape[1], shape[0])
        groups.setdefault(box, []).append(position)
    return groups


def part_path(a, name):
    return feature_dir(a, a.split) / 'parts' / (Path(name).stem + '.npz')


def check_part(q, z, rows, name, cfg):
    if str(q['image']) != name or json.loads(str(q['config'])) != cfg or not np.array_equal(q['id'], z['id'][rows]):
        raise ValueError(f'{name}: feature cache configuration/identity mismatch')
    if q['vectors'].shape != (len(rows), 2, 3, 1024) or not np.isfinite(q['vectors']).all():
        raise ValueError(f'{name}: invalid feature vectors')


def weights(z, rows, shape, grid, device, box=None):
    import torch
    import torch.nn.functional as F
    inside, rings = [], []
    for j in rows:
        full = np.zeros(shape, bool)
        s5.add_mask(full, unpack(z, j))
        radius = np.sqrt(float(full.sum()) / np.pi)
        ring = (ndi.distance_transform_edt(~full) <= 2 * radius) & ~full
        if box is not None:
            x0, y0, x1, y1 = box
            full, ring = full[y0:y1, x0:x1], ring[y0:y1, x0:x1]
        pair = torch.from_numpy(np.stack([full, ring]).astype(np.float32)).to(device)
        soft = F.interpolate(pair[:, None], size=(grid, grid), mode='area')[:, 0].flatten(1)
        soft /= soft.sum(1, keepdim=True).clamp_min(1e-12)
        inside.append(soft[0]); rings.append(soft[1])
    if not len(rows):
        empty = torch.empty((0, grid * grid), device=device)
        return empty, empty
    return torch.stack(inside), torch.stack(rings)


def extract(a):
    import torch
    from transformers import AutoModel
    from PIL import Image
    if not torch.cuda.is_available():
        raise RuntimeError('Feature extraction requires a CUDA Slurm job')
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError('Feature extraction requires bf16 support')
    z, cfg = load_units(a, a.split), config(a)
    todo = []
    for i in range(a.shard, len(z['images']), a.shards):
        name = str(z['images'][i]); path = part_path(a, name)
        if path.exists():
            check_part(c.archive(path), z, image_rows(z, i), name, cfg)
        else:
            todo.append(i)
    if not todo:
        print('All assigned images already complete', flush=True)
        return
    model = AutoModel.from_pretrained(a.model, local_files_only=True, torch_dtype=torch.bfloat16,
                                      attn_implementation='sdpa').eval().to('cuda')
    mc = model.config
    if (mc.patch_size, mc.hidden_size, mc.num_hidden_layers) != (16, 1024, 24):
        raise ValueError('Expected DINOv3 ViT-L/16 with 24 blocks')
    captured = {}
    def capture(module, inputs, output):
        captured['L12'] = output
    hook = model.layer[11].register_forward_hook(capture)
    size, grid = cfg['input_size'], cfg['input_size'] // 16
    mean = torch.tensor(cfg['mean'], device='cuda')[None, :, None, None]
    std = torch.tensor(cfg['std'], device='cuda')[None, :, None, None]
    elapsed, unit_count, crop_count = 0., 0, 0
    for i in todo:
        started = time.monotonic()
        torch.cuda.reset_peak_memory_stats()
        name = str(z['images'][i]); rows = image_rows(z, i)
        shape = tuple(z['shapes'][i])
        groups = crop_groups(a, z, rows, shape)
        vectors = np.empty((len(rows), 2, 3, 1024), np.float16)
        rgbs = [s5.load_rgb(c.raw_dataset(a.dataset, a.split), name, view) for view in ('t1', 't2')]
        if any(rgb.shape[:2] != shape for rgb in rgbs):
            raise ValueError(f'{name}: RGB shape differs from masks')
        with torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
            for box, positions in groups.items():
                ix = np.asarray(positions)
                iw, rw = weights(z, rows[ix], shape, grid, 'cuda', box)
                x0, y0, x1, y1 = box
                for view_id, original in enumerate(rgbs):
                    rgb = np.array(Image.fromarray(original[y0:y1, x0:x1]).resize((size, size), Image.Resampling.BILINEAR))
                    pixels = torch.from_numpy(rgb).permute(2, 0, 1)[None].to('cuda').float() / 255
                    output = model(pixel_values=(pixels-mean)/std)
                    offset = 1 + mc.num_register_tokens
                    t12 = model.norm(captured.pop('L12'))[0, offset:]
                    t24 = output.last_hidden_state[0, offset:]
                    if t24.shape != (grid*grid, 1024):
                        raise ValueError('Patch token grid mismatch')
                    with torch.autocast('cuda', enabled=False):
                        pooled = torch.stack([iw @ t12.float(), iw @ t24.float(), rw @ t24.float()], dim=1)
                    vectors[ix, view_id] = pooled.cpu().to(torch.float16).numpy()
                    del output, t12, t24, pooled, pixels
        torch.cuda.synchronize()
        seconds = time.monotonic() - started
        save(part_path(a, name), image=name, id=z['id'][rows], vectors=vectors, config=json.dumps(cfg),
             seconds=seconds, unique_crops=len(groups),
             peak_vram_bytes=torch.cuda.max_memory_allocated(), gpu=torch.cuda.get_device_name())
        seconds = time.monotonic() - started
        elapsed += seconds; unit_count += len(rows); crop_count += len(groups)
        per_unit = f'{seconds/len(rows):.4f}' if len(rows) else 'NA'
        print(f'{a.dataset}/{a.split}/{name}: {len(rows)} units, {len(groups)} crops, '
              f'{seconds:.2f}s including write, {per_unit}s/unit', flush=True)
    hook.remove()
    write(feature_dir(a, a.split) / f'throughput-shard{a.shard}.json',
          dict(images=len(todo), units=unit_count, unique_crops=crop_count, seconds=elapsed,
               seconds_per_unit=elapsed/unit_count if unit_count else None,
               seconds_per_crop=elapsed/crop_count if crop_count else None,
               scope='newly extracted images; includes read/forward/pool/write, excludes model load', config=cfg))


def merge(a):
    z, cfg = load_units(a, a.split), config(a)
    dest = feature_dir(a, a.split)
    dest.mkdir(parents=True, exist_ok=True)
    temp = dest / f'vectors.{os.getpid()}.tmp.npy'
    mm = np.lib.format.open_memmap(temp, mode='w+', dtype=np.float16, shape=(len(z['id']), 2, 3, 1024))
    seconds, peak, crops = 0., 0, 0
    paths = []
    for i, name in enumerate(z['images']):
        rows = image_rows(z, i)
        path = part_path(a, str(name)); q = c.archive(path)
        check_part(q, z, rows, str(name), cfg)
        mm[rows] = q['vectors']
        seconds += float(q['seconds']); peak = max(peak, int(q['peak_vram_bytes']))
        crops += int(q.get('unique_crops', bool(len(rows))))
        paths.append(str(path.relative_to(dest)))
    mm.flush(); del mm
    temp.replace(dest / 'vectors.npy')
    save(dest / 'index.npz', **{k: z[k] for k in ('images', 'image_id', 'id')}, parts=np.array(paths), config=json.dumps(cfg))
    write(dest / 'metadata.json', dict(cfg, units=len(z['id']), images=len(z['images']), seconds=seconds,
                                      gpu_hours=seconds/3600, peak_vram_bytes=peak, unique_crops=crops,
                                      seconds_scope='read/forward/pool; throughput-shard files include write'))
    print(f'Indexed {len(z["id"])} vectors; {seconds/3600:.3f} GPU hours', flush=True)


def main():
    p = parser(__doc__)
    p.add_argument('stage', choices=('extract', 'merge', 'all'))
    p.add_argument('--split', choices=('train', 'val', 'test'), required=True)
    p.add_argument('--model', default=MODEL)
    p.add_argument('--crop', choices=('full', 'local'), default='full')
    shard_args(p)
    a = setup(p)
    if a.crop == 'local' and a.dataset != 'sysu':
        p.error('--crop local is the SYSU E3 experiment')
    if a.stage == 'all' and a.shards != 1:
        p.error('Use extract shards then merge')
    if a.stage in ('extract', 'all'):
        extract(a)
    if a.stage in ('merge', 'all'):
        merge(a)


if __name__ == '__main__':
    main()
