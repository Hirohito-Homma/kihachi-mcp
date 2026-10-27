"""Local KIHACHI studio. Existing engines, isolated song outputs."""
import json, os, secrets, subprocess, threading, uuid
from urllib.parse import urlsplit, parse_qs
import google_music as google
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
ENGINE = Path(os.environ.get('KIHACHI_ENGINE_DIR', Path.home() / 'MusicAI' / 'kihachi-music-ai')).expanduser()
LIVE = Path(os.environ.get('ABLETONGPT_DIR', Path.home() / 'MusicAI' / 'abletongpt')).expanduser()
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
API_KEY = os.environ.get('GEMINI_API_KEY', '')

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
    global API_KEY
    if route == '/api/key':
        key=data.get('key','')
        if not isinstance(key,str) or len(key)>256 or any(ord(c)<33 or ord(c)>126 for c in key): raise ValueError('APIキーの形式が不正です')
        API_KEY=key
        return {'configured':bool(API_KEY)}
    if route == '/api/google/project':
        prompt=google.build_prompt(data)
        key=uuid.uuid4().hex
        p=ROOT/'songs'/key
        p.mkdir(parents=True)
        google.write_json(p/'google_project.json',{'id':key,'created_at':google.stamp(),'prompt':prompt,'brief':data,'provider':'google_music'})
        return {'id':key,'prompt':prompt,'takes':[]}
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
    if route == '/api/google/generate':
        if data.get('consent') is not True: raise ValueError('Googleへの送信とAPI利用を確認してください')
        if (p/'attempt.json').exists(): raise ValueError('追加済みの曲です。新しい曲を作成してください')
        project_data=json.loads((p/'google_project.json').read_text())
        saved=google.generate(p,project_data['prompt'],data.get('model'),API_KEY)
        return {'take':saved}
    if route == '/api/google/adopt':
        if (p/'attempt.json').exists(): raise ValueError('Live追加済みの曲は変更できません')
        manifest,path=google.take(p,data.get('take_id'))
        google.write_json(p/'adopted.json',{'take_id':manifest['id'],'sha256':manifest['sha256']})
        if (p/'baseline.json').exists(): (p/'baseline.json').unlink()
        return {'adopted':manifest['id']}
    if route == '/api/prepare':
        if (p/'attempt.json').exists(): raise ValueError('この曲は追加実行済みです。二重追加を防ぐため、Liveと記録を確認してください')
        state=live_state()
        if (p/'google_project.json').exists():
            if not (p/'adopted.json').exists(): raise ValueError('試聴して音源を採用してください')
            adopted=json.loads((p/'adopted.json').read_text())
            manifest,path=google.take(p,adopted['take_id'])
            if manifest['sha256']!=adopted['sha256']: raise ValueError('採用した音源が変わっています')
            google.write_json(p/'arrangement_plan.json',google.audio_plan(p,manifest,path,state))
        else:
            cli('ableton-plan',p,'--first-track-index',len(state['tracks']),'--overwrite')
        run(['-m','abletongpt.cli.jobs','import-kihachi','--arrangement-plan',str(p/'arrangement_plan.json'),'--out',str(p/'job.json')],LIVE)
        (p/'baseline.json').write_text(json.dumps(baseline(state)))
        return {'plan':json.loads((p/'arrangement_plan.json').read_text()),'current_tempo':state['tempo']}
    if route == '/api/apply':
        if not (p/'baseline.json').exists(): raise ValueError('先に追加内容を確認してください')
        state=live_state()
        if state['is_playing']: raise ValueError('Liveを停止してから追加してください')
        if baseline(state)!=json.loads((p/'baseline.json').read_text()): raise ValueError('Liveの状態が変わりました。追加内容を再確認してください')
        if (p/'google_project.json').exists():
            plan=json.loads((p/'arrangement_plan.json').read_text())
            manifest,path=google.take(p,plan['source_take'])
            if manifest['sha256']!=plan['source_sha256']: raise ValueError('追加計画の音源が変わっています')
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
        if self.path=='/app.js':
            return self.send(200,(ROOT/'app.js').read_text().replace('__TOKEN__',TOKEN).encode(),'text/javascript; charset=utf-8')
        if self.path=='/api/providers':
            return self.send(200,{'api_configured':bool(API_KEY),'models':google.MODELS,'flow_url':google.FLOW_URL})
        if self.path=='/api/projects':
            rows=[]
            for path in sorted((ROOT/'songs').glob('*/google_project.json'),key=lambda x:x.stat().st_mtime,reverse=True):
                item=json.loads(path.read_text())
                rows.append({'id':item['id'],'title':item['brief']['prompt'][:80]})
            return self.send(200,{'projects':rows})
        if self.path.startswith('/api/project/'):
            try:
                p=project(self.path.split('/')[-1]); item=json.loads((p/'google_project.json').read_text())
                item['takes']=[json.loads(x.read_text()) for x in sorted((p/'takes').glob('*/take.json'))]
                item['adopted']=json.loads((p/'adopted.json').read_text()) if (p/'adopted.json').exists() else None
                item['attempted']=(p/'attempt.json').exists()
                return self.send(200,item)
            except Exception: return self.send(404,{'error':'曲を読み込めません'})
        if self.path.startswith('/audio/'):
            try:
                _,_,key,take_id=self.path.split('/')
                manifest,path=google.take(project(key),take_id)
                mime={'.wav':'audio/wav','.mp3':'audio/mpeg','.flac':'audio/flac','.aif':'audio/aiff','.aiff':'audio/aiff'}[path.suffix]
                return self.send(200,path.read_bytes(),mime)
            except Exception: return self.send(404,{'error':'音源を読み込めません'})
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
            if self.path.startswith('/api/google/upload?'):
                if not 0<n<=google.MAX_AUDIO: raise ValueError('音源は100MB以下にしてください')
                query=parse_qs(urlsplit(self.path).query)
                p=project(query.get('id',[''])[0]); suffix=query.get('suffix',[''])[0].lower()
                if not (p/'google_project.json').exists(): raise ValueError('Google Musicの曲を作成してください')
                if (p/'attempt.json').exists(): raise ValueError('追加済みの曲は変更できません')
                raw=self.rfile.read(n)
                if len(raw)!=n: raise ValueError('音源の転送が完了しませんでした')
                saved=google.save_audio(p,raw,suffix,'flow_music_web',{'provenance':'user_uploaded_not_independently_verified'})
                return self.send(200,{'take':saved})
            if not 0<n<64000: raise ValueError('入力サイズが不正です')
            data=json.loads(self.rfile.read(n))
            if not isinstance(data,dict): raise ValueError('入力形式が不正です')
            self.send(200,action(self.path,data))
        except Exception as e: self.send(400,{'error':str(e)})
        finally: LOCK.release()

if __name__=='__main__':
    print('KIHACHI Studio: http://127.0.0.1:8421',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8421),Handler).serve_forever()
