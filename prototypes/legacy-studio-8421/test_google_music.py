import base64
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import google_music as g
import server

# Synthetic silence is a test fixture only, never a generated song.
def wav_fixture():
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*8000)
    return out.getvalue()

class GoogleMusicTests(unittest.TestCase):
    def test_prompt_retains_lyrics_and_structure(self):
        p=g.build_prompt({'prompt':'Dub 110 BPM','vocals':'vocal','lyrics':'[Verse]\n夜の街','structure':'0:00 Intro','duration':120})
        self.assertIn('[Verse]\n夜の街',p);self.assertIn('0:00 Intro',p)
        self.assertNotIn('夜の街',g.build_prompt({'prompt':'Dub','vocals':'instrumental','lyrics':'夜の街'}))
    def test_reject_bad_inputs(self):
        for data in [{'prompt':''},{'prompt':'x','duration':0},{'prompt':'x','vocals':'bad'}]:
            with self.assertRaises(ValueError):g.build_prompt(data)
    def test_official_steps_response(self):
        raw=wav_fixture()
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'lyrics'},{'type':'audio','mime_type':'audio/wav','data':base64.b64encode(raw).decode()}]}]}
        self.assertEqual(g.parse_response(response),(raw,'.wav','lyrics'))
        with self.assertRaises(ValueError):g.parse_response({'status':'failed','steps':[]})
        with self.assertRaises(ValueError):g.parse_response({'steps':[]})
    def test_audio_integrity_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); m=g.save_audio(root,wav_fixture(),'.wav','test_fixture')
            self.assertEqual(m['duration_sec'],1.0)
            _,p=g.take(root,m['id']);p.write_bytes(b'changed')
            with self.assertRaises(ValueError):g.take(root,m['id'])
            with self.assertRaises(ValueError):g.save_audio(root,b'not audio','.wav','test_fixture')
    def test_missing_key_does_not_send_or_write(self):
        with tempfile.TemporaryDirectory() as d,patch.object(g,'urlopen') as call:
            with self.assertRaises(ValueError):g.generate(Path(d),'test','lyria-3.5','')
            call.assert_not_called();self.assertEqual(list(Path(d).iterdir()),[])
    def test_mock_api_saves_audio_without_secret(self):
        response={'id':'test-response','status':'completed','steps':[{'type':'model_output','content':[{'type':'audio','mime_type':'audio/wav','data':base64.b64encode(wav_fixture()).decode()}]}]}
        with tempfile.TemporaryDirectory() as d,patch.object(g,'urlopen',return_value=io.BytesIO(json.dumps(response).encode())) as call:
            root=Path(d);m=g.generate(root,'test','lyria-3.5','secret-for-test')
            self.assertEqual(m['source'],'gemini_api');self.assertEqual(m['duration_sec'],1)
            request=call.call_args.args[0]
            self.assertEqual(request.full_url,g.API_URL)
            self.assertEqual(json.loads(request.data)['model'],'lyria-3.5')
            for p in root.rglob('*.json'):self.assertNotIn('secret-for-test',p.read_text())
    def test_audio_plan_does_not_change_tempo_or_create_midi(self):
        plan=g.audio_plan(Path('/tmp'),{'id':'test','sha256':'hash'},Path('/tmp/test.wav'),{'tracks':[{}],'tempo':110})
        self.assertEqual([o['op'] for o in plan['operations']],['import_vocal_take','copy_session_clip_to_arrangement'])
        self.assertEqual(plan['operations'][1]['params']['track_index'],1)
    def test_adoption_invalidates_review(self):
        with tempfile.TemporaryDirectory() as d,patch.object(server,'ROOT',Path(d)):
            result=server.action('/api/google/project',{'prompt':'test'})
            p=server.project(result['id']);m=g.save_audio(p,wav_fixture(),'.wav','test_fixture')
            (p/'baseline.json').write_text('{}')
            server.action('/api/google/adopt',{'id':result['id'],'take_id':m['id']})
            self.assertFalse((p/'baseline.json').exists())
            self.assertEqual(json.loads((p/'adopted.json').read_text())['sha256'],m['sha256'])
    def test_audio_prepare_uses_real_ableton_adapter_without_live_writes(self):
        with tempfile.TemporaryDirectory() as d,patch.object(server,'ROOT',Path(d)),patch.object(server,'live_state',return_value={'tracks':[{'name':'Existing'}],'tempo':110,'signature':[4,4]}):
            result=server.action('/api/google/project',{'prompt':'test'})
            p=server.project(result['id']);m=g.save_audio(p,wav_fixture(),'.wav','test_fixture')
            server.action('/api/google/adopt',{'id':result['id'],'take_id':m['id']})
            plan=server.action('/api/prepare',{'id':result['id']})['plan']
            self.assertTrue((p/'job.json').exists());self.assertEqual(len(plan['operations']),2)

if __name__=='__main__':unittest.main()
