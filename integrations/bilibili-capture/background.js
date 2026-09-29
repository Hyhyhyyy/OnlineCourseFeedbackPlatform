let running=false, stopped=false;
const update=async text=>chrome.storage.session.set({captureStatus:text});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function canonical(raw){const u=new URL(raw);if(u.protocol!=='https:'||u.hostname!=='www.bilibili.com'||!/^\/video\/BV[\w]+\/?$/.test(u.pathname))throw Error('请打开B站BV视频页面');return u.pathname.replace(/\/$/,'')+'?p='+(u.searchParams.get('p')||'1')}

// Runs inside the native page. It changes playback time only; it never redraws subtitles or danmaku.
async function prepareFrame(requested,selector){
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const video=[...document.querySelectorAll('video')].find(v=>v.getBoundingClientRect().width>200);
  if(!video||!Number.isFinite(video.duration)||requested>=video.duration)throw Error('播放器尚未就绪或时间超出视频');
  const before={muted:video.muted,rate:video.playbackRate};
  try{
    video.scrollIntoView({block:'center',inline:'center'});
    video.muted=true;video.playbackRate=1;
    const warmup=Math.min(2,requested);
    video.currentTime=Math.max(0,requested-warmup);
    const begin=Date.now();
    while(video.seeking||video.readyState<2){if(Date.now()-begin>10000)throw Error('视频定位超时');await sleep(100)}
    if(warmup>0){
      await video.play();
      while(video.currentTime<requested){if(Date.now()-begin>16000)throw Error('视频预播放超时');await sleep(35)}
    }
    video.pause();
    if(Math.abs(video.currentTime-requested)>.5)throw Error('实际播放位置偏差超过0.5秒，请重试');
    const visibleText=()=>[...document.querySelectorAll(selector)].filter(e=>{
      const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&Number(s.opacity)!==0;
    }).map(e=>e.textContent.trim()).filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).join('\n');
    let last='', stable=0, subtitle='';
    for(let i=0;i<30;i++){
      subtitle=visibleText();
      if(subtitle&&/[\u3400-\u9fff]/.test(subtitle)&&subtitle===last)stable+=200;else stable=0;
      if(stable>=1000)break;
      last=subtitle;await sleep(200);
    }
    if(stable<1000)throw Error('未检测到稳定中文字幕；请检查字幕开关、语言和当前时刻是否有字幕');
    const r=video.getBoundingClientRect();
    if(r.x<0||r.y<0||r.right>innerWidth||r.bottom>innerHeight)throw Error('播放器未完整显示，请缩小网页或调整窗口');
    const middle=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    if(middle?.closest('[role="dialog"], .bili-mini-mask, .bili-mini-content'))throw Error('播放器被弹窗遮挡，请关闭后重试');
    return {requested_time:requested,actual_time:video.currentTime,duration:video.duration,
      source_url:location.href,subtitle_text:subtitle,subtitle_stable:true,native_player:true,
      player_rect:{x:r.x,y:r.y,width:r.width,height:r.height},viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
      danmaku_dom_count:document.querySelectorAll('.bili-danmaku-x-dm, .b-danmaku').length,
      danmaku_canvas_present:!!document.querySelector('.bpx-player-row-dm-wrap canvas, .bili-danmaku canvas'),
      danmaku_enabled_confirmed:true,warmup_seconds:warmup,captured_at:new Date().toISOString()};
  }finally{video.pause();video.muted=before.muted;video.playbackRate=before.rate}
}

async function captureJob(message){
  const {config,tabId,times,selector}=message;let saved=0;const failures=[];
  try{
    for(const requested of times){
      if(stopped)break;
      await update(`定位 ${requested} 秒；已保存 ${saved}/${times.length} 张`);
      try{
        const tab=await chrome.tabs.get(tabId),window=await chrome.windows.get(tab.windowId);
        if(!tab.active||!window.focused)throw Error('请保持B站标签页及其浏览器窗口在前台');
        if(canonical(tab.url)!==canonical(config.source_url))throw Error('当前B站视频或分P与工作台任务不一致');
        const [{result:meta}]=await chrome.scripting.executeScript({target:{tabId},func:prepareFrame,args:[requested,selector]});
        if(stopped)break;
        const again=await chrome.tabs.get(tabId);
        const activeWindow=await chrome.windows.get(again.windowId);
        if(!again.active||!activeWindow.focused||canonical(again.url)!==canonical(config.source_url))throw Error('采集期间页面已切换，本次截图取消');
        const screenshot=await chrome.tabs.captureVisibleTab(again.windowId,{format:'png'});
        const [{result:after}]=await chrome.scripting.executeScript({target:{tabId},func:()=>{
          const v=[...document.querySelectorAll('video')].find(x=>x.getBoundingClientRect().width>200);return v?{time:v.currentTime,paused:v.paused}:null;
        }});
        if(!after?.paused||Math.abs(after.time-meta.actual_time)>.05)throw Error('截屏时播放器时间发生变化，请重试');
        const bitmap=await createImageBitmap(await (await fetch(screenshot)).blob());
        const sx=bitmap.width/meta.viewport.width,sy=bitmap.height/meta.viewport.height,r=meta.player_rect;
        const x=Math.round(r.x*sx),y=Math.round(r.y*sy),w=Math.round(r.width*sx),h=Math.round(r.height*sy);
        const canvas=new OffscreenCanvas(w,h);
        canvas.getContext('2d').drawImage(bitmap,x,y,w,h,0,0,w,h);bitmap.close();
        const bytes=new Uint8Array(await (await canvas.convertToBlob({type:'image/png'})).arrayBuffer());
        let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));
        const response=await fetch(config.endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-Capture-Token':config.token},body:JSON.stringify({...meta,png_base64:btoa(raw)})});
        const value=await response.json();if(!response.ok)throw Error(value.error||'本地保存失败');
        saved++;await update(`已保存 ${saved}/${times.length} 张；实际时间 ${meta.actual_time.toFixed(2)} 秒`);
        await pause(600);
      }catch(e){failures.push(`${requested}秒：${e.message}`);await update(`跳过 ${requested} 秒：${e.message}`)}
    }
    await update(`${stopped?'已停止':'采集结束'}：保存 ${saved} 张。\n${failures.join('\n')}\n请在工作台逐张复核字幕、弹幕与遮挡情况。`);
    await chrome.storage.session.set({lastCaptureRun:{saved,failures,task_id:config.task_id,time:new Date().toISOString()}});
  }catch(e){await update(e.message)}finally{running=false}
}

chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id!==chrome.runtime.id)return;
  if(message.action==='stop'){stopped=true;reply({ok:true});return}
  if(message.action==='start'){
    try{
      if(running)throw Error('已有采集任务进行中');
      const u=new URL(message.config.endpoint);
      if(u.protocol!=='http:'||u.hostname!=='127.0.0.1'||u.pathname!=='/api/capture')throw Error('仅允许保存至本机课镜采集接口');
      canonical(message.config.source_url);
      if(typeof message.config.token!=='string'||message.config.token.length<30)throw Error('配对配置无效');
      if(!Array.isArray(message.times)||!message.times.length||message.times.length>30||message.times.some(x=>!Number.isFinite(x)||x<0))throw Error('采集时间无效');
      running=true;stopped=false;captureJob(message);reply({ok:true});
    }catch(e){reply({error:e.message})}
  }
});
