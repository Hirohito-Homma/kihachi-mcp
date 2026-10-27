"""Local KIHACHI studio. Existing engines, isolated song outputs."""
import json, os, secrets, subprocess, threading, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
ENGINE = Path(os.environ.get('KIHACHI_ENGINE_DIR', Path.home() / 'MusicAI' / 'kihachi-music-ai')).expanduser()
LIVE = Path(os.environ.get('ABLETONGPT_DIR', Path.home() / 'MusicAI' / 'abletongpt')).expanduser()
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()

def run(args, root=ENGINE, timeout=180):
    env = dict(os.environ, PYTHONPATH=str(root / 'src'), PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([str(root / '.venv/bin/python'), *args], env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise ValueError((p.stderr or p.stdout)[-4000:])
    return p.stdout

def cli(*args):
    return run(['-c', 'from kihachi_music_ai.cli import main; import sys; sys.exit(main())', *map(str,args)])

def live_state():
    return json.loads(run(['-c', 'from abletongpt.bridge import AbletonBridge; import json; print(json.dumps(AbletonBridge().call("get_state")))'], LIVE, 15))

def baseline(state):
    return {'tracks': state['tracks'], 'tempo': state['tempo'], 'signature': state['signature']}

def project(key):
    if not isinstance(key,str) or len(key)!=32 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('曲のIDが不正です')
    p = ROOT/'songs'/key
    if not p.is_dir(): raise ValueError('曲が見つかりません')
    return p

def action(route, data):
    if route == '/api/compose':
        prompt = data.get('prompt','')
        if not isinstance(prompt,str) or not 1 <= len(prompt.strip()) <= 2000: raise ValueError('曲の指定を1〜2000文字で入力してください')
        seed = int(data.get('seed',8))
        key = uuid.uuid4().hex
        p = ROOT/'songs'/key
        cli('compose',prompt,'--output',p,'--seed',seed)
        spec=json.loads((p/'song_spec.json').read_text())
        return {'id':key,'spec':spec,'files':[x.name for x in p.iterdir() if x.is_file()]}
    p=project(data.get('id'))
    if route == '/api/prepare':
        if (p/'attempt.json').exists(): raise ValueError('この曲は追加実行済みです。二重追加を防ぐため、Liveと記録を確認してください')
        state=live_state()
        cli('ableton-plan',p,'--first-track-index',len(state['tracks']),'--overwrite')
        run(['-m','abletongpt.cli.jobs','import-kihachi','--arrangement-plan',str(p/'arrangement_plan.json'),'--out',str(p/'job.json')],LIVE)
        (p/'baseline.json').write_text(json.dumps(baseline(state)))
        return {'plan':json.loads((p/'arrangement_plan.json').read_text()),'current_tempo':state['tempo']}
    if route == '/api/apply':
        if not (p/'baseline.json').exists(): raise ValueError('先に追加内容を確認してください')
        state=live_state()
        if state['is_playing']: raise ValueError('Liveを停止してから追加してください')
        if baseline(state)!=json.loads((p/'baseline.json').read_text()): raise ValueError('Liveの状態が変わりました。追加内容を再確認してください')
        with (p/'attempt.json').open('x') as f: json.dump({'status':'started'},f)
        try:
            log=run(['-m','abletongpt.cli.jobs','run','--plan',str(p/'job.json')],LIVE,600)
            (p/'execution.txt').write_text(log)
            status=json.loads(run(['-m','abletongpt.cli.jobs','status','--plan',str(p/'job.json'),'--json'],LIVE))
            return {'log':log,'status':status,'live':live_state()}
        except Exception as e:
            (p/'execution.txt').write_text(str(e))
            raise ValueError('追加が途中で停止しました。自動再実行はしません。記録: '+str(p/'execution.txt')+'\n'+str(e))
    raise ValueError('操作が見つかりません')

class Handler(BaseHTTPRequestHandler):
    def send(self,status,body,kind='application/json; charset=utf-8'):
        raw=body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.headers.get('Host') != '127.0.0.1:8421': return self.send(403,{'error':'Host rejected'})
        if self.path=='/': return self.send(200,(ROOT/'index.html').read_text().replace('__TOKEN__',TOKEN).encode(),'text/html; charset=utf-8')
        if self.path=='/api/status':
            try: return self.send(200,{'connected':True,'live':live_state()})
            except Exception as e: return self.send(200,{'connected':False,'error':str(e)})
        if self.path.startswith('/files/'):
            try:
                _,_,key,name=self.path.split('/')
                p=project(key)/name
                if name not in {'song_spec.json','prompt.json','lyrics.txt','drums.mid','bass.mid','chords.mid','melody.mid','arrangement_plan.json'}: raise ValueError()
                return self.send(200,p.read_bytes(),'application/octet-stream')
            except Exception: pass
        self.send(404,{'error':'Not found'})
    def do_POST(self):
        if self.headers.get('Host')!='127.0.0.1:8421' or self.headers.get('X-Studio-Token')!=TOKEN: return self.send(403,{'error':'アクセスが拒否されました'})
        if not LOCK.acquire(blocking=False): return self.send(409,{'error':'処理中です'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<16000: raise ValueError('入力サイズが不正です')
            data=json.loads(self.rfile.read(n))
            if not isinstance(data,dict): raise ValueError('入力形式が不正です')
            self.send(200,action(self.path,data))
        except Exception as e: self.send(400,{'error':str(e)})
        finally: LOCK.release()

if __name__=='__main__':
    print('KIHACHI Studio: http://127.0.0.1:8421',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8421),Handler).serve_forever()
