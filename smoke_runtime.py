"""Offline smoke test of the exact executable, using a disposable data directory."""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

def main():
    command=sys.argv[1:]
    if not command: raise SystemExit('Usage: smoke_runtime.py <exe or python> [main.py]')
    with tempfile.TemporaryDirectory(prefix='relayforge-smoke-') as root:
        root=Path(root);data=root/'data'
        with socket.socket() as s: s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (root/'runtime.log').open('wb') as log:
            proc=subprocess.Popen(command+['--no-browser','--data-dir',str(data),'--port',str(port)],stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    if proc.poll() is not None: raise RuntimeError('Process exited early')
                    try:
                        with opener.open(base+'/api/health',timeout=2) as r: health=json.load(r)
                        if health.get('version')=='4.3.4':break
                    except (OSError,ValueError):time.sleep(.4)
                else: raise RuntimeError('HTTP server did not start')
                for path,needle in [('/',b'RELAYFORGE'),('/assets/main.js',b'initLab'),('/assets/main.css',b'engine-control')]:
                    with opener.open(base+path,timeout=4) as r: assert r.status==200 and needle in r.read()
                try: opener.open(base+'/api/status',timeout=4)
                except urllib.error.HTTPError as ex: assert ex.code==401;ex.close()
                else: raise AssertionError('unauthorized status endpoint')
                key=(data/'dashboard-key.txt').read_text().strip()
                req=urllib.request.Request(base+'/api/status',headers={'X-Studio-Key':key})
                with opener.open(req,timeout=4) as r: status=json.load(r)
                assert not status['engine_running'] and len(status['targets'])==5 and status['version']=='4.3.4'
                h={'X-Studio-Key':key,'Content-Type':'application/json','Origin':base}
                req=urllib.request.Request(base+'/api/richtext/preview',headers=h,data=json.dumps({'platform':'bale','text':'sample text','entities':[{'type':'bold','start':0,'end':6}]}).encode())
                with opener.open(req,timeout=5) as r: assert json.load(r)['text']=='*sample* text'
                req=urllib.request.Request(base+'/api/richtext/preview',headers=h,data=json.dumps({'platform':'eitaa','text':'sample text','entities':[{'type':'bold','start':0,'end':6}]}).encode())
                with opener.open(req,timeout=5) as r:
                    preview=json.load(r);assert preview['text']=='<b>sample</b> text' and preview['options']=={'parse_mode':'html'}
                req=urllib.request.Request(base+'/api/ai-lab/preview',headers=h,data=json.dumps({'text':'sample content','profile':{'template':'{body}'}}).encode())
                with opener.open(req,timeout=5) as r:
                    draft=json.load(r);assert draft['published'] is False and draft['output']=='sample content'
                print('HTTP SMOKE OK 4.3.4 (no real provider sends; rich text + local AI lab)')
            finally:
                if proc.poll() is None:
                    if os.name=='nt': subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=20)
                    else:proc.terminate()
                    try:proc.wait(20)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
        print((root/'runtime.log').read_text(errors='replace')[-2000:])
if __name__=='__main__':main()
