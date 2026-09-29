'use strict';
const $=id=>document.getElementById(id);
function show(value){$('status').textContent=value}
async function status(){const value=await chrome.storage.session.get('captureStatus');if(value.captureStatus)show(value.captureStatus)}
$('start').onclick=async()=>{try{
  if(!$('confirmed').checked)throw Error('请先在B站播放器开启中文字幕和弹幕。');
  const config=JSON.parse($('config').value), times=$('times').value.split(/[,，\s]+/).filter(Boolean).map(Number);
  if(!times.length||times.length>30||times.some(x=>!Number.isFinite(x)||x<0))throw Error('请填写1至30个有效时间。');
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const response=await chrome.runtime.sendMessage({action:'start',tabId:tab.id,config,times:[...new Set(times)].sort((a,b)=>a-b),selector:$('selector').value});
  if(response.error)throw Error(response.error);
  show('采集已开始。可关闭此弹窗，保持B站页面前台显示；重新打开可查看进度。');
}catch(e){show(e.message)}};
$('stop').onclick=async()=>{await chrome.runtime.sendMessage({action:'stop'});show('已请求停止，当前截图处理结束后停止。')};
status();setInterval(status,1200);
