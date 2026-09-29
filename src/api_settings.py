"""Session-only API credentials, official model discovery and usage accounting."""
import datetime
import time
import base64
import struct
import zlib
import json
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

PRESETS = {
    'bailian': dict(name='阿里云百炼 · Qwen 系列', url='https://dashscope.aliyuncs.com/compatible-mode/v1', docs='https://help.aliyun.com/zh/model-studio/list-models', note='官方示例 qwen3.5-plus（图文）、qwen3-max（文本）；北京新业务空间请按控制台填写专属地址。'),
    'deepseek': dict(name='DeepSeek · DeepSeek 系列', url='https://api.deepseek.com', docs='https://api-docs.deepseek.com/api/list-models/', note='官方示例 deepseek-flash、deepseek-v4-pro（以刷新结果为准）；视觉与 Chat Completions 兼容性需单独验证。'),
    'siliconflow': dict(name='硅基流动 · Qwen / DeepSeek 等', url='https://api.siliconflow.cn/v1', docs='https://api-docs.siliconflow.cn/docs/api/models-get', note='多种开源模型托管；模型列表包含不同能力，不能任意互换。'),
    'kimi': dict(name='Kimi · Kimi 系列', url='https://api.moonshot.cn/v1', docs='https://platform.kimi.ai/docs/models', note='官方当前列出 kimi-k3、kimi-k2.6；以账户地区与实时列表为准。'),
    'glm': dict(name='智谱 · GLM 系列', url='https://open.bigmodel.cn/api/paas/v4', docs='https://docs.bigmodel.cn/cn/guide/start/model-overview', note='官方示例 GLM-5.3、GLM-5.3-Flash、GLM-4.6V；列表接口不支持时手动填写官方模型 ID。'),
    'custom': dict(name='其他 OpenAI 兼容接口', url='', docs='', note='填写服务商官方 HTTPS 地址。不要填入来源不明的代理地址。'),
}
LOCK = threading.RLock()
CONFIG = None
STATE = dict(text_verified_until=0, vision_verified_until=0, key_status='尚未验证', models=[], checked_at=None, balance='未查询', requests=0, input_tokens=0, output_tokens=0, missing_usage=0)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('接口重定向已拒绝，请填写官方最终地址')


_OPENER = None
_OPENER_LOCK = threading.Lock()

def get_opener():
    # Reuse proxy discovery and TLS handler setup across paginated/model calls.
    global _OPENER
    with _OPENER_LOCK:
        if _OPENER is None:_OPENER=urllib.request.build_opener(NoRedirect)
        return _OPENER


def request_json(url, key, body=None):
    req=urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with get_opener().open(req, timeout=120 if body is not None else 20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        messages={401:'Key 未通过认证',403:'服务拒绝访问或权限不足',404:'接口或模型不存在',429:'限流或配额不足',400:'服务不接受当前参数或模型能力不匹配'}
        invalidate(messages.get(exc.code,'服务返回 HTTP '+str(exc.code)))
        raise ValueError(STATE['key_status']) from None
    except (urllib.error.URLError,TimeoutError):
        invalidate('服务连接失败或超时，请核对地址与网络')
        raise ValueError(STATE['key_status']) from None


def configure(data):
    global CONFIG
    vendor=data.get('vendor','custom')
    if vendor not in PRESETS:raise ValueError('厂商选项无效')
    url=str(data.get('url','')).strip().rstrip('/')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请输入不带凭据或查询参数的官方 HTTPS 接口地址')
    key=str(data.get('key','')).strip()
    if not key or len(key)>4096 or any(c.isspace() for c in key):raise ValueError('请输入有效格式的 API Key')
    model=str(data.get('model','')).strip()
    output_format=data.get('format','json_object')
    if output_format not in ('json_object','json_schema'):raise ValueError('输出格式无效')
    with LOCK:
        CONFIG=dict(vendor=vendor,url=url,key=key,model=model,format=output_format)
        STATE.update(text_verified_until=0,vision_verified_until=0,key_status='已保存到本次服务内存，尚未验证',models=[],checked_at=None,balance='未查询',requests=0,input_tokens=0,output_tokens=0,missing_usage=0)
    return public()


def snapshot(require_model=True):
    with LOCK:
        if not CONFIG:raise ValueError('请先填写 API 设置')
        cfg=dict(CONFIG)
    if require_model and not cfg['model']:raise ValueError('请先选择或填写模型 ID')
    return cfg


def public():
    with LOCK:
        return dict(STATE,configured=bool(CONFIG),vendor=CONFIG['vendor'] if CONFIG else None,
            url=CONFIG['url'] if CONFIG else '',model=CONFIG['model'] if CONFIG else '',
            format=CONFIG['format'] if CONFIG else 'json_object',
            key_present=bool(CONFIG),campus_status='【待真实接入测试】',presets=PRESETS)


def clear():
    global CONFIG
    with LOCK:
        CONFIG=None
        STATE.update(text_verified_until=0,vision_verified_until=0,key_status='已清除',models=[],checked_at=None,balance='未查询',requests=0,input_tokens=0,output_tokens=0,missing_usage=0)
    return public()


def record_usage(usage):
    with LOCK:
        STATE['requests']+=1
        if not isinstance(usage,dict):STATE['missing_usage']+=1;return
        inp=usage.get('prompt_tokens',usage.get('input_tokens'))
        out=usage.get('completion_tokens',usage.get('output_tokens'))
        if inp is None or out is None:STATE['missing_usage']+=1
        STATE['input_tokens']+=int(inp or 0);STATE['output_tokens']+=int(out or 0)


def models():
    cfg=snapshot(False)
    try:
        if cfg['vendor']=='bailian':
            parsed=urlsplit(cfg['url'])
            endpoint=parsed.scheme+'://'+parsed.netloc+'/api/v1/models'
            rows=[]
            for page in range(1,101):
                result=request_json(endpoint+f'?page_no={page}&page_size=100&capabilities=TG&capabilities=VU',cfg['key'])
                output=result.get('output',{})
                batch=output.get('models')
                if not isinstance(batch,list):raise ValueError('百炼模型列表结构不兼容，请核对业务空间地址')
                rows.extend({'id':m['model']} for m in batch if isinstance(m.get('model'),str))
                if len(rows)>=output.get('total',len(rows)) or not batch:break
            else:raise ValueError('模型列表页数超过上限，未将部分列表标为完整')
        else:
            result=request_json(cfg['url']+'/models',cfg['key'])
            rows=result.get('data',[])
        if not isinstance(rows,list):raise ValueError('厂商模型列表结构不兼容，请从官方文档填写模型 ID')
        ids=sorted({m['id'] for m in rows if isinstance(m,dict) and isinstance(m.get('id'),str)})
        with LOCK:STATE.update(models=ids,key_status='模型列表请求成功；所选模型推理权限尚待验证',checked_at=datetime.datetime.now().astimezone().isoformat())
    except ValueError as exc:
        invalidate(str(exc))
        with LOCK:STATE['models']=[]
        raise
    return public()


def select_model(data):
    model=str(data.get('model','')).strip()
    if not model or len(model)>200:raise ValueError('模型 ID 无效')
    with LOCK:
        snapshot(False)
        if CONFIG['model']!=model:invalidate('模型已更改，请重新验证')
        CONFIG['model']=model
        STATE['key_status']='模型已选择，推理尚待验证'
    return public()


def probe(vision=False):
    cfg=snapshot()
    # Only synthetic material is sent by this explicit, potentially billable test.
    body={'model':cfg['model'],'messages':[{'role':'user','content':'只返回 JSON 对象：{"ok":true}'}], 'max_tokens':256,'response_format':{'type':'json_object'}}
    if vision:
        def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',32,32,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*96)*32))+chunk(b'IEND',b'')
        body['messages'][0]['content']=[{'type':'text','text':'请确认收到图片，只返回 JSON 对象：{"ok":true}'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png).decode()}}]
    if cfg.get('vendor')=='bailian':body['enable_thinking']=False
    try:
        result=request_json(cfg['url']+'/chat/completions',cfg['key'],body)
        record_usage(result.get('usage'))
        choice=result['choices'][0]
        if choice.get('finish_reason')!='stop' or json.loads(choice['message']['content']).get('ok') is not True:
            raise ValueError('请求已返回，但 JSON 输出探测未通过；不能据此认定 Key 无效')
        with LOCK:
            if CONFIG!=cfg:raise ValueError('验证期间配置已更改，请重新验证')
            STATE.update(text_verified_until=time.time()+900,key_status='合成图片与 JSON 响应测试通过' if vision else '文本 JSON 响应测试通过；图文能力另行验证',checked_at=datetime.datetime.now().astimezone().isoformat())
            if vision:STATE['vision_verified_until']=time.time()+900
    except (ValueError,KeyError,IndexError) as exc:
        invalidate(str(exc) if isinstance(exc,ValueError) else '模型响应结构不兼容')
        raise ValueError(STATE['key_status']) from None
    return public()


def balance():
    cfg=snapshot(False)
    host=urlsplit(cfg['url']).hostname
    if host=='api.deepseek.com':
        result=request_json('https://api.deepseek.com/user/balance',cfg['key'])
        value='；'.join(str(x.get('total_balance','未知'))+' '+str(x.get('currency','')) for x in result.get('balance_infos',[])) or '接口未返回余额'
    elif host=='api.siliconflow.cn':
        result=request_json('https://api.siliconflow.cn/v1/user/info',cfg['key']).get('data',{})
        value=str(result.get('totalBalance',result.get('balance','接口未返回余额')))+'（厂商原值；币种请核对控制台）'
    else:value='当前厂商未接入余额接口，请查看官方控制台；不以 Token 数推算账户余额'
    with LOCK:STATE['balance']=value
    return public()


def invalidate(reason):
    with LOCK:STATE.update(text_verified_until=0,vision_verified_until=0,key_status=reason)


def readiness():
    with LOCK:
        text=bool(CONFIG and STATE['text_verified_until']>time.time())
        vision=bool(text and STATE['vision_verified_until']>time.time())
        reason=STATE['key_status'] if not text else ('已验证文本与图文接口' if vision else '文本已就绪；图文接口尚未验证')
        if CONFIG and not text and STATE['text_verified_until']:reason='验证已过期，请重新验证连接'
        return dict(text_ready=text,vision_ready=vision,reason=reason,model=CONFIG['model'] if CONFIG else '',valid_until=STATE['text_verified_until'])
