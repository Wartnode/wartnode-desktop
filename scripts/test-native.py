"""Run upstream math and deterministic native shutdown regressions on Windows."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wartnode.processes import hidden_options
cases=[('custom_float',False),('wartnode-conman-before',True),('wartnode-conman-shutdown',False)]
results=[]
for number,(name,negative) in enumerate(cases):
    with tempfile.TemporaryDirectory(prefix='native-链-',dir=ROOT/'work') as temporary:
        directory=Path(temporary)
        (directory/'runtime').mkdir()
        (directory/'config.toml').write_text('')
        environment=os.environ.copy()
        environment['LOCALAPPDATA']=str(directory/'runtime')
        args=[] if name=='custom_float' else ['--isolated','--rpc=127.0.0.1:0','--bind=127.0.0.1:0',
             '--chain-db=chain.db3','--peers-db=peers.db3','--config=config.toml']
        began=time.monotonic()
        completed=subprocess.run([str(ROOT/'work/upstream-node-build/tests'/(name+'.exe')),*args],cwd=directory,env=environment,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180,**hidden_options())
        output=completed.stdout
        passed=(completed.returncode!=0 and b'UV_HANDLE_CLOSING' in output) if negative else completed.returncode==0
        if name=='wartnode-conman-shutdown':passed=passed and b'8000 late callbacks rejected' in output
        result={'test':name,'exit_code':completed.returncode,'expected_failure':negative,'passed':passed,
                'seconds':round(time.monotonic()-began,3),'unicode_profile':True}
        (ROOT/'evidence'/f'native-{number:02d}-{name}.log').write_bytes(output)
        results.append(result)
        print(json.dumps(result),flush=True)
(ROOT/'evidence/native-tests.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
raise SystemExit(not all(r['passed'] for r in results))
