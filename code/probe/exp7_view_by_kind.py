import json,sys,collections
for setting in ['O','V']:
    p=f'/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp7_batch16/val_{setting}/sam_regions.jsonl'
    acc=collections.defaultdict(list)
    for line in open(p):
        r=json.loads(line); m,pr,v=r['configuration'].split('/')
        acc[(m,r['kind'],v)].append(r['iou'])
    print('##',setting)
    for m in ['sam3','sam_vith']:
        for k in ['新建','拆除','改建','植被互变','地表与植被互变','水体']:
            row=[f"{v}:{100*sum(acc[(m,k,v)])/max(1,len(acc[(m,k,v)])):.1f}(n={len(acc[(m,k,v)])})" for v in ['t1','t2','best']]
            print(m,k,*row)
