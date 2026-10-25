"""只读训练缓存的统计、固定抽样与审核页渲染。使用方法见 README_batch44.md。"""
from __future__ import annotations
import argparse
from collections import defaultdict
from functools import lru_cache
import json
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['MPLCONFIGDIR'] = str(HERE / '.mplconfig')
sys.path.insert(0, str(HERE.parent / 'batch43'))
import exp8s3_common as c
import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFont
from matplotlib import colormaps
from common import load_rgb

RUN = HERE.parent / 'batch43/run'
SEED = 20261025
SIDE = 240
EIGHT = np.ones((3, 3), bool)
COLORS = dict(gt='#ffffff', detector='#278bd2', B='#e69626', **{'G0.05':'#25b9ba'},
              H1='#be7ee0', fill='#24bc82', refine='#eb9735', positive='#24bc82',
              negative='#ed5656', middle='#b6ac91', ignore='#be7ee0',
              cell1='#36b9e8', cell2='#ff29ad', cell3='#f8bc38', cell5='#a9a9b3',
              unit='#ff4444', uncovered='#ff29ad', ink='#202632', paper='#ffffff')
GROUPS = {'positive2':'正例且②为主', 'positive3':'正例且③为主（训练忽略）',
          'hard_negative':'难负例', 'random_negative':'随机负例'}
SMOKE_IMAGES = {'second':['train_0.png'], 'sysu':['train_12848.png']}
FONT = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError('所有产物必须写入 batch44')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp.json')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    temp.replace(path)


@lru_cache(maxsize=2)
def manifest(ds):
    m = read(RUN / ds / 'manifest.json')
    rule = m['rule']
    if m['partial'] or rule['method'] != 'a' or rule['A_max'] is not None or rule['tau'] != .1 or rule['prefilter_novel_lt'] is not None:
        raise ValueError('需要完整训练池及 R(0.1)、无新增预过滤的冻结规则')
    if m['images']['train'] != read(c.root(ds) / 'protocol.json')['train_images']:
        raise ValueError('训练图像列表与 V1 协议不一致')
    return m


def names(ds, limit, images=None):
    all_names = manifest(ds)['images']['train']
    chosen = images or all_names
    if not set(chosen) <= set(all_names):
        raise ValueError('只允许训练划分图像')
    return chosen[:limit] if limit else chosen


def units(ds, name):
    return c.archive(RUN / ds / 'units/train' / (Path(name).stem+'.npz'))


def load(ds, name):
    gt, base, prob, cell, _ = c.train_pixels(ds, name)
    objects, shape = c.pool_objects(ds, 'train', name)
    meta = read(c.pool_dir(ds, 'train') / 'images' / (Path(name).stem+'.json'))
    details = meta['units']['U5_x4']['details']
    z = units(ds, name)
    if tuple(shape) != gt.shape or len(objects) != len(details):
        raise ValueError(f'{ds}/{name}: 掩码与元数据不一致')
    fill, refine = np.zeros_like(gt), np.zeros_like(gt)
    source_masks = {s:np.zeros_like(gt) for s in meta['config']['sources']}
    fill_sources = {s:np.zeros_like(gt) for s in source_masks}
    ratios = []
    for i, (obj, row) in enumerate(zip(objects, details)):
        x0,y0,x1,y1 = obj['bbox']; mask = obj['mask']; sl = np.s_[y0:y1,x0:x1]
        if row['unit_id'] != i or obj['area'] != row['area'] or obj['bbox'] != row['bbox']:
            raise ValueError(f'{ds}/{name}/{i}: 单元编号或几何不一致')
        r = float(base[sl][mask].mean()); ratios.append(r)
        (fill if r < .1 else refine)[sl] |= mask
        for s in row['sources']:
            source_masks[s][sl] |= mask
            if r < .1:
                fill_sources[s][sl] |= mask
    ratios = np.asarray(ratios)
    if not np.array_equal(np.flatnonzero(ratios < .1), z['id']) or not np.allclose(ratios[z['id']], z['r']):
        raise ValueError(f'{ds}/{name}: 补漏分流与 batch43 不一致')
    return dict(ds=ds, name=name, gt=gt, base=base, prob=prob, cell=cell, objects=objects,
                details=details, meta=meta, z=z, r=ratios, fill=fill, refine=refine,
                sources=source_masks, fill_sources=fill_sources)


def components(data):
    """与 batch39 同口径：逐 GT 目标内拆 GT & ~D，避免相邻语义目标合并。"""
    gt, base, cell = (data[k] for k in ('gt','base','cell'))
    gl, _, _ = c.gt_label(c.raw_dataset(data['ds'], 'train'), data['name'], gt)
    labels = np.zeros(gt.shape, np.int32); rows = []
    for target, sl in enumerate(ndimage.find_objects(gl), 1):
        if sl is None:
            continue
        local, count = ndimage.label((gl[sl] == target) & ~base[sl], EIGHT)
        for k, piece in enumerate(ndimage.find_objects(local), 1):
            mask = local[piece] == k
            ys = slice(sl[0].start+piece[0].start, sl[0].start+piece[0].stop)
            xs = slice(sl[1].start+piece[1].start, sl[1].start+piece[1].stop)
            groups = np.unique(cell[ys,xs][mask])
            if len(groups) != 1 or groups[0] not in (1,2,3,5):
                raise ValueError('漏检连通域的格标签不一致')
            ident = len(rows)+1
            labels[ys,xs][mask] = ident
            rows.append(dict(id=ident, target=target, cell=int(groups[0]), area=int(mask.sum()),
                             bbox=[xs.start,ys.start,xs.stop,ys.stop]))
    if not np.array_equal(labels > 0, gt & ~base):
        raise ValueError('漏检连通域未完整覆盖漏检像素')
    return gl, labels, rows


def summarize(data):
    gl, labels, rows = components(data)
    base, prob = data['base'], data['prob']
    chess = ndimage.distance_transform_cdt(~base, metric='chessboard') if base.any() else None
    euclidean = ndimage.distance_transform_edt(~base) if base.any() else None
    targets = []
    for row in rows:
        if row['cell'] not in (1,2):
            continue
        x0,y0,x1,y1 = row['bbox']; sl = np.s_[y0:y1,x0:x1]
        mask = labels[sl] == row['id']; area = row['area']
        def coverage(m):
            return float(m[sl][mask].sum()/area)
        distance = int(chess[sl][mask].min()) if chess is not None else None
        if row['cell'] == 1 and (distance is None or distance > 1):
            raise ValueError('① 的棋盘距离必须 ≤1')
        if row['cell'] == 2 and distance is not None and distance <= 1:
            raise ValueError('② 与检测结果发生一像素邻接')
        targets.append(dict(row, image=data['name'], distance=distance,
                            distance_euclidean=float(euclidean[sl][mask].min()) if euclidean is not None else None,
                            mean_probability=float(prob[sl][mask].mean()), max_probability=float(prob[sl][mask].max()),
                            fill_coverage=coverage(data['fill']), refine_coverage=coverage(data['refine']),
                            candidate_coverage=coverage(data['fill'] | data['refine']),
                            source_coverage={s:coverage(m) for s,m in data['sources'].items()},
                            fill_source_coverage={s:coverage(m) for s,m in data['fill_sources'].items()}))
    z = data['z']; pools = {'positive2':[], 'positive3':[], 'negative':[]}
    for i, ident in enumerate(z['id']):
        category = ('positive2' if z['majority'][i] == 2 else 'positive3' if z['majority'][i] == 3 else None) if z['label'][i] == 1 else 'negative' if z['label'][i] == 0 else None
        if category:
            pools[category].append(dict(image=data['name'], id=int(ident), mean=float(z['x'][i,0])))
    return dict(image=data['name'], targets=targets, pools=pools,
                has1=bool((data['cell']==1).any()), has2=bool((data['cell']==2).any()),
                positive=bool((z['label']==1).any()), unit_count=len(data['objects']), fill_count=len(z['id']))


def stats(args):
    chosen = names(args.dataset, args.limit, args.images)
    image_names = chosen[args.shard::args.shards]
    started = time.monotonic(); rows = []
    for name in image_names:
        t = time.monotonic()
        row = summarize(load(args.dataset, name)); row['seconds'] = time.monotonic()-t
        write(args.out / args.dataset / 'stats' / (Path(name).stem+'.json'), row)
        rows.append(name)
        print(f'{args.dataset}/{name}: {row["seconds"]:.3f}s, {len(row["targets"])} 个①②目标', flush=True)
    write(args.out / args.dataset / 'shards' / f'{args.shard:03d}.json',
          dict(images=rows, inventory=chosen, shards=args.shards, shard=args.shard,
               partial=bool(args.limit or args.images), seconds=time.monotonic()-started))


def pick(rng, items, count, description):
    if len(items) < count:
        raise ValueError(f'{description}: 需要 {count} 个，当前仅 {len(items)} 个；扩大 --limit 或使用全量统计')
    return [items[int(i)] for i in rng.choice(len(items), count, replace=False)]


def merge(args):
    ds = args.dataset
    parts = [read(args.out / ds / 'shards' / f'{i:03d}.json') for i in range(args.shards)]
    expected = names(ds, args.limit, args.images)
    for i, part in enumerate(parts):
        if part['inventory'] != expected or part['images'] != expected[i::args.shards] or part['shards'] != args.shards:
            raise ValueError('分片不完整或训练列表/分片参数不一致')
    rows = [read(args.out / ds / 'stats' / (Path(n).stem+'.json')) for n in expected]
    rng = np.random.default_rng(SEED)
    flow_count, unit_count, target_count = (1,2,2) if args.limit else (6,24,30)
    # 顺序满足条件；已抽中的样本也计入后续条件，最后从剩余图像补齐。
    selected = []
    for key, quota in [('has2',1 if args.limit else 3),('has1',0 if args.limit else 1),('positive',1)]:
        need = max(0, quota-sum(r[key] for r in selected))
        selected += pick(rng, [r for r in rows if r[key] and r not in selected], need, key)
    selected += pick(rng, [r for r in rows if r not in selected], flow_count-len(selected), '流程图')
    rng.shuffle(selected)
    pools = {key:[u for r in rows for u in r['pools'][key]] for key in ('positive2','positive3','negative')}
    negatives = sorted(pools['negative'], key=lambda u:(-u['mean'],u['image'],u['id']))
    top = negatives[:max(1,int(np.ceil(len(negatives)*.1)))]
    galleries = {key:pick(rng, pools[key], unit_count, key) for key in ('positive2','positive3')}
    galleries['hard_negative'] = pick(rng, top, unit_count, '难负例')
    galleries['random_negative'] = pick(rng, pools['negative'], unit_count, '随机负例')
    targets = [t for r in rows for t in r['targets']]
    chosen_targets = pick(rng, [t for t in targets if t['cell']==2], target_count, '②目标')
    sample = dict(dataset=ds, seed=SEED, partial=bool(args.limit or args.images), images=expected,
                  scope_selection='显式指定的小冒烟图像，先保证两类正例、负例及两个②目标均存在；不代表全训练分布。' if args.images else '训练 manifest 前 N 张的小样本。' if args.limit else '完整训练划分。',
                  flow=[r['image'] for r in selected], units=galleries, targets=chosen_targets,
                  flow_conditions={k:sum(r[k] for r in selected) for k in ('has1','has2','positive')},
                  sampling='先满足流程图分层配额，再随机补齐；单元类内无放回；难负例为负例均值排序前 ceil(10%)；随机负例从全部负例抽取，可与难负例重复；②按目标无放回随机抽样，不按覆盖筛选。',
                  negative_count=len(negatives), hard_pool_count=len(top),
                  hard_mean_min=top[-1]['mean'], unit_counts={k:len(v) for k,v in pools.items()})
    write(args.out / ds / 'samples.json', sample)
    bins = {}
    for group in (1,2):
        subset = [t for t in targets if t['cell']==group]
        bins[str(group)] = {}
        for key in ('distance','area','mean_probability','max_probability','fill_coverage','refine_coverage','candidate_coverage'):
            values = [t[key] for t in subset if t[key] is not None]
            hist, edges = np.histogram(values, bins=12, range=(0,1) if 'probability' in key or 'coverage' in key else None)
            bins[str(group)][key] = dict(counts=hist.tolist(), edges=edges.tolist(), missing=len(subset)-len(values))
    write(args.out / ds / 'targets.json', dict(dataset=ds, partial=sample['partial'], images=expected, targets=targets))
    write(args.out / ds / 'summary.json', dict(dataset=ds, partial=sample['partial'], image_count=len(rows),
          target_count={str(k):sum(t['cell']==k for t in targets) for k in (1,2)}, bins=bins,
          stats_seconds=sum(r['seconds'] for r in rows), seconds_per_image=float(np.mean([r['seconds'] for r in rows]))))
    print(f'{ds}: 合并 {len(rows)} 图，抽样 {len(selected)} 流程 / {4*unit_count} 单元 / {target_count} 目标', flush=True)


def rgb(color):
    return tuple(bytes.fromhex(COLORS.get(color,color).lstrip('#')))


def overlay(im, mask, color, alpha=.48, outline=False):
    a = np.array(im).copy()
    if outline:
        mask = mask & ~ndimage.binary_erosion(mask, EIGHT)
    a[mask] = (a[mask]*(1-alpha)+np.asarray(rgb(color))*alpha).astype(np.uint8)
    return Image.fromarray(a)


def full(obj, shape):
    mask = np.zeros(shape, bool); x0,y0,x1,y1=obj['bbox']
    mask[y0:y1,x0:x1] = obj['mask']
    return mask


def union(objects, shape):
    result=np.zeros(shape,bool)
    for obj in objects:
        x0,y0,x1,y1=obj['bbox']; result[y0:y1,x0:x1] |= obj['mask']
    return result


def heat(prob):
    return Image.fromarray((colormaps['viridis'](np.clip(prob,0,1))[...,:3]*255).astype(np.uint8))


def tile(im, label='', colorbar=False, side=SIDE):
    # 流程步骤图用 512 像素（SECOND 原分辨率），其余面板 240 像素。
    out = Image.new('RGB',(side,side+26),rgb('paper'))
    out.paste(im.resize((side,side), Image.Resampling.NEAREST if im.mode=='1' else Image.Resampling.LANCZOS))
    draw=ImageDraw.Draw(out)
    if colorbar:
        bar=heat(np.linspace(0,1,160)[None,:]).resize((160,10))
        out.paste(bar,(40,side+7)); draw.text((4,side+4),'0',fill=rgb('ink'),font=FONT)
        draw.text((218,side+4),'1',fill=rgb('ink'),font=FONT)
    else:
        draw.text((6,side+5),label,fill=rgb('ink'),font=FONT)
    return out


def strip(panels):
    out=Image.new('RGB',(sum(p.width for p in panels),max(p.height for p in panels)),rgb('paper'))
    x=0
    for p in panels:
        out.paste(p,(x,0)); x+=p.width
    return out


def save_image(out, relative, im):
    dest=out/relative; dest.parent.mkdir(parents=True,exist_ok=True)
    im.save(dest,'JPEG',quality=80,optimize=True)
    return str(relative)


def annotated(im, objects, shape, colors, texts=None):
    for i,obj in enumerate(objects):
        im=overlay(im, full(obj,shape), colors[i], .3)
        im=overlay(im, full(obj,shape), colors[i], 1, True)
    if texts:
        draw=ImageDraw.Draw(im)
        for obj,text in zip(objects,texts):
            x0,y0,x1,y1=obj['bbox']; xy=(x0,y0)
            box=draw.textbbox(xy,text,font=FONT); draw.rectangle(box,fill=rgb('ink'))
            draw.text(xy,text,font=FONT,fill=rgb('paper'))
    return im


def flow(data, out):
    ds,name=data['ds'],data['name']; stem=Path(name).stem; prefix=Path('images')/ds
    t1,t2=(Image.fromarray(load_rgb(c.raw_dataset(ds,'train'),name,v)) for v in ('t1','t2'))
    gt,base,prob,cell=(data[k] for k in ('gt','base','prob','cell'))
    cells=t2.copy()
    for k in (1,2,3,5): cells=overlay(cells,cell==k,'cell'+str(k),.7)
    cells=overlay(cells,gt,'gt',1,True)
    raw_img=strip([tile(t1,'T1'),tile(t2,'T2'),tile(Image.fromarray(gt),'GT 0 / 255'),
                   tile(Image.fromarray(base),'D > 0.5'),tile(heat(prob),colorbar=True),tile(cells,'1 / 2 / 3 / 5')])
    raw_path=save_image(out,prefix/(stem+'-raw.jpg'),raw_img)
    pool=c.pool_dir(ds,'train'); meta=data['meta']; shape=gt.shape
    original,_=c.b40.load_units(pool/'objects_none'/(stem+'.npz'))
    merged,_=c.b40.load_units(pool/'objects_direct_pairs'/(stem+'.npz'))
    original_rows=meta['units']['objects_none']['details']; merged_rows=meta['units']['objects_direct_pairs']['details']
    for objects,details in ((original,original_rows),(merged,merged_rows)):
        if len(objects)!=len(details) or any(o['area']!=r['area'] or o['bbox']!=r['bbox'] for o,r in zip(objects,details)):
            raise ValueError('原始/合并候选掩码与元数据不一致')
    if len(original)!=meta['input_objects']:
        raise ValueError('原始候选计数不一致')
    merged_mask=union(merged,shape)
    unit_mask=union(data['objects'],shape)
    split_lost=int((merged_mask & ~unit_mask).sum())
    source_panels=[]; source_counts={}
    for source in data['sources']:
        objs=[o for o,r in zip(original,original_rows) if source in r['sources']]
        source_counts[source]=dict(count=len(objs),pixels=int(union(objs,shape).sum()))
        source_panels.append(tile(annotated(t2.copy(),objs,shape,[source]*len(objs)),f'{source}: {len(objs)}'))
    objects=data['objects']; z=data['z']; ids=z['id']; rows=data['details']
    # 定性区分单元，不承载类别含义；具体编号和语义色另有图例。
    palette=[tuple(int(v*255) for v in colormaps['turbo']((i*.61803398875)%1)[:3]) for i in range(len(objects))]
    unique=['#%02x%02x%02x'%p for p in palette]
    labels={int(i):j for j,i in enumerate(ids)}
    route_colors=['fill' if r<.1 else 'refine' for r in data['r']]
    route_text=[f'{i}:{r:.2f}' for i,r in enumerate(data['r'])]
    label_colors=[]; label_text=[]; table=[]
    fill_objs=[objects[int(i)] for i in ids]
    positive=np.zeros(shape,bool)
    for j,i in enumerate(ids):
        i=int(i); lab=int(z['label'][j]); maj=int(z['majority'][j])
        color='ignore' if lab==1 and maj==3 else 'positive' if lab==1 else 'negative' if lab==0 else 'middle'
        label_colors.append(color); label_text.append(f'{i}:{z["precision"][j]:.0%}')
        if lab==1: positive |= full(objects[i],shape)
    for i,obj in enumerate(objects):
        j=labels.get(i)
        table.append(dict(id=i,r=float(data['r'][i]),group='补漏' if j is not None else '精修',
                          novel=int((full(obj,shape)&~base).sum()),
                          precision=float(z['precision'][j]) if j is not None else None,
                          label=label_colors[j] if j is not None else None))
    novel=data['fill']&~base
    gl, target_labels, target_rows=components(data)
    absent=np.zeros(shape,bool)
    for row in target_rows:
        if row['cell']==2:
            mask=target_labels==row['id']
            if not ((data['fill']|data['refine'])&mask).any(): absent |= mask
    covered=(cell==2)&positive
    last=overlay(t2,cell==2,'cell2',.2)
    last=overlay(last,covered,'positive',.85); last=overlay(last,absent,'uncovered',.95)
    last=overlay(last,cell==2,'gt',1,True)
    big=512  # 步骤图不在图上写编号，逐单元的 r、GT 占比放在页面表格里
    stage_images=[strip(source_panels), tile(annotated(t2.copy(),merged,shape,['fill']*len(merged)),'D1',side=big),
                  tile(annotated(t2.copy(),objects,shape,unique),'U5_x4',side=big),
                  tile(annotated(t2.copy(),objects,shape,route_colors),'fill / refine',side=big),
                  tile(overlay(t2,novel,'fill',.65),'U5 - D',side=big),
                  tile(annotated(t2.copy(),fill_objs,shape,label_colors),'label',side=big),tile(last,'2',side=big)]
    descriptions=[f'各来源原始候选分别显示：{len(original)} 个；像素并集 {int(union(original,shape).sum())}。',
                  f'D1 直接配对合并：跨来源重叠的原始候选最多参与一次配对，保存 {len(merged)} 个对象、并集 {int(union(merged,shape).sum())} 像素。',
                  f'U5_x4 用已保存的 SAM 分区拆分大对象，小对象保留完整：{len(objects)} 个单元、并集 {int(unit_mask.sum())} 像素；上游拆分跳过空分区和小于10像素的碎片，本图比合并对象减少 {split_lost} 像素。',
                  f'按单元自身 r 分流：补漏 {len(ids)} 个、精修 {len(objects)-len(ids)} 个。冻结规则未启用新增 < 100 预过滤，本步无丢弃。',
                  f'补漏组 {len(ids)} 个单元去掉已有检测结果，新增像素并集 {int(novel.sum())}。',
                  '按新增像素的 GT 占比标注：'+ '、'.join(f'{label} {label_colors.count(key)} 个' for key,label in [('positive','正例'),('negative','负例'),('middle','中间'),('ignore','③为主正例（忽略）')])+'。',
                  f'② 共 {int((cell==2).sum())} 像素；绿色为补漏正例覆盖 {int(covered.sum())} 像素，亮粉色为完全没有 U5 候选覆盖的目标 {int(absent.sum())} 像素；淡粉色为其余②。']
    steps=[dict(image=save_image(out,prefix/(stem+f'-step{k+1}.jpg'),im),description=desc) for k,(im,desc) in enumerate(zip(stage_images,descriptions))]
    return dict(image=name,raw=raw_path,steps=steps,table=table,sources=source_counts)


def cropbox(mask, factor=1):
    ys,xs=np.nonzero(mask); h,w=mask.shape
    length=min(max(h,w),max(24,int(max(xs.max()-xs.min()+1,ys.max()-ys.min()+1)*1.3))*factor)
    length=int(min(length,h,w)); cx=(xs.min()+xs.max()+1)/2; cy=(ys.min()+ys.max()+1)/2
    x=max(0,min(w-length,int(cx-length/2))); y=max(0,min(h-length,int(cy-length/2)))
    return (x,y,x+length,y+length)


def card_image(data, mask, target=False, gt_object=None):
    t1,t2=(Image.fromarray(load_rgb(c.raw_dataset(data['ds'],'train'),data['name'],v)) for v in ('t1','t2'))
    near,context=cropbox(mask),cropbox(mask,3)
    color='cell2' if target else 'unit'
    def outlined(im,with_context=False):
        if with_context:
            im=overlay(im,data['base'],'detector',.5)
        if target:
            im=overlay(im,gt_object,'gt',1,True)
        return overlay(im,mask,color,1,True)
    panels=[tile(outlined(t1).crop(near) if target else t1.crop(near),'T1'),tile(outlined(t2).crop(near),'T2'),
            tile(outlined(t1,True).crop(context),'T1 (x3)'),tile(outlined(t2,True).crop(context),'T2 (x3)'),
            tile(heat(data['prob']).crop(near),colorbar=True)]
    return strip(panels),dict(near=list(near),context=list(context))


def render(args):
    ds=args.dataset; sample=read(args.out/ds/'samples.json'); started=time.monotonic()
    score=c.archive(RUN/ds/'scores/M1-ign3/train.npz')
    if score['images'].tolist()!=manifest(ds)['images']['train'] or not np.isfinite(score['score']).all():
        raise ValueError('分数的训练图像列表或数值无效')
    needed=defaultdict(lambda:dict(units=[],targets=[]))
    for group,rows in sample['units'].items():
        for row in rows: needed[row['image']]['units'].append((group,row))
    for row in sample['targets']: needed[row['image']]['targets'].append(row)
    for name in sample['flow']: needed[name]
    rendered=dict(dataset=ds,features=manifest(ds)['feature_names'],flow=[],units={k:[] for k in GROUPS},targets=[],partial=sample['partial'])
    order={n:i for i,n in enumerate(manifest(ds)['images']['train'])}
    for name, tasks in needed.items():
        data=load(ds,name); z=data['z']; positions=np.flatnonzero(score['image_id']==order[name])
        if not np.array_equal(score['id'][positions],z['id']): raise ValueError('分数与单元编号不一致')
        x,_=c.features(ds,'train',name,data['objects'],data['prob'],data['details'])
        if not np.array_equal(x[z['id']],z['x']): raise ValueError('缓存 V1 特征不一致')
        if name in sample['flow']: rendered['flow'].append(flow(data,args.out))
        for group,row in tasks['units']:
            ident=row['id']; j=int(np.flatnonzero(z['id']==ident)[0]); obj=data['objects'][ident]
            mask=full(obj,data['gt'].shape); novel=mask&~data['base']
            precision=float((novel&data['gt']).sum()/novel.sum())
            if abs(precision-float(z['precision'][j]))>1e-7: raise ValueError('新增 GT 占比不一致')
            im,boxes=card_image(data,mask)
            path=Path('images')/ds/(Path(name).stem+f'-unit-{ident}.jpg')
            rendered['units'][group].append(dict(row,image_path=save_image(args.out,path,im),bbox=obj['bbox'],**boxes,
                area=obj['area'],novel=int(z['novel'][j]),sources=data['details'][ident]['sources'],r=float(z['r'][j]),
                precision=precision,label=int(z['label'][j]),majority=int(z['majority'][j]),score=float(score['score'][positions[j]]),
                features=dict(zip(rendered['features'],map(float,z['x'][j])))))
        if tasks['targets']:
            gl,labels,_=components(data)
            for row in tasks['targets']:
                mask=labels==row['id']
                if int(mask.sum())!=row['area']: raise ValueError('目标编号或面积不一致')
                im,boxes=card_image(data,mask,True,gl==row['target'])
                path=Path('images')/ds/(Path(name).stem+f'-target-{row["id"]}.jpg')
                rendered['targets'].append(dict(row,image_path=save_image(args.out,path,im),**boxes))
    rendered['flow'].sort(key=lambda row:sample['flow'].index(row['image']))
    rendered['seconds']=time.monotonic()-started; rendered['images_loaded']=len(needed)
    write(args.out/ds/'rendered.json',rendered)
    print(f'{ds}: 渲染 {len(needed)} 张输入图，{rendered["seconds"]:.3f}s',flush=True)


def page(args):
    from exp8s4_page import build
    build(args.out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('stats','merge','render','page','smoke'))
    p.add_argument('--out',type=Path,default=HERE/'run')
    p.add_argument('--dataset',choices=('second','sysu'))
    p.add_argument('--limit',type=int,default=0)
    p.add_argument('--images',nargs='+')
    p.add_argument('--shards',type=int,default=1); p.add_argument('--shard',type=int,default=0)
    args=p.parse_args(); args.out=args.out.resolve()
    if not args.out.is_relative_to(HERE) or args.out==HERE: p.error('--out 必须是当前目录的子目录')
    if args.limit<0 or not 0<=args.shard<args.shards: p.error('limit / shard 无效')
    if args.images and not args.limit: p.error('--images 小样本必须同时设置 --limit')
    if args.stage not in ('page','smoke') and not args.dataset: p.error('需要 --dataset')
    if args.stage in ('stats','merge','render') and not args.limit and not os.environ.get('SLURM_JOB_ID'):
        p.error('完整统计、抽样、渲染需由 Slurm 运行；登录节点仅允许 --limit 小冒烟')
    if args.stage=='smoke':
        if not args.limit: p.error('smoke 需显式 --limit 1')
        args.shards,args.shard,args.limit=1,0,1
        for ds in ('second','sysu'):
            args.dataset=ds; args.images=SMOKE_IMAGES[ds]
            stats(args); merge(args); render(args)
        page(args)
    else:
        globals()[args.stage](args)
    if 'torch' in sys.modules: raise RuntimeError('禁止导入 torch')

if __name__=='__main__': main()
