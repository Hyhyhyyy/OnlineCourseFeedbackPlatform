'use strict';
const $=id=>document.getElementById(id);let current=null,selected=null,poll=null,reviewDraft=null;
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function notify(text){$('notice').textContent=text||''}
async function api(path,data){const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':window.APP_TOKEN},body:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw Error(value.error||'请求失败');return value}
const taskPath=action=>'/api/task/'+selected+(action?'/'+action:'');
function bind(id,fn){$(id).onclick=async()=>{const b=$(id);b.disabled=true;notify('');try{await fn()}catch(e){notify(e.message)}finally{b.disabled=false}}}
function stamp(t){return Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0')}
function showTab(id){for(const p of document.querySelectorAll('.tab-panel'))p.hidden=p.id!==id;for(const b of document.querySelectorAll('#tabs button'))b.classList.toggle('selected',b.dataset.tab===id)}
for(const b of document.querySelectorAll('#tabs button'))b.onclick=()=>showTab(b.dataset.tab);
async function listing(){const data=await api('/api/tasks');$('taskList').replaceChildren();for(const task of data.tasks){const b=element('button',task.title,'task-link'+(task.id===selected?' active':''));b.onclick=()=>openTask(task.id).catch(e=>notify(e.message));$('taskList').append(b)}}
async function openTask(id){selected=id;current=await api(taskPath());render();await listing();if(poll)clearTimeout(poll);if(current.state.status==='running')poll=setTimeout(()=>openTask(id).catch(e=>notify(e.message)),2000)}
function metric(label,value){const e=element('div',undefined,'metric');e.append(element('span',label),element('strong',value));return e}
function addReport(parent,title,value){const e=element('div',undefined,'report-block');e.append(element('h3',title),element('p',value||'等待模型接入或人工填写',value?'':'empty'));parent.append(e)}
function render(){
  const m=current.input;$('createPanel').hidden=true;$('workspace').hidden=false;$('pageTitle').textContent=m.title;$('taskStatus').textContent=current.state.stage||'已建立任务，等待素材';
  $('sourceLink').replaceChildren();if(m.source_url){const a=element('a','打开B站原视频');a.href=m.source_url;a.target='_blank';a.rel='noreferrer';$('sourceLink').append(a)}else $('sourceLink').textContent='本地导入任务';
  $('subtitleQuality').replaceChildren();const q=current.subtitles?.quality;if(q)$('subtitleQuality').append(metric('字幕条数',q.count),metric('时间覆盖率',(q.coverage*100).toFixed(1)+'%'),metric('重叠条数',q.overlap_count));else $('subtitleQuality').append(element('p','导入中文字幕后显示检查结果。','muted'));
  $('subtitleSource').textContent=current.subtitles?'来源：'+current.subtitles.source+' · '+(current.subtitles.language||'导入字幕')+' · 时间覆盖率用于检查完整性，字幕内容需结合原视频核对。':'';
  $('subtitlePreview').replaceChildren();for(const row of (current.subtitles?.segments||[]).slice(0,80)){const e=element('div',undefined,'subtitle-row');e.append(element('span',stamp(row.start)+'–'+stamp(row.end)),element('span',row.text));$('subtitlePreview').append(e)}
  $('frameGallery').replaceChildren();for(const frame of m.frames){const card=element('article',undefined,'frame-card'),img=element('img');img.src=taskPath('asset')+'?name='+encodeURIComponent(frame.file);img.alt=m.title+' '+stamp(frame.time)+'原始快照';img.onclick=()=>window.open(img.src,'_blank');const box=element('div');box.append(element('strong',stamp(frame.time)+' · '+(frame.source==='bilibili_native_capture'?'B站原生截图':'上传原始截图')),element('p',frame.note));if(frame.capture)box.append(element('p','实际时间 '+frame.time.toFixed(3)+' 秒；中文字幕：'+frame.capture.subtitle_text));box.append(element('p','待核对：弹幕可见性、字幕正确性与遮挡情况','muted'));card.append(img,box);$('frameGallery').append(card)}
  if(!m.frames.length)$('frameGallery').append(element('p','还没有原始快照。完成采集后点击“刷新记录”，或导入已保存截图。','muted'));
  const videoUrl=m.video?taskPath('asset')+'?name='+encodeURIComponent(m.video):'';if($('player').getAttribute('src')!==videoUrl){if(videoUrl)$('player').src=videoUrl;else $('player').removeAttribute('src')}$('player').hidden=!videoUrl;
  $('segmentList').replaceChildren();for(const segment of current.segments){const row=element('div',undefined,'segment-row'),b=element('button',stamp(segment.start)+'–'+stamp(segment.end),'quiet');b.onclick=()=>{$('player').currentTime=segment.start};row.append(b,element('strong',' '+segment.title),element('p',m.frames.filter(f=>f.time>=segment.start&&f.time<segment.end).length+' 张关联快照','muted'));$('segmentList').append(row)}
  $('segmentEditor').value=JSON.stringify(current.segments.map(({start,end,title})=>({start,end,title})),null,2);
  const report=current.report;$('reportView').replaceChildren();$('jsonExport').href=taskPath('export');$('htmlExport').href=taskPath('html');$('jsonExport').hidden=$('htmlExport').hidden=!report;
  $('reportStatus').textContent=report?(report.input_version===m.input_version?'报告框架与当前输入一致；自动分析接口待接入。':'输入已更新，下面是历史报告，请更新报告框架。'):'先完成素材与快照，再建立报告框架。';
  if(report){
    const latest=[...current.reviews].reverse().find(x=>x.report_created_at===report.created_at);
    const shown=latest?.revised||report;
    if(latest)$('reportStatus').textContent+=' 下方显示最新人工修订，原始报告保留在导出记录中。';
    for(const snapshot of shown.snapshots){const r=snapshot.result;$('reportView').append(element('h3','快照 '+stamp(snapshot.frame.time)));addReport($('reportView'),'课程内容总结分析 · 授课内容',r.course_content.teaching_content);addReport($('reportView'),'课程内容总结分析 · 教师表现',r.course_content.teacher_observation);addReport($('reportView'),'弹幕内容总结分析',r.danmaku_summary);addReport($('reportView'),'弹幕整体状态',r.overall_state.primary==='待接入'?'':r.overall_state.primary)}
    for(const segment of shown.segments){$('reportView').append(element('h3','片段 · '+segment.segment.title));addReport($('reportView'),'整体内容总结',segment.result.overall_content.teaching_content);addReport($('reportView'),'弹幕反馈情况',segment.result.danmaku_feedback)}
    const recent=[...current.reviews].reverse().find(x=>x.report_created_at===report.created_at);
    $('reviewEditor').value=JSON.stringify(recent?.revised||{snapshots:report.snapshots,segments:report.segments},null,2);
  }else $('reviewEditor').value='';
  if(window.syncTaskModelProvider)window.syncTaskModelProvider(m.model_provider||'api');
  renderReviewForm();
  $('reviewHistory').replaceChildren();for(const review of [...current.reviews].reverse())$('reviewHistory').append(element('p',new Date(review.time*1000).toLocaleString()+' · '+review.reason+' · '+review.status));
}
function renderReviewForm(){
  $('reviewForm').replaceChildren();
  if(!current.report){reviewDraft=null;return}
  reviewDraft=JSON.parse($('reviewEditor').value);
  function field(parent,labelText,object,key){const label=element('label',labelText),area=element('textarea');area.value=object[key];area.oninput=()=>{object[key]=area.value;$('reviewEditor').value=JSON.stringify(reviewDraft,null,2)};label.append(area);parent.append(label)}
  for(const snapshot of reviewDraft.snapshots){
    const block=element('section',undefined,'report-block');block.append(element('h3','填写快照 '+stamp(snapshot.frame.time)));
    const image=element('img');image.src=taskPath('asset')+'?name='+encodeURIComponent(snapshot.frame.file);image.alt='复核原始快照';image.className='review-image';block.append(image);
    field(block,'授课内容',snapshot.result.course_content,'teaching_content');field(block,'可观察的教师表现',snapshot.result.course_content,'teacher_observation');field(block,'弹幕整体内容总结',snapshot.result,'danmaku_summary');field(block,'状态相关观察与依据（分类标准待确定）',snapshot.result.overall_state,'reason');$('reviewForm').append(block);
  }
  for(const segment of reviewDraft.segments){const block=element('section',undefined,'report-block');block.append(element('h3','填写片段 · '+segment.segment.title));field(block,'片段授课内容总结',segment.result.overall_content,'teaching_content');field(block,'片段教师表现',segment.result.overall_content,'teacher_observation');field(block,'片段弹幕反馈情况',segment.result,'danmaku_feedback');$('reviewForm').append(block)}
}
bind('newTask',()=>{selected=null;current=null;if(poll)clearTimeout(poll);$('createPanel').hidden=false;$('workspace').hidden=true;$('pageTitle').textContent='从原始画面开始复盘';return listing()});
bind('create',async()=>{const d=await api('/api/tasks',{title:$('title').value,mode:$('mode').value,source_url:$('sourceUrl').value,source_note:$('sourceNote').value,duration:Number($('duration').value),timestamp:Number($('timestamp').value),rights:$('rights').checked,model_provider:window.selectedModelProvider?window.selectedModelProvider():'api'});await openTask(d.input.id)});
bind('refresh',()=>openTask(selected));
async function upload(kind,input,extra=''){if(!selected)throw Error('请先建立任务');const file=$(input).files[0];if(!file)throw Error('请选择文件');notify('正在导入文件…');const r=await fetch(taskPath('upload')+'?kind='+kind+'&name='+encodeURIComponent(file.name)+extra,{method:'POST',headers:{'X-Demo-Token':window.APP_TOKEN},body:file});const data=await r.json();if(!r.ok)throw Error(data.error);await openTask(selected);notify('文件已导入。')}
bind('uploadVideo',()=>upload('video','videoFile'));bind('uploadSubtitles',()=>upload('subtitles','subtitleFile'));bind('uploadImage',()=>upload('image','imageFile','&time='+encodeURIComponent($('imageTime').value)));
bind('saveSegments',async()=>{await api(taskPath('configure'),{segments:JSON.parse($('segmentEditor').value)});await openTask(selected);notify('片段边界已保存。')});
bind('prepare',async()=>{await api(taskPath('prepare'),{});await openTask(selected)});
bind('saveReview',async()=>{await api(taskPath('review'),{reason:$('reviewReason').value,revised:JSON.parse($('reviewEditor').value)});await openTask(selected);notify('修订已独立保存，等待裁决。')});
bind('pair',async()=>{$('pairConfig').value=JSON.stringify(await api(taskPath('pair'),{}),null,2)});
bind('copyPair',async()=>{if(!$('pairConfig').value)throw Error('请先生成配置');await navigator.clipboard.writeText($('pairConfig').value);notify('配置已复制，请粘贴到B站页面的课镜采集扩展。')});
bind('acquire',async()=>{await api(taskPath('acquire'),{});await openTask(selected)});
bind('login',async()=>{const d=await api('/api/login/start',{});$('loginPanel').hidden=false;$('qr').src=d.qr_image;$('loginStatus').textContent=d.message});
bind('pollLogin',async()=>{const d=await api('/api/login/poll',{});$('loginStatus').textContent=d.message;if(d.logged_in)$('qr').hidden=true});
listing().catch(e=>notify(e.message));
