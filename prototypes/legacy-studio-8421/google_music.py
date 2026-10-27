"""Google music providers: explicit browser handoff or official Gemini REST.

API contract: https://ai.google.dev/gemini-api/docs/music-generation
Flow Music is a separate web product, not a Gemini API endpoint.
"""
import base64
import hashlib
import json
import math
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

FLOW_URL = 'https://www.flowmusic.app/'
API_URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
MODELS = ('lyria-3.5', 'lyria-3-clip-preview')
MAX_AUDIO = 100 * 1024 * 1024
SUFFIXES = {'.wav', '.mp3', '.flac', '.aif', '.aiff'}

def write_json(path, data):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)

def stamp():
    return datetime.now(timezone.utc).isoformat()

def text(value, label, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{label}は{limit}文字以内で入力してください')
    return value.strip()

def build_prompt(data):
    brief = text(data.get('prompt', ''), '曲の指定', 2000)
    if not brief: raise ValueError('曲の指定を入力してください')
    vocals = data.get('vocals', 'instrumental')
    if vocals not in ('instrumental', 'vocal'): raise ValueError('歌声の指定が不正です')
    lyrics = text(data.get('lyrics', ''), '歌詞', 8000)
    structure = text(data.get('structure', ''), '構成', 2000)
    duration = int(data.get('duration', 120))
    if not 15 <= duration <= 600: raise ValueError('希望の長さは15〜600秒で指定してください')
    lines = [brief, f'Target duration: approximately {duration} seconds.']
    lines.append('Instrumental only. No vocals, singing, or spoken words.' if vocals == 'instrumental' else 'Include vocals.')
    if structure: lines.extend(['Song structure:', structure])
    if vocals == 'vocal' and lyrics: lines.extend(['Use these lyrics:', lyrics])
    return '\n\n'.join(lines)

def probe(path):
    executable = shutil.which('ffprobe')
    if not executable: raise ValueError('音源検証にffprobeが必要です')
    p = subprocess.run([executable, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration,format_name:stream=codec_type,sample_rate,channels', '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if p.returncode: raise ValueError('音源を読み取れません。WAV/MP3/FLAC/AIFFを選択してください')
    info = json.loads(p.stdout)
    audio = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    formats = set(info.get('format', {}).get('format_name', '').split(','))
    if not formats.intersection({'wav','mp3','flac','aiff'}) or not audio:
        raise ValueError('対応する音声ファイルではありません')
    duration = float(info.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or not 0 < duration <= 1200:
        raise ValueError('音源の長さを検証できないか、20分を超えています')
    return {'duration_sec': duration, 'sample_rate': int(audio[0]['sample_rate']), 'channels': audio[0]['channels']}

def save_audio(project, raw, suffix, source, extra=None):
    if suffix not in SUFFIXES or not 0 < len(raw) <= MAX_AUDIO:
        raise ValueError('対応形式の100MB以下の音源が必要です')
    take_id = uuid.uuid4().hex
    folder = project / 'takes' / take_id
    folder.mkdir(parents=True)
    path = folder / ('audio' + suffix)
    path.write_bytes(raw)
    try:
        info = probe(path)
    except Exception:
        write_json(folder / 'validation.json', {'status':'rejected','created_at':stamp()})
        raise
    manifest = {'id':take_id,'file':path.name,'source':source,'created_at':stamp(),
                'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),**info,**(extra or {})}
    write_json(folder / 'take.json', manifest)
    return manifest

def take(project, key):
    if not isinstance(key,str) or len(key)!=32 or any(c not in '0123456789abcdef' for c in key): raise ValueError('テイクIDが不正です')
    folder = project/'takes'/key
    manifest=json.loads((folder/'take.json').read_text())
    name=manifest['file']
    if Path(name).name!=name or Path(name).suffix not in SUFFIXES: raise ValueError('音源パスが不正です')
    path=folder/name
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']: raise ValueError('音源が変更されています。再取込してください')
    return manifest,path

def parse_response(response):
    if response.get('status') not in (None,'completed'):
        raise ValueError('生成が完了していません。自動再送は行いません')
    audios=[]; lyrics=[]
    for step in response.get('steps',[]):
        if step.get('type') != 'model_output': continue
        for block in step.get('content',[]):
            if block.get('type')=='audio' and block.get('data'): audios.append(block)
            elif block.get('type')=='text': lyrics.append(block.get('text',''))
    if len(audios)!=1: raise ValueError('完全な音源1件を取得できませんでした。自動再送は行いません')
    block=audios[0]
    mime=block.get('mime_type','audio/mpeg')
    suffix={'audio/mpeg':'.mp3','audio/mp3':'.mp3','audio/wav':'.wav','audio/x-wav':'.wav'}.get(mime)
    if suffix is None: raise ValueError('未対応の音声形式です')
    try: raw=base64.b64decode(block['data'],validate=True)
    except Exception: raise ValueError('音声データが破損しています') from None
    return raw,suffix,'\n'.join(lyrics)

def generate(project, prompt, model, api_key):
    if model not in MODELS: raise ValueError('対応モデルを選択してください')
    if not api_key: raise ValueError('Gemini APIキーを設定してください。Flow Musicの契約とは別です')
    request_id=uuid.uuid4().hex
    path=project/('request-'+request_id+'.json')
    record={'provider':'gemini_api','model':model,'prompt':prompt,'created_at':stamp(),'status':'submitted'}
    write_json(path,record)
    body={'model':model,'input':prompt}
    request=Request(API_URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','x-goog-api-key':api_key},method='POST')
    try:
        with urlopen(request,timeout=240) as result:
            payload=result.read(145*1024*1024+1)
            if len(payload)>145*1024*1024: raise ValueError('API応答が大きすぎます')
            response=json.loads(payload)
        record['interaction_id']=response.get('id')
        raw,suffix,lyrics=parse_response(response)
        saved=save_audio(project,raw,suffix,'gemini_api',{'model':model,'interaction_id':response.get('id'),'request_id':request_id})
        (project/'takes'/saved['id']/'lyrics.txt').write_text(lyrics)
        record.update(status='completed',take_id=saved['id'])
        return saved
    except HTTPError as e:
        record['status']='failed';record['http_status']=e.code
        raise ValueError(f'Google APIエラー（HTTP {e.code}）。APIキー・利用権限・課金・割当量を確認してください。自動再送はしていません') from None
    except (URLError, TimeoutError):
        record['status']='unknown'
        raise ValueError('Google APIの応答を確認できません。課金済みの可能性があるため自動再送しません') from None
    except Exception:
        record['status']='failed_or_unverified'
        raise
    finally: write_json(path,record)

def audio_plan(project, manifest, path, state):
    index=len(state['tracks']); label='KIHACHI Google Music'
    return {'arrangement_plan_version':'0.1','execution_state':'planned_not_applied',
            'song':{'title':label,'bpm':state['tempo']},
            'tracks':[{'part':'audio','live_track_index':index,'name':label,'file':str(path)}],
            'operations':[
                {'op':'import_vocal_take','params':{'file_path':str(path),'track_name':label,'clip_name':label,'clip_index':0}},
                {'op':'copy_session_clip_to_arrangement','params':{'track_index':index,'clip_index':0,'destination_time_beats':0.0,'name':label}}],
            'source_take':manifest['id'],'source_sha256':manifest['sha256'],'tempo_changed':False}
