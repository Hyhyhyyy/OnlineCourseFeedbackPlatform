"""Loopback-only prototype, with task-scoped pairing for native Bilibili captures."""
import argparse
import io
import json
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

from acquire_course import normalize_url
from bilibili_login import BilibiliLogin
from fetch_course import fetch, save_json, public_summary
from research_workbench import ResearchStore, read, parse_subtitles, suggest_boundaries, export_html

USER = {'id':'local-researcher'}


def fetch_bilibili_subtitles(login, record, folder):
    """Use Bilibili-provided Chinese tracks; preserve language and source provenance."""
    import urllib.request
    response = login.request('https://api.bilibili.com/x/player/wbi/v2?'+urlencode({'bvid':record['bvid'],'cid':record['cid']}))
    if response.get('code')!=0:
        raise ValueError('B站字幕接口未返回可用结果，请核对登录状态与视频权限')
    tracks = response.get('data',{}).get('subtitle',{}).get('subtitles',[])
    tracks = [x for x in tracks if x.get('lan') in ('ai-zh','zh-CN','zh-Hans','zh') and x.get('subtitle_url')]
    if not tracks:
        raise ValueError('当前未取得B站中文字幕；可扫码登录后重试，或导入B站导出的字幕')
    track = sorted(tracks,key=lambda x:x['lan']!='ai-zh')[0]
    url = track['subtitle_url']
    if url.startswith('//'):
        url = 'https:'+url
    host = urlparse(url).hostname or ''
    if urlparse(url).scheme!='https' or not any(host==d or host.endswith('.'+d) for d in ('bilibili.com','hdslb.com','bilivideo.com')):
        raise ValueError('字幕地址不属于预期B站资源域名')
    class RedirectCheck(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urlparse(newurl)
            if target.scheme!='https' or target.hostname!=host:
                raise ValueError('字幕下载重定向需另行核对')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    opener = urllib.request.build_opener(RedirectCheck())
    with opener.open(urllib.request.Request(url,headers={'Referer':'https://www.bilibili.com/'}),timeout=30) as res:
        raw = res.read(10*1024*1024+1)
    if len(raw)>10*1024*1024:
        raise ValueError('字幕文件超过10MB')
    rows, quality = parse_subtitles(raw.decode('utf-8-sig'),'bilibili.json',record['duration_s'])
    save_json(folder/'subtitles.json',{'segments':rows,'quality':quality,'source':'B站字幕接口',
        'language':track['lan'],'track_name':track.get('lan_doc'),'cid':record['cid'],'bvid':record['bvid']})
    (folder/'bilibili-subtitles.json').write_bytes(raw)
    return rows


def create_server(root, port=8766):
    root = Path(root)
    store = ResearchStore(root)
    token = secrets.token_urlsafe(32)
    login = BilibiliLogin()
    lock = threading.RLock()
    workers, pairs = {}, {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def send(self,code,value,kind='application/json; charset=utf-8',attachment=None):
            raw = json.dumps(value,ensure_ascii=False).encode() if isinstance(value,(dict,list)) else value
            self.send_response(code)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if attachment:
                self.send_header('Content-Disposition',f'attachment; filename="{attachment}"')
            origin = self.headers.get('Origin','')
            if origin.startswith(('chrome-extension://','moz-extension://')):
                self.send_header('Access-Control-Allow-Origin',origin)
                self.send_header('Vary','Origin')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except ConnectionError:
                pass

        def check_host(self):
            actual = self.server.server_address[1]
            if self.headers.get('Host') not in (f'127.0.0.1:{actual}',f'localhost:{actual}'):
                raise PermissionError('服务仅允许本机访问')

        def do_OPTIONS(self):
            try:
                self.check_host()
                if not self.headers.get('Origin','').startswith('chrome-extension://'):
                    raise PermissionError('来源不允许')
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
                self.send_header('Access-Control-Allow-Methods','POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers','Content-Type, X-Capture-Token')
                self.end_headers()
            except PermissionError as exc:
                self.send(403,{'error':str(exc)})

        def do_GET(self):
            try:
                self.check_host()
                url=urlparse(self.path);path=url.path
                if path=='/health':
                    return self.send(200,{'service':'course-feedback-research','status':'ok','version':'1.0'})
                if path=='/':
                    return self.send(200,(root/'web/research.html').read_text(encoding='utf-8').replace('__TOKEN__',token).encode(),'text/html; charset=utf-8')
                if path in ('/research.js','/research.css','/model-settings.js'):
                    return self.send(200,(root/'web'/path[1:]).read_bytes(),'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path=='/brand/kejing-symbol.svg':
                    return self.send(200,(root/path[1:]).read_bytes(),'image/svg+xml')
                if path=='/api/tasks':
                    return self.send(200,{'tasks':store.listing(USER)})
                if path=='/api/login/status':
                    return self.send(200,login.status())
                if path=='/api/model/settings':
                    from api_settings import public
                    return self.send(200,public())
                if path=='/api/model/readiness':
                    from model_access import readiness
                    return self.send(200,readiness(parse_qs(url.query).get('provider',['api'])[0]))
                if path=='/api/model/profiles':
                    from model_providers import settings
                    profiles={}
                    for provider in ('local','campus'):
                        try:
                            config=settings(provider)
                            profiles[provider]={'configured':True,'model':config['model'],'url':config['url']}
                        except ValueError:
                            profiles[provider]={'configured':False,'model':'','url':''}
                    return self.send(200,profiles)
                parts=path.strip('/').split('/')
                if len(parts)>=3 and parts[:2]==['api','task']:
                    rid=parts[2];folder=store.folder(rid,USER)
                    if len(parts)==3:
                        return self.send(200,store.get(rid,USER))
                    action=parts[3]
                    if action=='acquisition':
                        return self.send(200,public_summary(read(folder/'acquisition.json',{})))
                    if action=='asset':
                        name=parse_qs(url.query).get('name',[''])[0]
                        meta=read(folder/'input.json')
                        allowed={f['file'] for f in meta['frames']} | ({meta['video']} if meta.get('video') else set())
                        if name not in allowed:
                            raise ValueError('文件未列入本任务素材')
                        target=folder/name
                        if name.endswith('.mp4'):
                            return self.media(target)
                        return self.send(200,target.read_bytes(),mimetypes.guess_type(name)[0] or 'application/octet-stream')
                    if action in ('export','html'):
                        data=store.export(rid,USER)
                        if action=='html':
                            return self.send(200,export_html(data).encode(),'text/html; charset=utf-8','kejing-report.html')
                        return self.send(200,data,attachment='kejing-research-record.json')
                return self.send(404,{'error':'未找到入口'})
            except PermissionError as exc:
                self.send(403,{'error':str(exc)})
            except (ValueError,KeyError,TypeError,OSError) as exc:
                self.send(400,{'error':str(exc)})

        def media(self,path):
            import re
            size=path.stat().st_size;start=0;end=size-1;partial=False
            if self.headers.get('Range'):
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
                if not match:
                    return self.send(416,{'error':'无效的视频范围'})
                start=int(match[1]);end=min(int(match[2]) if match[2] else end,end);partial=True
                if start>end:
                    return self.send(416,{'error':'视频范围超出文件'})
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(end-start+1))
            if partial:
                self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(262144,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
            except ConnectionError:
                pass

        def do_POST(self):
            try:
                self.check_host()
                url=urlparse(self.path);path=url.path;query=parse_qs(url.query)
                if path=='/api/capture':
                    pairing=pairs.get(self.headers.get('X-Capture-Token',''))
                    if not pairing or pairing['expires']<time.time():
                        raise PermissionError('采集配对已失效，请在工作台重新生成')
                else:
                    pairing=None
                    if self.headers.get('X-Demo-Token')!=token:
                        raise PermissionError('会话校验失败，请刷新页面')
                    actual=self.server.server_address[1]
                    if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{actual}',f'http://localhost:{actual}'):
                        raise PermissionError('来源不允许')
                length=int(self.headers.get('Content-Length','0'))
                parts=path.strip('/').split('/')
                if len(parts)==4 and parts[:2]==['api','task'] and parts[3]=='upload':
                    with lock:
                        if workers.get(parts[2]) and workers[parts[2]].is_alive():
                            raise ValueError('任务进行中，请完成后修改素材')
                        self.connection.settimeout(120)
                        return self.send(200,store.upload(parts[2],USER,query.get('kind',[''])[0],self.rfile,length,query))
                if not 0<length<=30*1024*1024:
                    raise ValueError('请求大小无效')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('请求应为JSON对象')
                if path=='/api/capture':
                    with lock:
                        if workers.get(pairing['id']) and workers[pairing['id']].is_alive():
                            raise ValueError('素材获取进行中，请等待完成后采集')
                        return self.send(200,store.capture(pairing['id'],USER,data))
                if path.startswith('/api/login/'):
                    action=path.rsplit('/',1)[-1]
                    if action not in ('start','poll','clear','refresh'):
                        raise ValueError('登录操作无效')
                    return self.send(200,getattr(login,action)())
                if path.startswith('/api/model/'):
                    import api_settings
                    actions={'configure':lambda:api_settings.configure(data),'models':api_settings.models,
                        'select':lambda:api_settings.select_model(data),'probe':api_settings.probe,
                        'probe-vision':lambda:api_settings.probe(True),'balance':api_settings.balance,'clear':api_settings.clear}
                    action=path.rsplit('/',1)[-1]
                    if action not in actions:
                        raise ValueError('模型设置操作无效')
                    return self.send(200,actions[action]())
                if path=='/api/tasks':
                    source_url=normalize_url(data['source_url']) if data.get('source_url') else None
                    result=store.create(data,USER)
                    if source_url:
                        folder=store.folder(result['input']['id'],USER)
                        meta=result['input'];meta['source_url']=source_url
                        save_json(folder/'input.json',meta)
                    return self.send(201,result)
                if len(parts)==4 and parts[:2]==['api','task']:
                    rid,action=parts[2:];folder=store.folder(rid,USER)
                    with lock:
                        if workers.get(rid) and workers[rid].is_alive():
                            raise ValueError('任务进行中，请完成后修改')
                        if action=='configure':
                            return self.send(200,store.configure(rid,USER,data))
                        if action=='prepare':
                            store.run(rid,USER)
                            return self.send(200,store.get(rid,USER))
                        if action=='review':
                            return self.send(200,store.review(rid,USER,data))
                        if action=='pair':
                            meta=read(folder/'input.json')
                            if not meta.get('source_url'):
                                raise ValueError('请先创建带B站链接的任务')
                            secret=secrets.token_urlsafe(32)
                            pairs[secret]={'id':rid,'expires':time.time()+3600}
                            return self.send(200,{'endpoint':f'http://127.0.0.1:{self.server.server_address[1]}/api/capture',
                                'token':secret,'source_url':meta['source_url'],'task_id':rid,'expires_in':3600})
                        if action=='acquire':
                            meta=read(folder/'input.json')
                            if not meta.get('source_url'):
                                raise ValueError('当前任务未填写B站课程链接')
                            save_json(folder/'state.json',{'status':'running','stage':'获取B站视频、弹幕与中文字幕'})
                            def worker():
                                cookie=folder/'.session.cookies.txt'
                                try:
                                    record=fetch(meta['source_url'],folder,login.export_for_job(cookie),reuse_existing=True)
                                    latest=read(folder/'input.json')
                                    if record.get('artifacts',{}).get('video'):
                                        latest.update(video='course.mp4',duration=record['artifacts']['video']['duration_s'])
                                        store.changed(folder,latest)
                                    if record.get('status')!='acquired':
                                        raise ValueError(record.get('error') or '视频获取未完成')
                                    rows=fetch_bilibili_subtitles(login,record,folder)
                                    save_json(folder/'segments.json',suggest_boundaries(rows,latest['duration']))
                                    latest['segmentation_method']='字幕章节表达、停顿与最长时长规则候选；待人工复核'
                                    store.changed(folder,latest)
                                    save_json(folder/'state.json',{'status':'acquired','stage':'B站素材已取得，中文字幕已检查'})
                                except Exception as exc:
                                    save_json(folder/'state.json',{'status':'failed','stage':str(exc)[:700]})
                                finally:
                                    if cookie.exists():cookie.unlink()
                            workers[rid]=threading.Thread(target=worker,daemon=True);workers[rid].start()
                            return self.send(202,{'id':rid})
                return self.send(404,{'error':'操作未找到'})
            except PermissionError as exc:
                self.send(403,{'error':str(exc)})
            except (ValueError,KeyError,TypeError,OSError) as exc:
                self.send(400,{'error':str(exc)})

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    return server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args();server=create_server(Path(__file__).resolve().parents[1],args.port)
    print(f'课镜研究工作台 http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
