"""V2 384px near crops and three-times context; red novel pixels, cyan detector D."""
from __future__ import annotations
import argparse
from exp6j_common import *


def crop_box(bbox, shape, scale=4):
    import numpy as np
    x0,y0,x1,y1 = bbox
    side = int(min(max(scale * max(x1-x0,y1-y0),64),*shape))
    left = int(np.clip((x0+x1)/2-side/2,0,shape[1]-side))
    top = int(np.clip((y0+y1)/2-side/2,0,shape[0]-side))
    return [left,top,left+side,top+side]


def context_box(near, shape):
    import numpy as np
    x0,y0,x1,y1 = near
    side = int(min(3*(x1-x0), *shape))
    left = int(np.clip((x0+x1)/2-side/2,0,shape[1]-side))
    top = int(np.clip((y0+y1)/2-side/2,0,shape[0]-side))
    return [left,top,left+side,top+side]


def edge(mask):
    import numpy as np
    from PIL import Image, ImageFilter
    enlarged = Image.fromarray(mask.astype('uint8')*255).resize((384,384),Image.Resampling.NEAREST)
    padded = Image.new('L',(388,388))
    padded.paste(enlarged,(2,2))
    inner = padded.filter(ImageFilter.MinFilter(3)).crop((2,2,386,386))
    boundary = (np.asarray(enlarged)>0) & (np.asarray(inner)==0)
    return np.asarray(Image.fromarray(boundary.astype('uint8')*255).filter(ImageFilter.MaxFilter(3)))>0


def render_pair(rgb, novel, detector, box, context=False):
    import numpy as np
    from PIL import Image
    l,t,r,b = box
    red = edge(novel[t:b,l:r])
    cyan = edge(detector[t:b,l:r]) if context else None
    output = []
    for image in rgb:
        im = Image.fromarray(image[t:b,l:r]).resize((384,384),Image.Resampling.BICUBIC)
        pixels = np.array(im)
        if context:
            pixels[cyan] = (0,255,255)
        pixels[red] = (255,0,0)
        output.append(Image.fromarray(pixels))
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--datasets', nargs='+', choices=('sysu','second'), default=['sysu','second'])
    p.add_argument('--out', type=Path, default=RUN)
    p.add_argument('--limit', type=int, default=0, help='Render first N units, keeping the full sample manifest')
    args = p.parse_args()
    if args.limit < 0:
        p.error('--limit must be >= 0')
    args.out = local(args.out)
    sys.path.insert(0,str(B43))
    import exp8s3_common as b43
    import numpy as np
    from common import load_rgb
    for dataset in args.datasets:
        path = sample_path(args.out,'step2',dataset)
        rows = read_jsonl(path)
        selected = rows[:args.limit or None]
        current = None
        for row in selected:
            if row['image'] != current:
                current = row['image']
                raw = b43.raw_dataset(dataset,'val')
                rgb = [load_rgb(raw,current,v) for v in ('t1','t2')]
                _,base,_,_,_ = b43.pixels(dataset,'val',current)
                objects,shape = b43.pool_objects(dataset,'val',current)
                if tuple(shape) != base.shape:
                    raise ValueError('Image and pool shape mismatch')
            obj = objects[row['unit_id']]
            x0,y0,x1,y1 = obj['bbox']
            local_new = obj['mask'] & ~base[y0:y1,x0:x1]
            if int(local_new.sum()) != row['novel']:
                raise ValueError(f"{row['cid']}: rendered novel pixels differ from sampled unit")
            yy,xx = np.nonzero(local_new)
            bbox = [int(xx.min()+x0),int(yy.min()+y0),int(xx.max()+x0+1),int(yy.max()+y0+1)]
            novel = np.zeros(base.shape,bool)
            novel[y0:y1,x0:x1] = local_new
            near = crop_box(bbox,base.shape)
            wide = context_box(near,base.shape)
            folder = local(args.out / 'inputs/step2' / dataset / Path(current).stem / str(row['unit_id']))
            folder.mkdir(parents=True,exist_ok=True)
            inputs = {}
            for context, box, keys in ((False,near,('t1','t2')),(True,wide,('context_t1','context_t2'))):
                for key,im in zip(keys,render_pair(rgb,novel,base,box,context)):
                    dest = folder / f'{key}.png'
                    im.save(dest)
                    inputs[key] = str(dest)
            row.update(inputs=inputs,crop=near,context_crop=wide,bbox=bbox)
        write_jsonl(path,rows)
        print(dataset, 'rendered', len(selected), flush=True)

if __name__ == '__main__':
    main()
