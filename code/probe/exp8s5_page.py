"""Append F0/frozen-filter comparisons to a self-contained copy of batch44."""
from exp8s5_common import *
import colorsys
from html.parser import HTMLParser
import shutil
from PIL import Image, ImageDraw
sys.path.insert(0, str(HERE.parent / 'batch44'))
from exp8s4_page import table, esc


def flow_names(ds, limit):
    seq = read(OLD_PAGE / ds / 'samples.json')['flow']
    return seq[:limit] if limit else seq


def paint(rgb, mask, color, alpha=.4):
    rgb[mask] = (rgb[mask] * (1 - alpha) + np.asarray(color) * alpha).astype(np.uint8)
    edge = mask & ~ndi.binary_erosion(mask, EIGHT)
    rgb[edge] = color


def panel(d, scheme, step):
    rgb = load_rgb(c.raw_dataset(d['ds'], 'train'), d['name'], 't2').copy()
    units = filtered(d, scheme)
    keep = {u['id']: u for u in units}
    fill_ids = set(int(x) for x in d['z']['id'])
    if step == 3:
        for ident, o in enumerate(d['objects']):
            if ident in fill_ids and ident not in keep:
                continue
            x0, y0, x1, y1 = o['bbox']
            mask = o['mask']
            if ident in keep:
                mask = (mask & d['base'][y0:y1, x0:x1]) | keep[ident]['mask']
            color = tuple(int(255 * v) for v in colorsys.hsv_to_rgb((ident * .61803398875) % 1, .7, 1))
            paint(rgb[y0:y1, x0:x1], mask, color)
    else:
        union = np.zeros_like(d['gt'])
        for u in units:
            add_mask(union, u)
        paint(rgb, union, (36, 188, 130), .65)
    image = Image.new('RGB', (rgb.shape[1], rgb.shape[0] + 28), '#ffffff')
    image.paste(Image.fromarray(rgb), (0, 28))
    ImageDraw.Draw(image).text((8, 8), f'Step {step} | {scheme} | fill units: {len(units)}', fill='black')
    return image


def render(a):
    choice = frozen(a)
    for name in flow_names(a.dataset, a.limit)[a.shard::a.shards]:
        d = load(a.dataset, 'train', name)
        images = []
        for step in (3, 5):
            left, right = panel(d, 'F0', step), panel(d, choice['scheme'], step)
            im = Image.new('RGB', (left.width + right.width, left.height), 'white')
            im.paste(left, (0, 0))
            im.paste(right, (left.width, 0))
            relative = Path('images/exp8s5') / a.dataset / f'{Path(name).stem}-step{step}.png'
            dest = local(a.page_out / relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest)
            images.append(str(relative))
        kept = filtered(d, choice['scheme'])
        write(a.page_out / 'exp8s5' / a.dataset / (Path(name).stem + '.json'),
              dict(dataset=a.dataset, image=name, limit=a.limit, scheme=choice['scheme'],
                   images=images, before=len(d['candidates']), after=len(kept)))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.files = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value and not value.startswith(('#', 'http:', 'https:', 'data:')):
                self.files.add(value.split('#')[0])


def build(a):
    old = (OLD_PAGE / 'index.html').read_text()
    links = Links()
    links.feed(old)
    # Include every original image, as well as linked JSON downloads.
    sources = links.files | {str(p.relative_to(OLD_PAGE)) for p in (OLD_PAGE / 'images').rglob('*') if p.is_file()}
    for relative in sorted(sources):
        src = (OLD_PAGE / relative).resolve()
        if not src.is_relative_to(OLD_PAGE.resolve()) or not src.is_file():
            raise ValueError(f'Invalid old-page asset: {relative}')
        dst = local(a.page_out / relative)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    body = ['<section id="exp8s5"><h2>6 · 实验8补充5：碎片过滤对比</h2>',
            '<p>候选负责提供待查清单，补漏、去误检和掩码精修合并到MLLM阶段。本页只读缓存，没有调用模型。</p>',
            '<p>第3步保留精修组，仅对补漏组应用过滤；第5步显示补漏新增像素。F2若被选中，第3步显示原有D内像素加开运算后的新增像素。左右采用同一原始图像范围和单元颜色。</p>']
    if a.limit:
        body.append('<p class="notice"><strong>仅为冒烟预览：</strong>冻结选择只使用每个数据集'+str(a.limit)+'张固定验证图，不能作为正式方案。原页面内容仍为此前完整训练审核。</p>')
    records = []
    for ds in ('second', 'sysu'):
        choice = frozen(a, ds)
        body.append(f'<h3>{ds.upper()} · 验证冻结 {choice["scheme"]}</h3>')
        for name in flow_names(ds, a.limit):
            row = read(a.page_out / 'exp8s5' / ds / (Path(name).stem + '.json'))
            if row['limit'] != a.limit or row['scheme'] != choice['scheme']:
                raise ValueError('Page render/freeze scope mismatch')
            records.append(row)
            body.append(f'<h4>{esc(name)} · 补漏单元 {row["before"]} → {row["after"]}</h4>')
            for step, path in zip((3, 5), row['images']):
                caption = f'第{step}步：左 F0；右 {row["scheme"]}。点击查看原分辨率。'
                body.append(f'<figure><a href="{esc(path)}"><img loading="lazy" src="{esc(path)}" alt="{esc(caption)}"></a><figcaption>{esc(caption)}</figcaption></figure>')
    detail = read(a.out / 'inspection/train_2697.json')
    body += ['<h3>train_2697 右上角逐像素核查</h3>', '<p>'+esc(detail['diagnosis'])+'</p>',
             table(['GT目标/漏检域', '目标像素', 'D覆盖率', '棋盘/欧氏距离', '语义转移'],
                   [(f'{r["target"]}/{r["id"]}', r['target_area'], f'{r["detector_coverage"]:.2%}',
                     f'{r["distance"]}/{r["distance_euclidean"]}', r['transition']) for r in detail['targets']])]
    for path in (a.out / 'inspection').glob('*'):
        dst = local(a.page_out / 'exp8s5/inspection' / path.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst)
    body.append('<p>粉色为②漏检域轮廓，青色为D轮廓。放大采用最近邻，网格间隔为一个原始像素。</p><a href="exp8s5/inspection/train_2697-upper-right-4x.png"><img src="exp8s5/inspection/train_2697-upper-right-4x.png" alt="train_2697右上角原像素放大核查"></a></section>')
    if old.count('</main>') != 1:
        raise ValueError('Expected one main element in old page')
    result = old.replace('</main>', ''.join(body) + '</main>')
    (a.page_out / 'index.html').write_text(result)
    links = Links()
    links.feed(result)
    missing = [r for r in links.files if not (a.page_out / r).is_file()]
    if missing:
        raise FileNotFoundError(f'Page has missing assets: {missing}')
    write(a.page_out / 'exp8s5/delivery.json', dict(partial=bool(a.limit), limit=a.limit,
          examples=len(records), old_assets=len(sources), linked_files=len(links.files),
          image_bytes=sum(p.stat().st_size for p in (a.page_out / 'images').rglob('*') if p.is_file()),
          original_page=str(OLD_PAGE / 'index.html'), records=records,
          publication='未发布；由Claude替换原Artifact链接'))


if __name__ == '__main__':
    p = parser(__doc__, ('render', 'build'))
    p.add_argument('--page-out', type=Path, default=HERE / 'page')
    a = setup(p)
    a.page_out = local(a.page_out)
    a.page_out.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    {'render': render, 'build': build}[a.stage](a)
    timed(a, 'page-' + a.stage, start)
