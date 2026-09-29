import base64
import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from PIL import Image

from research_workbench import ResearchStore, parse_subtitles, validate_boundaries, export_html, read
from research_server import create_server
from fetch_course import save_json
from acquire_course import normalize_url


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=ResearchStore(self.root);self.user={'id':'local-researcher'}

    def task(self,mode='snapshot'):
        data=self.store.create({'title':'字幕课例','mode':mode,'rights':True,'duration':60},self.user)
        return data['input']['id']

    def image(self):
        stream=io.BytesIO();Image.new('RGB',(160,90),'white').save(stream,format='PNG')
        return stream.getvalue()

    def test_subtitle_union_coverage_and_bad_ranges(self):
        rows,qa=parse_subtitles(json.dumps({'body':[{'from':0,'to':10,'content':'甲'}, {'from':5,'to':15,'content':'乙'}]}),'bili.json',20)
        self.assertEqual(qa['coverage'],.75);self.assertEqual(qa['overlap_count'],1)
        with self.assertRaises(ValueError):parse_subtitles('1\n00:00:10,000 --> 00:00:30,000\n甲','test.srt',20)
        with self.assertRaises(ValueError):parse_subtitles('WEBVTT\n\n1\nnot a timestamp\n甲','test.vtt',20)

    def test_timestamped_formats(self):
        for text,name in [('WEBVTT\n\n00:00.000 --> 00:03.000\n定义','x.vtt'),('1\n00:00:00,000 --> 00:00:03,000\n定义','x.srt')]:
            rows,qa=parse_subtitles(text,name,10);self.assertEqual(rows[0]['text'],'定义');self.assertEqual(qa['coverage'],.3)

    def test_contiguous_boundaries(self):
        with self.assertRaises(ValueError):validate_boundaries([{'start':0,'end':8,'title':'甲'},{'start':9,'end':20,'title':'乙'}],20)
        with self.assertRaises(ValueError):validate_boundaries([{'start':0,'end':19,'title':'甲'}],20)
        self.assertEqual(len(validate_boundaries([{'start':0,'end':20,'title':'甲'}],20)),1)

    def test_original_image_and_pending_report(self):
        rid=self.task();raw=self.image()
        self.store.upload(rid,self.user,'image',io.BytesIO(raw),len(raw),{'time':['5']})
        self.store.run(rid,self.user);data=self.store.export(rid,self.user)
        frame=data['report']['snapshots'][0]['frame']
        self.assertEqual((self.store.folder(rid,self.user)/frame['file']).read_bytes(),raw)
        self.assertIsNone(data['report']['model']);self.assertEqual(data['state']['status'],'prepared')
        self.assertEqual(data['report']['snapshots'][0]['result']['danmaku_summary'],'')
        self.store.configure(rid,self.user,{'use_title':False})
        self.assertTrue(self.store.export(rid,self.user)['stale'])

    def test_review_keeps_provenance_and_exports_manual_edit(self):
        rid=self.task();raw=self.image();self.store.upload(rid,self.user,'image',io.BytesIO(raw),len(raw),{})
        self.store.run(rid,self.user);report=self.store.get(rid,self.user)['report']
        revised=copy.deepcopy({k:report[k] for k in ('snapshots','segments')})
        revised['snapshots'][0]['frame']['time']=8
        with self.assertRaises(ValueError):self.store.review(rid,self.user,{'reason':'test','revised':revised})
        revised['snapshots'][0]['frame']=copy.deepcopy(report['snapshots'][0]['frame'])
        revised['snapshots'][0]['result']['danmaku_summary']='人工复核：<script>仅为测试</script>'
        self.store.review(rid,self.user,{'reason':'核对原图','revised':revised})
        data=self.store.export(rid,self.user)
        self.assertEqual(data['report']['snapshots'][0]['result']['danmaku_summary'],'')
        page=export_html(data);self.assertNotIn('<script>',page);self.assertIn('人工复核',page)

    def capture_data(self):
        return {'source_url':'https://www.bilibili.com/video/BV1Y2DGYoEEw/', 'actual_time':5,'duration':60,
                'subtitle_stable':True,'subtitle_text':'定义','native_player':True,
                'png_base64':base64.b64encode(self.image()).decode()}

    def test_capture_matching_course_and_visible_subtitles(self):
        rid=self.task('video');folder=self.store.folder(rid,self.user);meta=read(folder/'input.json')
        meta['source_url']=normalize_url(self.capture_data()['source_url']);save_json(folder/'input.json',meta)
        bad=self.capture_data();bad['source_url']+='?p=2'
        with self.assertRaises(ValueError):self.store.capture(rid,self.user,bad)
        bad=self.capture_data();bad['subtitle_stable']=False
        with self.assertRaises(ValueError):self.store.capture(rid,self.user,bad)
        self.store.capture(rid,self.user,self.capture_data())
        result=self.store.get(rid,self.user);self.assertEqual(result['input']['frames'][0]['source'],'bilibili_native_capture')
        with self.assertRaises(PermissionError):self.store.get(rid,{'id':'another'})

    def test_native_capture_then_subtitle_without_downloaded_video(self):
        rid=self.task('video');folder=self.store.folder(rid,self.user);meta=read(folder/'input.json')
        meta['source_url']=normalize_url(self.capture_data()['source_url']);save_json(folder/'input.json',meta)
        self.store.capture(rid,self.user,self.capture_data())
        raw=b'1\n00:00:00,000 --> 00:01:00,000\nChinese subtitles\n'
        self.store.upload(rid,self.user,'subtitles',io.BytesIO(raw),len(raw),{'name':['bili.srt']})
        self.store.run(rid,self.user);self.assertEqual(self.store.get(rid,self.user)['state']['status'],'prepared')

    def test_http_pairing_csrf_and_retired_routes(self):
        (self.root/'web').mkdir();(self.root/'web/research.html').write_text('__TOKEN__',encoding='utf-8')
        server=create_server(self.root,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url='http://127.0.0.1:'+str(server.server_address[1])
        with urllib.request.urlopen(url) as response:token=response.read().decode()
        def post(path,data,headers):
            req=urllib.request.Request(url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**headers})
            with urllib.request.urlopen(req) as response:return json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as denied:post('/api/tasks',{}, {})
        self.assertEqual(denied.exception.code,403)
        result=post('/api/tasks',{'title':'fixture','mode':'snapshot','rights':True,'source_url':self.capture_data()['source_url']},{'X-Demo-Token':token})
        rid=result['input']['id'];pair=post('/api/task/'+rid+'/pair',{}, {'X-Demo-Token':token})
        post('/api/capture',self.capture_data(),{'X-Capture-Token':pair['token']})
        with self.assertRaises(urllib.error.HTTPError):post('/api/capture',self.capture_data(),{'X-Capture-Token':'wrong'})
        with self.assertRaises(urllib.error.HTTPError) as legacy:urllib.request.urlopen(url+'/api/platform/me')
        self.assertEqual(legacy.exception.code,404)


if __name__=='__main__':unittest.main()
