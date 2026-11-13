#!/usr/bin/env python3
"""One-shot node/API preflight; never log proxy values or credentials."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--compute',action='store_true');a=p.parse_args()
proxies=[v for k,v in os.environ.items() if k.lower() in ('http_proxy','https_proxy','all_proxy') and v]
endpoint=os.environ.get('DASHSCOPE_BASE_URL','https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/')+'/models'
request=urllib.request.Request(endpoint,headers={'Authorization':'Bearer '+os.environ['DASHSCOPE_API_KEY']})
def probe(direct):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    try:
        with opener.open(request,timeout=10) as response:
            data=json.load(response)
            return dict(http_status=response.status,flash_listed=any(row.get('id')=='qwen3.7-flash' for row in data.get('data',[])))
    except urllib.error.HTTPError as error:return dict(http_status=error.code)
    except (OSError,ValueError):return dict(http_status=None,error='connection_or_response_failure')
result=dict(node=socket.gethostname(),proxy_present=bool(proxies),
    localhost_proxy=any(urllib.parse.urlparse(v).hostname in ('127.0.0.1','localhost','::1') for v in proxies),
    inherited=probe(False),direct=probe(True),network_mode='direct',cost_cny=0)
if a.compute:
    gpu=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader'],capture_output=True,text=True)
    result['gpu']=gpu.stdout.strip();result['gpu_visible']=gpu.returncode==0 and bool(gpu.stdout.strip())
    root=Path(__file__).resolve().parents[2]
    cfg=json.loads((root/'code/agent_baseline/vision_t13.example.json').read_text())
    paths=[str(root),'/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python',
           '/guisongxia01/pangchao/wangyihan/conda_envs/omniovcd-env/bin/python',
           '/guisongxia01/pangchao/wangyihan/models/sam3/sam3.pt']
    paths.extend(v for group in cfg.values() for v in group.values() if isinstance(v,str) and v.startswith('/'))
    result['missing_paths']=[path for path in paths if not Path(path).exists()]
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
if result['direct'].get('http_status')!=200 or (a.compute and (not result['gpu_visible'] or result['missing_paths'])):
    raise SystemExit('Preflight failed; inspect recorded status before any paid run')
