import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import api_settings
from research_server import create_server
from research_workbench import ResearchStore


class ResearchModelTests(unittest.TestCase):
    def setUp(self):
        api_settings.clear()
        self.addCleanup(api_settings.clear)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        (self.root/'web').mkdir();(self.root/'web/research.html').write_text('__TOKEN__')
        self.server=create_server(self.root,0)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        with urllib.request.urlopen(self.url) as r:self.token=r.read().decode()

    def request(self,path,data=None,token=True):
        request=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type':'application/json',**({'X-Demo-Token':self.token} if token else {})})
        with urllib.request.urlopen(request) as r:return json.load(r)

    def test_api_actions_protected_and_secrets_excluded(self):
        config=dict(vendor='custom',url='https://example.test/v1',key='test-secret-only',model='test-model',format='json_schema')
        with self.assertRaises(urllib.error.HTTPError) as denied:self.request('/api/model/configure',config,False)
        self.assertEqual(denied.exception.code,403)
        self.request('/api/model/configure',config)
        state=self.request('/api/model/settings');self.assertEqual(state['format'],'json_schema')
        self.assertNotIn(config['key'],json.dumps(state))
        with patch.object(api_settings,'request_json',return_value={'data':[{'id':'test-model'}]}):
            self.assertEqual(self.request('/api/model/models',{})['models'],['test-model'])
        self.assertFalse(self.request('/api/model/readiness?provider=api')['text_ready'])
        response={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],'usage':{'prompt_tokens':2,'completion_tokens':1}}
        with patch.object(api_settings,'request_json',return_value=response) as call:
            self.request('/api/model/probe-vision',{})
            self.assertTrue(call.call_args.args[2]['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;'))
        self.assertTrue(self.request('/api/model/readiness?provider=api')['vision_ready'])
        self.request('/api/model/select',{'model':'another-model'})
        self.assertFalse(self.request('/api/model/readiness?provider=api')['vision_ready'])
        self.request('/api/model/balance',{})
        self.assertFalse(self.request('/api/model/clear',{})['key_present'])

    def test_profiles_do_not_expose_campus_credentials_or_claim_readiness(self):
        env={'COURSE_CAMPUS_BASE_URL':'https://campus.example/v1','COURSE_CAMPUS_MODEL':'campus-model','COURSE_CAMPUS_KEY':'campus-secret-only'}
        with patch.dict('os.environ',env):
            result=self.request('/api/model/profiles')
            self.assertTrue(result['campus']['configured'])
            self.assertNotIn(env['COURSE_CAMPUS_KEY'],json.dumps(result))
            self.assertFalse(self.request('/api/model/readiness?provider=campus')['text_ready'])
        with patch.dict('os.environ',{**env,'COURSE_CAMPUS_BASE_URL':'https://user:secret@campus.example/v1'}):
            self.assertFalse(self.request('/api/model/profiles')['campus']['configured'])
        with patch('local_model.status',return_value={'connected':False,'model':'qwen35-4b'}):
            self.assertFalse(self.request('/api/model/readiness?provider=local')['text_ready'])

    def test_task_choice_persists_without_triggering_model_execution(self):
        user={'id':'local-researcher'};store=ResearchStore(self.root)
        for provider in ('api','local','campus'):
            task=self.request('/api/tasks',{'title':'choice test','mode':'snapshot','rights':True,'model_provider':provider,'key':'ignored-secret'})
            rid=task['input']['id'];self.assertEqual(task['input']['model_provider'],provider)
            raw=io.BytesIO();Image.new('RGB',(20,20),'white').save(raw,format='PNG');blob=raw.getvalue()
            store.upload(rid,user,'image',io.BytesIO(blob),len(blob),{})
            with patch.object(api_settings,'request_json',side_effect=AssertionError('Unexpected inference')):
                self.request('/api/task/'+rid+'/prepare',{})
            exported=self.request('/api/task/'+rid+'/export')
            self.assertIsNone(exported['report']['model']);self.assertNotIn('ignored-secret',json.dumps(exported))
            self.request('/api/task/'+rid+'/configure',{'model_provider':'local'})
            self.assertEqual(self.request('/api/task/'+rid)['input']['model_provider'],'local')
            self.assertTrue(self.request('/api/task/'+rid+'/export')['stale'])
            before=self.request('/api/task/'+rid)['input']
            with self.assertRaises(urllib.error.HTTPError):self.request('/api/task/'+rid+'/configure',{'model_provider':'invalid'})
            self.assertEqual(self.request('/api/task/'+rid)['input'],before)

    def test_invalid_format_keeps_existing_connection(self):
        config=dict(vendor='custom',url='https://example.test/v1',key='test-only',model='original')
        self.request('/api/model/configure',config)
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/model/configure',{**config,'model':'changed','format':'invalid'})
        self.assertEqual(self.request('/api/model/settings')['model'],'original')


if __name__=='__main__':unittest.main()
