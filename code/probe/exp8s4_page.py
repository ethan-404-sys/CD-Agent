"""生成第 0–5 节的中文审核页面；只引用相对路径 JPEG。"""
from html import escape as esc
from pathlib import Path
import json
from PIL import Image

LABELS={'positive':'正例','negative':'负例','middle':'中间','ignore':'③为主（忽略）'}
GROUPS={'positive2':'正例且②为主','positive3':'正例且③为主（训练忽略）','hard_negative':'难负例','random_negative':'随机负例'}
SOURCE_TEXT={'B':'两期 SAM3 语义预测均有效且类别不同的区域', 'G0.05':'检测器概率大于 0.05 的连通区域',
             'H1':'已保存的 AnyChange 变化候选'}
CSS='''
:root{--bg:#faf9f5;--fg:#242930;--muted:#626a73;--line:#d5d8db;--surface:#eeefeb;--accent:#176c78;--gt:#ffffff;--detector:#278bd2;--B:#e69626;--G:#25b9ba;--H:#be7ee0;--fill:#24bc82;--refine:#eb9735;--positive:#24bc82;--negative:#ed5656;--middle:#b6ac91;--ignore:#be7ee0;--cell1:#36b9e8;--cell2:#ff29ad;--cell3:#f8bc38;--cell5:#a9a9b3;--unit:#ff4444;color-scheme:light}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#181c20;--fg:#e7e9eb;--muted:#b2bac3;--line:#454c53;--surface:#252b30;--accent:#79d6df;--gt:#ffffff;--detector:#278bd2;--B:#e69626;--G:#25b9ba;--H:#be7ee0;--fill:#24bc82;--refine:#eb9735;--positive:#24bc82;--negative:#ed5656;--middle:#b6ac91;--ignore:#be7ee0;--cell1:#36b9e8;--cell2:#ff29ad;--cell3:#f8bc38;--cell5:#a9a9b3;--unit:#ff4444;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#181c20;--fg:#e7e9eb;--muted:#b2bac3;--line:#454c53;--surface:#252b30;--accent:#79d6df;--gt:#ffffff;--detector:#278bd2;--B:#e69626;--G:#25b9ba;--H:#be7ee0;--fill:#24bc82;--refine:#eb9735;--positive:#24bc82;--negative:#ed5656;--middle:#b6ac91;--ignore:#be7ee0;--cell1:#36b9e8;--cell2:#ff29ad;--cell3:#f8bc38;--cell5:#a9a9b3;--unit:#ff4444;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:28px 72px;font:16px/1.75 "Noto Sans SC",system-ui,sans-serif;overflow-wrap:anywhere}main{max-width:1280px;margin:auto;min-width:0}h1,h2,h3{font-family:"Noto Serif SC",serif;line-height:1.45}h1{font-size:32px}h2{margin-top:52px;border-top:1px solid var(--line);padding-top:22px}h3{margin-top:28px}a{color:var(--accent)}p{max-width:100ch}.muted,figcaption{color:var(--muted);font-size:14px}.notice{border-left:4px solid var(--cell2);padding:8px 18px;background:var(--surface)}nav{display:flex;gap:20px;flex-wrap:wrap;position:sticky;top:env(safe-area-inset-top,0px);background:var(--bg);padding:12px 0;z-index:2;border-bottom:1px solid var(--line)}nav a[aria-selected=true]{font-weight:700;border-bottom:3px solid var(--accent)}.legend{display:flex;flex-wrap:wrap;gap:8px 20px}.swatch{display:inline-block;width:14px;height:14px;border:1px solid var(--muted);margin-right:7px;vertical-align:middle;background:var(--swatch)}figure{margin:16px 0;min-width:0}img{max-width:100%;height:auto;display:block}figure a{display:block}.steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.steps figure:first-child{grid-column:1/-1}.gallery{display:grid;grid-template-columns:minmax(0,1fr);gap:30px}.gallery article{border-top:1px solid var(--line);padding-top:16px;min-width:0}.metrics{font-variant-numeric:tabular-nums}.scroll{max-width:100%;overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}td,th{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left}th{background:var(--surface)}code{font-family:"JetBrains Mono",monospace;font-size:.88em}details{margin:12px 0}summary{cursor:pointer;color:var(--accent)}.charts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}.chart svg{width:100%;height:auto;color:var(--fg)}.chart rect{fill:var(--cell2)}.chart.control rect{fill:var(--cell1)}.chart line{stroke:var(--line)}svg text{fill:currentColor;font:12px system-ui,sans-serif}.flow{width:100%;max-width:100%;height:auto;color:var(--fg)}.flow rect{fill:var(--surface);stroke:var(--line)}.flow path{stroke:var(--accent);fill:none;stroke-width:2}.flow text{font-size:14px}.tab[hidden]{display:none}button{color:var(--fg);background:var(--surface);border:1px solid var(--line);padding:4px 10px;cursor:pointer} @media(max-width:650px){h1{font-size:26px}.steps,.charts{grid-template-columns:minmax(0,1fr)}.steps figure:first-child{grid-column:auto}td,th{padding:7px}nav{gap:14px}.flow text{font-size:16px}}
.panels{display:grid;grid-template-columns:repeat(var(--panels),minmax(0,1fr));gap:4px}.panel{display:block;aspect-ratio:240/266;min-width:0;overflow:hidden}.panel img{width:100%;height:100%;object-fit:cover;max-height:none}.flow-mobile{display:none}@media(max-width:650px){.panels{grid-template-columns:repeat(2,minmax(0,1fr))}.flow-wide{display:none}.flow-mobile{display:block}}
'''


def read(path): return json.loads(Path(path).read_text())

def fmt(value):
    return '无检测结果（距离无穷）' if value is None else f'{value:.4g}'


def figure(path, caption):
    panels = 6 if '-raw.jpg' in path else 5 if '-unit-' in path or '-target-' in path else (3 if '/second/' in path else 2) if '-step1.jpg' in path else 1
    if panels > 1:
        pictures = '<span class="panels" style="--panels:'+str(panels)+'">'+''.join(
            f'<span class="panel"><img loading="lazy" src="{esc(path)}" alt="{esc(caption)}，面板 {i+1}" style="object-position:{i/(panels-1)*100:.4f}% 50%"></span>' for i in range(panels))+'</span>'
    else:
        pictures = f'<img loading="lazy" src="{esc(path)}" alt="{esc(caption)}">'
    return f'<figure><a href="{esc(path)}" target="_blank" rel="noopener">{pictures}</a><figcaption>{esc(caption)} · 点击图片查看原尺寸</figcaption></figure>'


def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join(f'<th>{esc(str(x))}</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{esc(str(v))}</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def feature_meaning(name):
    fixed={'det_mean':'单元内部检测器概率的平均值','det_max':'单元内部最高概率','det_min':'单元内部最低概率',
           'det_p90':'单元内部概率的 90% 分位数','log_area':'单元面积的自然对数','source_count':'单元来自几种候选来源'}
    if name in fixed: return fixed[name]
    if name.startswith('source_'):
        source=name.removeprefix('source_')
        return f'是否含 {source} 来源（1 是、0 否）'+('；本池该位恒为 0' if source in ('C1','C2','pred') else '')
    if name.startswith('combination_'): return '来源是否恰好为 '+name.removeprefix('combination_')+'（1 是、0 否）'
    raise ValueError('未解释的 V1 特征：'+name)


def flow_svg():
    items=[('原始数据','两期图像、真值与概率'),('候选来源','汇集已保存的变化区域'),('合并去重','跨来源直接配对取并集'),
           ('U5 拆分','用 SAM 分区拆大对象'),('分流','r < 0.1 进入补漏组'),('新增像素','去掉已有检测结果'),
           ('标签','按新增像素真值占比'),('特征','提取概率、面积和来源'),('判断器','读取 M1-ign3 已存分数')]
    body=[]
    for i,(title,desc) in enumerate(items):
        x=(i%3)*320+8; y=(i//3)*102+8
        body.append(f'<rect x="{x}" y="{y}" width="290" height="80"/><text x="{x+14}" y="{y+28}">{i+1} · {title}</text><text x="{x+14}" y="{y+57}">{esc(desc)}</text>')
        if i%3<2: body.append(f'<path d="M{x+292},{y+40}h22m-7,-5 7,5 -7,5"/>')
    wide = '<svg class="flow flow-wide" role="img" aria-label="原始数据到判断器的九步流程，按编号逐行阅读" viewBox="0 0 960 310">'+''.join(body)+'</svg>'
    mobile = []
    for i, (title, desc) in enumerate(items):
        y = i*102+8
        mobile.append(f'<rect x="8" y="{y}" width="304" height="80"/><text x="22" y="{y+28}">{i+1} · {title}</text><text x="22" y="{y+57}">{esc(desc)}</text>')
        if i < 8:
            mobile.append(f'<path d="M160,{y+82}v16m-5,-6 5,6 5,-6"/>')
    return wide+'<svg class="flow flow-mobile" role="img" aria-label="九步流程，按编号向下阅读" viewBox="0 0 320 922">'+''.join(mobile)+'</svg>'


def chart(hist,title,control=False):
    counts=hist['counts']; edges=hist['edges']; maximum=max(counts,default=0) or 1
    bars=''.join(f'<rect x="{30+i*23}" y="{135-v/maximum*105:.2f}" width="19" height="{v/maximum*105:.2f}"><title>{edges[i]:.4g}–{edges[i+1]:.4g}：{v} 个</title></rect>' for i,v in enumerate(counts))
    svg=f'<svg viewBox="0 0 330 175" role="img" aria-label="{esc(title)}"><line x1="30" y1="135" x2="310" y2="135"/><text x="4" y="26">{maximum}</text><text x="10" y="135">0</text>{bars}<text x="30" y="155">{edges[0]:.3g}</text><text x="262" y="155">{edges[-1]:.3g}</text></svg>'
    bins=table(['区间','目标数'],[(f'{edges[i]:.4g}–{edges[i+1]:.4g}',v) for i,v in enumerate(counts)])
    return f'<div class="chart {"control" if control else ""}"><h4>{esc(title)}</h4>{svg}<p class="muted">有限值 {sum(counts)} 个；无检测结果 {hist["missing"]} 个。纵轴：目标数。</p><details><summary>分箱明细</summary>{bins}</details></div>'


def dataset_page(ds,out):
    r=read(out/ds/'rendered.json'); s=read(out/ds/'samples.json'); summary=read(out/ds/'summary.json')
    result=[f'<section class="tab" id="{ds}" aria-label="{ds.upper()}"><h2>1 · 原始数据 / {ds.upper()}</h2>']
    result += [f'<p class="notice">'+('小冒烟：统计仅含抽中的 '+str(summary['image_count'])+' 张训练图，画廊每类 2 个，不能代表全训练分布。' if s['partial'] else f'全训练划分：{summary["image_count"]:,} 张图；流程图 6 张、单元每类 24 个、②目标 30 个。')+'</p>',
               '<p>T1 / T2 是变化前后的图像；GT 是人工标注的变化真值；D 是概率严格大于 0.5 的检测结果。每张图按原尺寸完整取景，各步骤使用同一范围。热力图由紫到黄表示概率 0 到 1。</p>',
               '<p>从左到右：T1、T2、GT 黑白掩码、D 黑白掩码、概率热力图、①②③⑤叠加图。最后一幅<strong>用真值定义，只用于评估</strong>。</p>']
    for row in r['flow']: result.append('<h3>'+esc(row['image'])+'</h3>'+figure(row['raw'],'原始数据六面板'))
    result += ['<h2>2 · 候选如何成为补漏训练单元</h2>',
               '<p>候选是来源方法提出的可能变化区域；单元是候选合并、拆分后的独立判断对象。r = 候选与检测结果重叠的像素比例。冻结规则在 U5 上使用单元自身的 r；r &lt; 0.1 进入补漏组，其余进入精修组。</p>',
               '<p>'+ '；'.join(f'<strong>{k}</strong>：{v}' for k,v in SOURCE_TEXT.items() if k != 'H1' or ds=='second')+'。这里只读取已有候选缓存。</p>',
               '<p>D1：只在不同来源的原始对象间配对，交并比 ≥0.5 或交集/较小对象面积 ≥0.8；按交并比降序，每个对象最多配对一次，取并集。U5_x4：超过冻结面积门槛的对象用已保存的 SAM 分区拆分，其余保留完整；x4 表示冻结几何尺度的四倍。拆分沿用上游规则，跳过编号0的分区与小于10像素的碎片；这是候选构造阶段的规则。</p>',
               '<p>标签的 GT 占比以<strong>新增像素</strong>为分母：≥50% 为正例，&lt;10% 为负例，其余为中间。正例中新增真值像素最多落在③的单元，在 M1-ign3 训练时忽略。“为主”取①②③⑤像素数最大者，并列沿用该顺序。</p>']
    for row in r['flow']:
        result.append('<h3>'+esc(row['image'])+'</h3><div class="steps">')
        for i,step in enumerate(row['steps'],1): result.append(figure(step['image'],f'第 {i} 步 · '+step['description']))
        result.append('</div><details><summary>逐单元编号、r、分流和标签占比（图上重叠文字可在此复查）</summary>')
        result.append(table(['编号','r','去向','新增像素','新增 GT 占比','标签'],[(t['id'],fmt(t['r']),t['group'],t['novel'],fmt(t['precision']) if t['precision'] is not None else '—',LABELS.get(t['label'],'—')) for t in row['table']]))
        result.append('</details><details><summary>各来源候选数量和像素并集</summary>'+table(['来源','候选个数','像素并集'],[(k,v['count'],v['pixels']) for k,v in row['sources'].items()])+'</details>')
    result += ['<p class="muted">第 7 步以全部 U5 单元判断“完全没有候选覆盖”，绿色覆盖来自补漏组全部正例（含③为主正例）。已有报告中补漏组对②的像素覆盖约为 52–59%，本页逐图显示实际覆盖情况。</p>',
               '<h2>3 · 最终训练数据长什么样</h2>',
               '<p class="notice">图像近景目前没有输入判断器；V1 只用元数据，即概率、面积和来源这些数值。之前的 SAM / DINOv3 / RemoteCLIP 特征、CNN、千问 V2 方案都没有超过元数据，见 exp6b_verifier.md、exp6f_v1_variants.md。M1-ign3 分数是<strong>训练集内分数，偏乐观</strong>；③为主正例未参加该变体的监督损失。</p>',
               f'<p>{ds.upper()} 使用 {len(r["features"])} 维 V1 特征。概率统计来自整个单元，标签精度来自新增像素，二者范围不同。C1、C2 是历史提示候选来源位，pred 是检测结果来源位，本池这三位恒为 0。</p>',
               table(['特征名','白话含义'],[(n,feature_meaning(n)) for n in r['features']]),
               '<p>每张横条从左到右为 T1 近景、T2 近景（红色单元轮廓）、T1 上下文、T2 上下文、概率近景。上下文约为近景三倍范围，遇到图像边缘裁回图内；蓝色半透明区域是 D。</p>',
               f'<p>难负例从 {s["negative_count"]:,} 个负例中检测器均值最高的前 10%（{s["hard_pool_count"]:,} 个，最低均值 {s["hard_mean_min"]:.4g}）内随机抽样。随机负例从全部负例抽样，允许与难负例重复。</p>']
    for group,title in GROUPS.items():
        result.append(f'<h3>{title}</h3><div class="gallery">')
        for u in r['units'][group]:
            label='③为主正例（忽略）' if u['label']==1 and u['majority']==3 else '正例' if u['label']==1 else '负例'
            result += ['<article><strong>'+esc(f'{u["image"]} · 单元 {u["id"]}')+'</strong>',figure(u['image_path'],'单元五面板'),
                       f'<p class="metrics">检测器均值 {u["features"]["det_mean"]:.4f} · 最大值 {u["features"]["det_max"]:.4f} · 面积 {u["area"]} 像素 · 新增 {u["novel"]} 像素 · 来源 {esc(" + ".join(u["sources"]))} · r {u["r"]:.4f} · 新增 GT 占比 {u["precision"]:.1%} · {label} · M1-ign3 {u["score"]:.4f}</p>',
                       '<details><summary>全部 V1 特征</summary>'+table(['特征','值'],[(k,fmt(v)) for k,v in u['features'].items()])+'</details></article>']
        result.append('</div>')
    result += ['<h2>4 · ②漏检是否孤立</h2>',
               '<p>先在每个 GT 目标内部，把 GT 中未被 D 覆盖的像素按八邻接拆成连通域；SECOND 按语义变化类型区分 GT 目标。②画廊按这些漏检连通域随机抽取，不按候选覆盖情况筛选。</p>',
               '<p>①与②都来自检测器覆盖不足 10% 的 GT 目标；①经 3×3 膨胀一像素会接触 D（包括对角线），②不会。③来自覆盖至少 10% 的目标；GT 目标或漏检连通域小于 20 像素时优先归⑤。</p>',
               '<p>距离取目标内任一漏检像素到最近 D 像素的最小值。主图采用棋盘距离（横、竖、斜移一步均计 1），因此①距离为 1、②大于 1；逐目标 JSON 另保存欧氏距离，对角相邻时为 √2。图内完全没有 D 时距离记为空值，单独计数。距离较近的②也可能只隔一条窄缝。</p>',
               f'<p>本次统计① {summary["target_count"]["1"]:,} 个、② {summary["target_count"]["2"]:,} 个。覆盖比例是漏检目标被单元掩码并集覆盖的像素比例；补漏和精修单元可重叠，比例不能直接相加。</p><div class="charts">']
    titles={'distance':'② 到 D 的距离 / 像素','area':'② 面积 / 像素','mean_probability':'② 内部平均概率','max_probability':'② 内部最大概率','fill_coverage':'② 补漏组覆盖比例','refine_coverage':'② 精修组覆盖比例','candidate_coverage':'② 全部 U5 覆盖比例'}
    for key,title in titles.items(): result.append(chart(summary['bins']['2'][key],title))
    result += [chart(summary['bins']['1']['distance'],'① 距离对照 / 像素',True),'</div>',
               f'<p><a href="{ds}/targets.json">下载逐目标统计 JSON</a> · <a href="{ds}/summary.json">下载分箱与统计范围</a></p>',
               '<h3>随机②目标</h3><p>横条依次为 T1、T2 近景、T1、T2 上下文及概率图；白色轮廓为完整 GT 目标，亮粉色轮廓为本张卡片的②漏检连通域，蓝色填充为周围 D。</p><div class="gallery">']
    for t in r['targets']:
        sources='、'.join(f'{k} {v:.1%}' for k,v in t['source_coverage'].items() if v) or '无'
        fill_sources='、'.join(f'{k} {v:.1%}' for k,v in t['fill_source_coverage'].items() if v) or '无'
        result += ['<article><strong>'+esc(f'{t["image"]} · GT 目标 {t["target"]} / 漏检连通域 {t["id"]}')+'</strong>',figure(t['image_path'],'②目标五面板'),
                   f'<p class="metrics">距离 {fmt(t["distance"])} · 欧氏距离 {fmt(t["distance_euclidean"])} · 面积 {t["area"]} 像素 · 平均概率 {t["mean_probability"]:.4f} · 最大概率 {t["max_probability"]:.4f} · 补漏覆盖 {"是" if t["fill_coverage"] else "否"}（{t["fill_coverage"]:.1%}）· 精修覆盖 {t["refine_coverage"]:.1%} · 全部来源覆盖 {sources} · 补漏来源覆盖 {fill_sources}</p></article>']
    result += ['</div><details><summary>抽样与复查</summary>',f'<p>种子 {s["seed"]}。{esc(s["sampling"])} 流程示例中含① {s["flow_conditions"]["has1"]} 张、含② {s["flow_conditions"]["has2"]} 张、补漏组有正例 {s["flow_conditions"]["positive"]} 张。</p>',
               f'<p><a href="{ds}/samples.json">本数据集抽样清单</a></p></details></section>']
    return ''.join(result)


def build(out):
    from exp8s4_render import write, HERE
    out=Path(out).resolve()
    if not out.is_relative_to(HERE): raise ValueError('输出必须在 batch44 内')
    datasets=('second','sysu'); samples={ds:read(out/ds/'samples.json') for ds in datasets}
    if samples['second']['partial']!=samples['sysu']['partial']: raise ValueError('不能混合完整页与冒烟页')
    write(out/'samples.json',dict(seed=20261025,datasets=samples))
    legend=[('gt','GT 轮廓'),('detector','D 检测结果'),('B','B 来源'),('G','G0.05 来源'),('H','H1 来源'),
            ('fill','补漏 / 正例'),('refine','精修'),('negative','负例'),('middle','中间'),('ignore','③为主（忽略）'),
            ('cell1','① 无信号·邻接'),('cell2','② 无信号·孤立'),('cell3','③ 有信号'),('cell5','⑤ 小目标 / 小残余'),('unit','单元红线')]
    content=['<title>补漏训练数据审核</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n<style>'+CSS+'</style>',
             '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
             '<main lang="zh-CN"><h1>补漏训练数据审核</h1><p>实验 8 · 补充 4 · 训练数据人工审核</p>',
             '<nav aria-label="切换数据集"><a href="#second" role="tab">SECOND</a><a href="#sysu" role="tab">SYSU</a><button type="button" id="theme">切换明暗</button><a href="samples.json">抽样清单</a></nav>',
             '<h2>0 · 一屏读懂</h2><p>“我始终相信 Claude 能把补漏这件事处理好：以尽可能低的 F1 代价，换取较高的第二类漏检召回。”—— Ethan</p>',
             '<p>本页帮助检查两个问题：②漏检离已有检测结果有多远，以及正例和难负例在图像上是否看得出区别。F1 同时衡量误报与漏报；召回表示找回了多少真实变化。流程按编号逐行阅读。</p>',flow_svg(),
             '<div class="legend">'+''.join(f'<span><i class="swatch" style="--swatch:var(--{k})"></i>{v}</span>' for k,v in legend)+'</div>',
             '<p class="muted">颜色全页统一；U5 拆分图中的多种颜色只用于区分单元，不表示类别。白色 GT 轮廓叠加在原图上，图例带描边。图像保留原始颜色，不随页面明暗主题变化。</p>']
    content.extend(dataset_page(ds,out) for ds in datasets)
    content += ['<section><h2>5 · 你看到的情况</h2><!-- OBSERVATIONS --><p class="muted">待 Claude 看完样本后填写观察。</p></section>',
                '<footer><p class="muted">只读缓存；没有运行模型或训练。所有分数均从 batch43 的 M1-ign3/train.npz 读取。</p></footer></main>',
                '''<script>(()=>{function show(){const ds=location.hash==='#sysu'?'sysu':'second';document.querySelectorAll('.tab').forEach(el=>el.hidden=el.id!==ds);document.querySelectorAll('nav [role=tab]').forEach(el=>el.setAttribute('aria-selected',el.hash==='#'+ds));}addEventListener('hashchange',show);show();document.getElementById('theme').onclick=()=>{const root=document.documentElement;const dark=root.dataset.theme==='dark'||(!root.dataset.theme&&matchMedia('(prefers-color-scheme:dark)').matches);root.dataset.theme=dark?'light':'dark';};})();</script>''']
    (out/'index.html').write_text('\n'.join(content))
    images=list((out/'images').rglob('*.jpg'))
    total=sum(p.stat().st_size for p in images)
    # 优先保留 80 质量；只在整页超过预算时统一压到 75，避免静默产出超额页面。
    quality=80
    if total>14_000_000:
        quality=75
        for p in images:
            with Image.open(p) as im: im.save(p,quality=quality,optimize=True)
        total=sum(p.stat().st_size for p in images)
    if len(images)>400 or total>14_000_000 or total+(out/'index.html').stat().st_size>16_000_000:
        raise ValueError(f'图片预算超限：{len(images)} 张，{total} 字节；需要调整面板或发布方案')
    write(out/'delivery.json',dict(image_count=len(images),image_bytes=total,jpeg_quality=quality,
                                 html_bytes=(out/'index.html').stat().st_size,partial=samples['second']['partial']))
    print(f'页面：{out / "index.html"}；JPEG {len(images)} 张 / {total/1024/1024:.2f} MiB',flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);build(p.parse_args().out)
