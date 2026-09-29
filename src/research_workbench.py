"""Snapshot and subtitled-video research workflow; independent of legacy comment labels."""
import hashlib
import html
import json
import math
import re
import time
import uuid
from pathlib import Path

from fetch_course import save_json

VERSION = 'snapshot-workbench-1'
CANDIDATES = []  # Final taxonomy will be connected after the study freezes it.


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def number(value, name, minimum=0):
    if isinstance(value, bool):
        raise ValueError(name+'应为数值')
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(name+'应为数值') from None
    if not math.isfinite(result) or result < minimum:
        raise ValueError(name+'超出有效范围')
    return result


def parse_subtitles(text, filename, duration):
    """Strict timestamp validation and union coverage, including overlaps."""
    if filename.lower().endswith('.json'):
        value = json.loads(text)
        rows = value if isinstance(value, list) else value.get('segments', value.get('body'))
        if not isinstance(rows, list):
            raise ValueError('JSON需为segments数组或B站body数组')
        raw = [(x.get('start', x.get('from')), x.get('end', x.get('to')), x.get('text', x.get('content', ''))) for x in rows]
    else:
        def seconds(stamp):
            parts = stamp.replace(',', '.').split(':')
            return sum(float(v)*60**i for i, v in enumerate(reversed(parts)))
        raw = []
        for block in re.split(r'\n\s*\n', text.replace('\r', '').strip()):
            lines = block.splitlines()
            at = next((i for i, line in enumerate(lines) if '-->' in line), None)
            if at is None:
                if block.startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
                    continue
                if block.strip():
                    raise ValueError('字幕块缺少起止时间')
                continue
            match = re.fullmatch(r'\s*([\d:.,]+)\s+-->\s+([\d:.,]+)(?:\s+.*)?', lines[at])
            if not match:
                raise ValueError('字幕时间格式无效')
            raw.append((seconds(match[1]), seconds(match[2]), re.sub(r'<[^>]+>', '', '\n'.join(lines[at+1:]))))
    if not raw or len(raw) > 50000:
        raise ValueError('字幕为空或超过50000条')
    rows, previous, out_of_order = [], -1, 0
    for index, (start, end, content) in enumerate(raw):
        a, b = number(start, '字幕起点'), number(end, '字幕终点')
        if not a < b <= duration+0.1 or a >= duration:
            raise ValueError(f'第{index+1}条字幕超出视频时间范围')
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f'第{index+1}条字幕文本为空')
        out_of_order += a < previous
        previous = a
        rows.append({'start': a, 'end': min(b, duration), 'text': content.strip()})
    rows.sort(key=lambda x: x['start'])
    covered, end, overlaps = 0, 0, 0
    for row in rows:
        overlaps += row['start'] < end
        covered += max(0, row['end']-max(end, row['start']))
        end = max(end, row['end'])
    return rows, {'count': len(rows), 'coverage': round(covered/duration, 4), 'overlap_count': overlaps,
                  'reordered_count': out_of_order, 'uncovered_seconds': round(duration-covered, 3),
                  'note': '覆盖率描述时间覆盖；字幕准确性需结合视频复核。'}


def validate_boundaries(rows, duration):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise ValueError('片段数量应在1至500之间')
    end, result = 0, []
    for i, row in enumerate(rows):
        a, b = number(row.get('start'), '片段起点'), number(row.get('end'), '片段终点')
        if abs(a-end) > .001 or b <= a or b > duration+.001:
            raise ValueError('片段须从0开始，连续、不重叠，且处于视频范围内')
        title = str(row.get('title', '')).strip()
        if not title or len(title) > 200:
            raise ValueError('每个片段需填写不超过200字的标题')
        result.append({'id': f's{i+1:04d}', 'start': a, 'end': b, 'title': title})
        end = b
    if abs(end-duration) > .001:
        raise ValueError('片段须完整覆盖视频')
    return result


def suggest_boundaries(subtitles, duration):
    """Inspectable engineering baseline, not an implementation of a cited paper."""
    starts = [0.0]
    for i, row in enumerate(subtitles):
        gap = row['start']-starts[-1]
        previous = subtitles[max(0, i-1)]
        chapter = re.search(r'接下来|下面[来看讲]|第[一二三四五六七八九十\d]+[章节]|例题|小结|总结一下', row['text'])
        pause = row['start']-previous['end'] >= 3
        if row['start'] < duration-5 and ((gap >= 30 and (chapter or pause)) or gap >= 180):
            starts.append(row['start'])
    return [{'id': f's{i+1:04d}', 'start': a, 'end': starts[i+1] if i+1<len(starts) else duration,
             'title': next((r['text'][:45] for r in subtitles if r['end']>a), '待命名片段')}
            for i, a in enumerate(starts)]


class ResearchStore:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root/'outputs/research'
        self.directory.mkdir(parents=True, exist_ok=True)

    def folder(self, rid, user):
        if not re.fullmatch(r'[0-9a-f]{32}', rid):
            raise ValueError('研究任务编号无效')
        folder = self.directory/rid
        meta = read(folder/'input.json')
        if not meta or meta['owner'] != user['id']:
            raise PermissionError('仅任务创建者可以查看和修改')
        return folder

    def create(self, data, user):
        title = str(data.get('title', '')).strip()
        if not title or len(title)>200:
            raise ValueError('请填写不超过200字的课程标题')
        if data.get('rights') is not True:
            raise ValueError('请确认材料已获准用于本次本地研究')
        mode = data.get('mode')
        if mode not in ('snapshot', 'video'):
            raise ValueError('请选择快照或视频任务')
        provider=data.get('model_provider','api')
        if provider not in ('api','local','campus'):
            raise ValueError('模型运行方式无效')
        duration = number(data.get('duration', 0), '视频时长')
        timestamp = number(data.get('timestamp', 0), '截图时间')
        if duration and timestamp > duration:
            raise ValueError('截图时间超出视频时长')
        rid = uuid.uuid4().hex
        folder = self.directory/rid
        folder.mkdir()
        save_json(folder/'input.json', {'id': rid, 'owner': user['id'], 'title': title, 'mode': mode,
            'duration': duration, 'timestamp': timestamp, 'rights': True, 'created_at': time.time(),
            'source_note': str(data.get('source_note', ''))[:2000], 'input_version': 1,
            'use_title': data.get('use_title', True) is True, 'use_position': data.get('use_position', True) is True,
            'labels': CANDIDATES, 'taxonomy_status': '待问卷与试标确定，分类接口尚未接入', 'frames': [],
            'classification_enabled': False,
            'data_kind': 'user_material', 'model_provider':provider})
        return self.get(rid, user)

    def get(self, rid, user):
        folder = self.folder(rid, user)
        meta = read(folder/'input.json')
        return {'input': meta, 'state': read(folder/'state.json', {'status': 'idle'}),
                'subtitles': read(folder/'subtitles.json'), 'segments': read(folder/'segments.json', []),
                'report': read(folder/'report.json'), 'reviews': read(folder/'reviews.json', [])}

    def listing(self, user):
        rows = [read(x) for x in self.directory.glob('*/input.json')]
        return [{'id': x['id'], 'title': x['title'], 'mode': x['mode']} for x in sorted(rows, key=lambda x:x['created_at'], reverse=True) if x['owner']==user['id']]

    def changed(self, folder, meta):
        meta['input_version'] += 1
        save_json(folder/'input.json', meta)
        save_json(folder/'state.json', {'status': 'idle', 'stage': '输入已更新，等待运行'})
        # Keep previous report as a reviewable artifact, explicitly mark stale on retrieval/UI.

    def upload(self, rid, user, kind, stream, length, query):
        folder = self.folder(rid, user)
        meta = read(folder/'input.json')
        limit = 512*1024*1024 if kind=='video' else 20*1024*1024
        if kind not in ('video', 'image', 'subtitles') or not 0<length<=limit:
            raise ValueError('文件类型或大小不符合要求')
        if kind=='video' and meta['mode']!='video':
            raise ValueError('快照任务仅接收原始截图')
        filename = query.get('name', [''])[0]
        temporary = folder/(uuid.uuid4().hex+'.upload')
        try:
            with temporary.open('wb') as target:
                remaining = length
                while remaining:
                    chunk = stream.read(min(262144, remaining))
                    if not chunk:
                        raise ValueError('文件上传未完成')
                    target.write(chunk)
                    remaining -= len(chunk)
            if kind=='video':
                import av
                with av.open(str(temporary)) as source:
                    if not source.streams.video or not source.duration:
                        raise ValueError('视频缺少可识别画面或时长')
                    duration = source.duration/av.time_base
                if meta.get('video'):
                    raise ValueError('替换视频请建立新任务，以保持时间与证据对应')
                temporary.replace(folder/'video.mp4')
                meta.update(video='video.mp4', duration=duration)
            elif kind=='image':
                from PIL import Image
                with Image.open(temporary) as im:
                    if im.format not in ('JPEG', 'PNG') or im.width*im.height>40000000:
                        raise ValueError('请上传不超过4000万像素的PNG或JPEG原始截图')
                    im.verify()
                    extension = '.png' if im.format=='PNG' else '.jpg'
                timestamp = number(query.get('time', [meta['timestamp']])[0], '截图时间')
                if meta['duration'] and timestamp>=meta['duration']:
                    raise ValueError('截图时间须小于视频时长')
                if meta['mode']=='video' and not meta['duration']:
                    raise ValueError('请先上传视频，再关联原始截图')
                fid = 'f'+uuid.uuid4().hex[:12]
                name = fid+extension
                digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
                temporary.replace(folder/name)
                frame = {'id': fid, 'file': name, 'time': timestamp, 'source': 'uploaded_original',
                         'sha256': digest, 'note': '用户上传的原始截图，像素内容未拼接' }
                meta['frames'] = [frame] if meta['mode']=='snapshot' else meta['frames']+[frame]
            else:
                if not meta['duration']:
                    raise ValueError('请先导入视频或完成原生截图采集，以取得真实时长')
                rows, quality = parse_subtitles(temporary.read_text(encoding='utf-8-sig'), filename, meta['duration'])
                save_json(folder/'subtitles.json', {'segments': rows, 'quality': quality, 'source': filename})
                save_json(folder/'segments.json', suggest_boundaries(rows, meta['duration']))
                meta['segmentation_method'] = '字幕章节表达、停顿与最长时长规则候选；待人工复核'
            self.changed(folder, meta)
            return self.get(rid, user)
        finally:
            if temporary.exists():
                temporary.unlink()

    def configure(self, rid, user, data):
        folder = self.folder(rid, user)
        meta = read(folder/'input.json')
        provider=data.get('model_provider',meta.get('model_provider','api'))
        if provider not in ('api','local','campus'):
            raise ValueError('模型运行方式无效')
        if 'segments' in data:
            rows = validate_boundaries(data['segments'], meta['duration'])
            save_json(folder/'segments.json', rows)
            meta['segmentation_method'] = '人工确认或调整的连续片段边界'
        meta.update(model_provider=provider,use_title=data.get('use_title', meta['use_title']) is True,
                    use_position=data.get('use_position', meta['use_position']) is True,
                    classification_enabled=False)
        self.changed(folder, meta)
        return self.get(rid, user)

    def run(self, rid, user):
        """Build reviewable report slots; model interfaces are intentionally unconnected."""
        folder = self.folder(rid, user)
        meta = read(folder/'input.json')
        try:
            subtitles = read(folder/'subtitles.json', {})
            segments = read(folder/'segments.json', [])
            frames = meta['frames']
            if meta['mode']=='video':
                if not subtitles.get('segments') or not meta['duration']:
                    raise ValueError('请先导入视频和带时间戳的B站中文字幕')
                segments = validate_boundaries(segments, meta['duration'])
            if not frames:
                raise ValueError('请先采集或上传原始截图')
            snapshots = []
            for frame in frames:
                snapshots.append({'frame':frame, 'result':{
                    'course_content':{'teaching_content':'', 'teacher_observation':''},
                    'danmaku_summary':'', 'overall_state':{'primary':'待接入', 'secondary':[],
                    'evidence_status':'待人工复核', 'reason':''}, 'evidence':[], 'review_notes':[]},
                    'analysis_status':'模型接口待接入', 'review_status':'待人工复核'})
            reports = [{'segment':segment, 'frame_ids':[f['id'] for f in frames if segment['start']<=f['time']<segment['end']],
                'result':{'overall_content':{'teaching_content':'', 'teacher_observation':''},
                'danmaku_feedback':'', 'evidence_frame_ids':[], 'limitations':[]},
                'analysis_status':'片段聚合接口待接入'} for segment in segments]
            result = {'version':VERSION, 'input_version':meta['input_version'], 'input':meta,
                      'model':None, 'inference':'报告结构已建立，分析内容等待模型接入或人工填写',
                      'taxonomy_status':meta['taxonomy_status'], 'snapshots':snapshots, 'segments':reports,
                      'subtitle_quality':subtitles.get('quality'), 'segmentation_method':meta.get('segmentation_method'),
                      'created_at':time.time(), 'review_status':'待人工复核'}
            save_json(folder/'report.json', result)
            save_json(folder/'state.json', {'status':'prepared', 'stage':'报告框架已建立，可人工填写；模型接口待接入'})
        except Exception as exc:
            save_json(folder/'state.json', {'status':'failed', 'stage':str(exc), 'error_type':type(exc).__name__})

    def review(self, rid, user, data):
        folder = self.folder(rid, user)
        report = read(folder/'report.json')
        meta = read(folder/'input.json')
        if not report or report['input_version']!=meta['input_version']:
            raise ValueError('请先生成与当前输入一致的报告')
        reason = str(data.get('reason','')).strip()
        revised = data.get('revised')
        if not reason or not isinstance(revised,dict):
            raise ValueError('请填写修订原因和修订后的JSON报告')
        original = {'snapshots':report['snapshots'], 'segments':report['segments']}
        if set(revised)!=set(original) or len(json.dumps(revised,ensure_ascii=False))>2000000:
            raise ValueError('修订需保留snapshots与segments两个主体字段，且大小合理')
        for key, identity in [('snapshots','frame'),('segments','segment')]:
            if not isinstance(revised[key],list) or len(revised[key])!=len(original[key]):
                raise ValueError('修订需保留原始快照和片段数量')
            for before,after in zip(original[key],revised[key]):
                if not isinstance(after,dict) or after.get(identity)!=before[identity]:
                    raise ValueError('截图来源和片段边界属于原始依据，请在素材或片段页修改')
                result=after.get('result')
                if not isinstance(result,dict) or set(result)!=set(before['result']):
                    raise ValueError('修订需保留报告字段结构')
                def same_shape(value,template):
                    if type(value) is not type(template):
                        raise ValueError('修订字段类型与报告约定不一致')
                    if isinstance(template,dict):
                        if set(value)!=set(template):raise ValueError('修订字段结构不完整')
                        for k,v in template.items():same_shape(value[k],v)
                same_shape(result,before['result'])
                if key=='segments' and not set(result['evidence_frame_ids'])<=set(before['frame_ids']):
                    raise ValueError('片段报告只能引用本片段关键帧')
        records = read(folder/'reviews.json', [])
        records.append({'id':uuid.uuid4().hex, 'time':time.time(), 'reviewer':user['id'], 'reason':reason[:5000],
                        'report_created_at':report['created_at'], 'input_version':meta['input_version'],
                        'original':original, 'revised':revised, 'status':'待裁决，尚未纳入训练集'})
        save_json(folder/'reviews.json', records)
        return {'saved':True,'review_count':len(records)}

    def export(self, rid, user):
        value = self.get(rid,user)
        if not value['report']:
            raise ValueError('请先生成报告')
        value['stale'] = value['report']['input_version']!=value['input']['input_version']
        value['dataset_status'] = '研究记录导出；人工修订待裁决，尚未作为训练真值'
        return value

    def capture(self, rid, user, data):
        """Accept a cropped native browser screenshot, never redraw its contents."""
        import base64
        import io
        from PIL import Image
        from acquire_course import normalize_url
        folder = self.folder(rid, user)
        meta = read(folder/'input.json')
        source_url = normalize_url(data.get('source_url', ''))
        if source_url != meta.get('source_url'):
            raise ValueError('采集页面与本任务B站课程或分P不一致')
        timestamp = number(data.get('actual_time'), '实际播放时间')
        duration = number(data.get('duration'), '视频时长', .001)
        if timestamp>=duration or (meta['duration'] and abs(meta['duration']-duration)>3):
            raise ValueError('播放器时间与任务时长不一致')
        if data.get('subtitle_stable') is not True or not str(data.get('subtitle_text','')).strip():
            raise ValueError('中文字幕尚未稳定显示，本次截图不入库')
        if data.get('native_player') is not True:
            raise ValueError('本任务仅接收原生B站播放器截图')
        raw = base64.b64decode(data.get('png_base64',''), validate=True)
        if not 0<len(raw)<=20*1024*1024:
            raise ValueError('截图大小无效')
        with Image.open(io.BytesIO(raw)) as im:
            if im.format!='PNG' or im.width*im.height>40000000:
                raise ValueError('截图须为有效PNG')
            im.verify()
        fid = 'f'+uuid.uuid4().hex[:12]
        name = fid+'.png'
        (folder/name).write_bytes(raw)
        audit = {key:data.get(key) for key in ('source_url','requested_time','actual_time','duration',
            'subtitle_text','subtitle_stable','danmaku_dom_count','danmaku_canvas_present','danmaku_enabled_confirmed',
            'player_rect','viewport','captured_at','warmup_seconds','native_player')}
        frame = {'id':fid,'file':name,'time':timestamp,'source':'bilibili_native_capture',
                 'sha256':hashlib.sha256(raw).hexdigest(),'note':'B站网页实际显示区域截图，仅裁切播放器边界',
                 'capture':audit,'review_status':'需人工核对弹幕可见性、遮挡及字幕正确性'}
        meta['frames'] = [frame] if meta['mode']=='snapshot' else meta['frames']+[frame]
        meta['duration'] = duration
        self.changed(folder,meta)
        return {'saved':True,'frame_id':fid}


def export_html(data):
    report = data['report']
    latest=next((r for r in reversed(data['reviews']) if r['report_created_at']==report['created_at']),None)
    content=latest['revised'] if latest else report
    sections = []
    for snapshot in content['snapshots']:
        value = snapshot['result']
        sections.append('<h2>快照 '+html.escape(snapshot['frame']['id'])+'</h2><p>时间 '+str(snapshot['frame']['time'])+' 秒</p>'+
            '<h3>课程内容总结分析</h3><p>'+html.escape(value['course_content']['teaching_content'])+'</p><p>'+html.escape(value['course_content']['teacher_observation'])+'</p>'+
            '<h3>弹幕内容总结分析</h3><p>'+html.escape(value['danmaku_summary'])+'</p><h3>整体状态结果</h3><p>'+html.escape(value['overall_state']['primary']+'：'+value['overall_state']['reason'])+'</p>')
    for segment in content['segments']:
        sections.append('<h2>'+html.escape(segment['segment']['title'])+'</h2><pre>'+html.escape(json.dumps(segment['result'],ensure_ascii=False,indent=2))+'</pre>')
    return '<!doctype html><meta charset="utf-8"><title>课镜研究报告</title><style>body{max-width:900px;margin:40px auto;font:17px/1.8 sans-serif}pre{white-space:pre-wrap}p{text-indent:2em}</style><h1>'+html.escape(data['input']['title'])+'</h1><p>'+html.escape(report['inference']+'；'+report['taxonomy_status'])+'</p><p>'+('输入已更新：下列为历史报告' if data['stale'] else '结果待人工复核')+'</p>'+''.join(sections)+'<h2>完整记录与人工修订</h2><pre>'+html.escape(json.dumps(data,ensure_ascii=False,indent=2))+'</pre>'
