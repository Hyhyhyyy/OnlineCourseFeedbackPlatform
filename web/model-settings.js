'use strict';
(() => {
  const el = id => document.getElementById(id);
  const names = {api:'通用大模型 API', local:'本地模型', campus:'校园服务器'};
  let saved = null, profiles = {}, busy = false;
  let provider = 'api';
  try { provider = localStorage.getItem('course-inference-provider') || 'api'; } catch (_) {}
  if (!names[provider]) provider = 'api';
  function mode(value) {
    provider = names[value] ? value : 'api';
    el('inferenceProvider').value = provider;
    el('modelChoiceLabel').textContent = names[provider];
    for (const key of Object.keys(names)) el(key+'Fields').hidden = provider !== key;
    el('connectionStatus').textContent = '所选运行方式：'+names[provider]+' · 点击检查连接状态';
    try { localStorage.setItem('course-inference-provider', provider); } catch (_) {}
  }
  window.selectedModelProvider = () => provider;
  window.syncTaskModelProvider = mode;
  function docs() {
    const preset = saved?.presets[el('apiVendor').value];
    el('apiDocs').hidden = !preset?.docs;
    if (preset?.docs) el('apiDocs').href = preset.docs;
    else el('apiDocs').removeAttribute('href');
  }
  function display(state, fill = false) {
    saved = state;
    if (fill) {
      el('apiVendor').replaceChildren(...Object.entries(state.presets).map(([value,preset]) => new Option(preset.name,value)));
      el('apiVendor').value = state.vendor || 'bailian';
      el('apiUrl').value = state.url || state.presets[el('apiVendor').value].url;
      el('apiModel').value = state.model;
      el('apiFormat').value = state.format || 'json_object';
    }
    el('apiModels').replaceChildren(new Option('选择模型后点击“应用模型 ID”',''), ...state.models.map(id => new Option(id,id)));
    el('apiModels').value = state.models.includes(state.model) ? state.model : '';
    el('apiState').textContent = state.key_status;
    el('apiUsage').textContent = `本次连接已记录调用 ${state.requests} 次；输入 ${state.input_tokens}、输出 ${state.output_tokens} Token（计费文本单位）；${state.missing_usage} 次未返回完整用量。`;
    el('apiBalance').textContent = '账户余额：'+state.balance;
    el('apiKey').placeholder = state.key_present ? 'Key已保存在本次服务内存；修改连接时请重新填写' : '仅保存于本次服务内存';
    docs();
  }
  function requireSaved(model = false) {
    if (!saved?.configured) throw Error('请先填写并保存本次连接。');
    if (el('apiKey').value || el('apiVendor').value !== saved.vendor || el('apiUrl').value.trim().replace(/\/+$/,'') !== saved.url || el('apiFormat').value !== saved.format)
      throw Error('连接设置已修改，请先保存本次连接。');
    if (model && el('apiModel').value.trim() !== saved.model) throw Error('模型 ID 已修改，请先应用模型 ID。');
  }
  async function check() {
    const requested = provider;
    const state = await api('/api/model/readiness?provider='+requested);
    if (requested === provider) el('connectionStatus').textContent = names[requested]+'：'+state.reason+(state.model?' · '+state.model:'')+'。研究分析接口待接入。';
  }
  function action(id, fn) {
    el(id).onclick = async () => {
      if (busy) return;
      busy = true;
      const controls = [...el('modelDialog').querySelectorAll('button,input,select')];
      controls.forEach(node => node.disabled = true);
      el('modelMessage').textContent = '正在处理…';
      try { await fn(); }
      catch (error) { el('modelMessage').textContent = error.message; }
      finally { busy = false; controls.forEach(node => node.disabled = false); }
    };
  }
  el('openModels').onclick = async () => {
    if (busy) return;
    el('modelDialog').showModal();
    el('modelMessage').textContent = '正在读取本机设置…';
    try {
      const values = await Promise.all([api('/api/model/settings'),api('/api/model/profiles')]);
      display(values[0],true); profiles = values[1];
      for (const key of ['local','campus']) {
        const p = profiles[key];
        el(key+'Profile').textContent = p.configured ? `${p.model} · ${p.url}` : '尚未配置服务地址与模型 ID。';
      }
      el('modelMessage').textContent = '';
    } catch (error) { el('modelMessage').textContent = error.message; }
  };
  el('closeModels').onclick = () => el('modelDialog').close();
  el('modelDialog').addEventListener('close',() => { el('apiKey').value = ''; });
  el('modelDialog').addEventListener('cancel',event => { if (busy) event.preventDefault(); });
  el('inferenceProvider').onchange = () => { mode(el('inferenceProvider').value); el('modelMessage').textContent = '此选择用于新任务；已有任务请点击“将运行方式保存到当前任务”。'; };
  el('apiVendor').onchange = () => {
    el('apiUrl').value = saved?.presets[el('apiVendor').value]?.url || '';
    el('apiKey').value = ''; el('apiModel').value = '';
    el('apiModels').replaceChildren(new Option('保存新连接后刷新模型列表','')); docs();
  };
  el('apiModels').onchange = () => { if (el('apiModels').value) el('apiModel').value = el('apiModels').value; };
  action('saveApi',async () => {
    const state = await api('/api/model/configure',{vendor:el('apiVendor').value,url:el('apiUrl').value,key:el('apiKey').value,model:el('apiModel').value,format:el('apiFormat').value});
    el('apiKey').value = ''; display(state,true); await check();
    el('modelMessage').textContent = '连接已保存到本次服务内存。可刷新模型列表并验证所选模型。';
  });
  action('selectModel',async () => { requireSaved(); display(await api('/api/model/select',{model:el('apiModel').value}),true); await check(); el('modelMessage').textContent = '模型 ID 已应用，可发起连接验证。'; });
  for (const [id,path,message,needsModel] of [
    ['fetchModels','models','模型列表已更新。',false],
    ['probeText','probe','文本测试调用完成。',true],
    ['probeVision','probe-vision','图文测试调用完成。',true],
    ['queryBalance','balance','余额查询完成。',false]
  ]) action(id,async () => {
    requireSaved(needsModel);
    try { display(await api('/api/model/'+path,{})); el('modelMessage').textContent = message; }
    finally { display(await api('/api/model/settings')); await check(); }
  });
  action('clearKey',async () => { display(await api('/api/model/clear',{}),true); el('apiKey').value = ''; await check(); el('modelMessage').textContent = '本次连接的 Key 已清除。'; });
  action('checkConnection',async () => { await check(); el('modelMessage').textContent = '连接状态已更新。'; });
  action('applyTaskProvider',async () => {
    if (!selected) throw Error('当前选择已用于新任务。若需修改已有任务，请先打开该任务。');
    const id = selected;
    await api('/api/task/'+id+'/configure',{model_provider:provider});
    await openTask(id);
    el('modelMessage').textContent = '运行方式已保存到当前任务；研究分析接口待接入。';
  });
  mode(provider);
})();
