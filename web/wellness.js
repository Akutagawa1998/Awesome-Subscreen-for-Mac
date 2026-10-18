'use strict';
(() => {
  let visible=currentPage===PAGE.todo, todo=null, health=null, todoBusy=false, healthBusy=false;
  let lastTodo=0, lastHealth=0, selectedList=localStorage.getItem('wokyis-todo-list')||'', completed=false;
  let reminders;
  try {reminders=JSON.parse(localStorage.getItem('wokyis-reminders-v1'))||{};} catch {reminders={};}
  for(const [key,minutes] of [['stand',60],['water',45]]) {
    if(!reminders[key] || !Number.isFinite(reminders[key].due))reminders[key]={minutes,due:Date.now()+minutes*60000,enabled:true};
  }
  const persist=()=>localStorage.setItem('wokyis-reminders-v1',JSON.stringify(reminders));
  const announced=new Set();
  persist();
  const age=at=>Math.max(0,(Date.now()-new Date(at).getTime())/60000);
  function ago(at){const m=age(at);return m<1?'刚刚':m<60?`${Math.floor(m)} 分钟前`:m<1440?`${Math.floor(m/60)} 小时前`:`${Math.floor(m/1440)} 天前`;}
  function renderTodo(){
    if(!todo){$('#todo-content').innerHTML='<div class="empty"><p>正在读取待办…</p></div>';return;}
    if(!todo.connected){
      $('#todo-content').innerHTML='<div class="empty connect-empty"><span class="terminal-mark">[ ]</span><h2>把今天放在这里</h2><p>连接 Microsoft To Do<br>查看清单，随手完成。</p><button class="primary" data-wellness-settings>连接 Microsoft</button></div>';return;
    }
    if(selectedList&&!(todo.lists||[]).some(l=>l.id===selectedList)){selectedList='';localStorage.removeItem('wokyis-todo-list');}
    const tasks=(todo.tasks||[]).filter(t=>(!selectedList||t.listId===selectedList)&&(completed||t.status!=='completed'));
    tasks.sort((a,b)=>(a.status==='completed')-(b.status==='completed')||(b.importance==='high')-(a.importance==='high')||(a.due?.dateTime||'z').localeCompare(b.due?.dateTime||'z'));
    $('#todo-content').innerHTML=`<div class="todo-filters"><label class="sr-only" for="todo-list-select">任务列表</label><select id="todo-list-select"><option value="">所有清单</option>${(todo.lists||[]).map(l=>`<option value="${esc(l.id)}" ${selectedList===l.id?'selected':''}>${esc(l.name)}</option>`).join('')}</select><button class="soft small" id="todo-completed" aria-pressed="${completed}">${completed?'收起已完成':'已完成'}</button></div>
      ${todo.errors?.length?`<div class="notice warn">${todo.stale?'旧数据 · ':''}${esc(todo.errors[0])}</div>`:''}
      <div class="todo-count">${tasks.filter(t=>t.status!=='completed').length} 项待办 <span>${todo.stale?'同步中断':todo.errors?.length?'部分同步':'已同步'}</span></div>
      ${tasks.length?tasks.map(t=>`<article class="todo-row ${t.status==='completed'?'done':''}"><button class="todo-check" data-task="${esc(t.id)}" data-list="${esc(t.listId)}" data-done="${t.status==='completed'}" aria-label="${t.status==='completed'?'恢复':'完成'} ${esc(t.title)}">${t.status==='completed'?'✓':''}</button><div><h3>${esc(t.title)}</h3><p>${t.importance==='high'?'<span class="important">重要 · </span>':''}${esc(t.listName)}${t.due?` · ${esc(t.due.dateTime.slice(0,10))}`:''}</p></div></article>`).join(''):todo.errors?.length?'<div class="empty"><h2>还有清单未同步</h2><p>连接恢复后，再查看待办。</p></div>':'<div class="empty"><span class="terminal-mark">[✓]</span><h2>清单已清空</h2><p>留一点空间给自己。</p></div>'}`;
    $('#todo-list-select').addEventListener('change',e=>{selectedList=e.target.value;localStorage.setItem('wokyis-todo-list',selectedList);renderTodo();});
    $('#todo-completed').addEventListener('click',()=>{completed=!completed;renderTodo();});
  }
  async function loadTodo(force=false){
    if(todoBusy)return;todoBusy=true;$('#refresh-todo').disabled=true;
    try {todo=await api('/api/todo'+(force?'?refresh=1':''));lastTodo=Date.now();renderTodo();}
    catch(e){if(todo?.connected){todo={...todo,stale:true,errors:[e.message]};renderTodo();}else $('#todo-content').innerHTML=`<div class="empty"><h2>同步暂时中断</h2><p>${esc(e.message)}</p><button class="soft" data-wellness-settings>检查连接</button></div>`;}
    finally{todoBusy=false;$('#refresh-todo').disabled=false;}
  }
  $('#todo-content').addEventListener('click',e=>{
    const b=e.target.closest('[data-task]');if(!b)return;
    perform(b,async()=>{
      const done=b.dataset.done!=='true';await api('/api/todo/complete',{id:b.dataset.task,listId:b.dataset.list,completed:done});
      const task=todo.tasks.find(t=>t.id===b.dataset.task&&t.listId===b.dataset.list);if(task)task.status=done?'completed':'notStarted';
      renderTodo();toast(done?'已完成；在「已完成」中可恢复':'已恢复待办');
    });
  });
  function renderHealth(){
    const m=health?.metrics||{}, heart=m.heartRate, fresh=heart&&age(heart.at)<5;
    $('#health-live').textContent=health?.error?'同步中断':heart?(fresh?'最近采样':'等待新采样'):health?.connected?'已连接':'未连接';
    $('#health-live').classList.toggle('fresh',!!fresh&&!health?.error);
    const number=(metric,digits=0)=>metric?Number(metric.value).toFixed(digits):'—';
    $('#health-content').innerHTML=`<div class="heart-reading ${fresh?'fresh':''}"><div><span class="metric-caption">心率</span><div class="heart-number">${number(heart)}<small>bpm</small></div></div><span class="heart-glyph" aria-hidden="true">♡</span></div>
      <div class="sample-time">${heart?`${ago(heart.at)} · ${health.mode==='import'?'导入快照':esc(heart.source||'Apple Health')}`:'等待 iPhone 同步'}${health?.error?` · ${esc(health.error)}`:''}${health?.warnings?.length?` · ${esc(health.warnings[0])}`:''}</div>
      <div class="recovery-heading">恢复参考</div><div class="recovery-grid"><div><span>HRV</span><strong>${number(m.hrv)}<small>ms</small></strong><em>${m.hrv?ago(m.hrv.at)+' · '+esc(m.hrv.source||'Apple Health'):'暂无数据'}</em></div><div><span>静息心率</span><strong>${number(m.restingHeartRate)}<small>bpm</small></strong><em>${m.restingHeartRate?ago(m.restingHeartRate.at)+' · '+esc(m.restingHeartRate.source||'Apple Health'):'暂无数据'}</em></div><div><span>最近睡眠</span><strong>${number(m.sleepHours,1)}<small>h</small></strong><em>${m.sleepHours?ago(m.sleepHours.at)+' · '+esc(m.sleepHours.source||'Apple Health'):'暂无数据'}</em></div></div>`;
  }
  async function loadHealth(){
    if(healthBusy)return;healthBusy=true;
    try{health=await api('/api/wellness');lastHealth=Date.now();}
    catch{health={connected:false,metrics:{},error:'本机服务未连接'};}
    finally{healthBusy=false;renderHealth();}
  }
  function tickReminders(){
    for(const key of ['stand','water']){
      const r=reminders[key],s=Math.max(0,Math.ceil((r.due-Date.now())/1000)),due=r.enabled&&s===0;
      $('#'+key+'-timer').textContent=!r.enabled?'已暂停':due?'现在':`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
      $('#'+key+'-done').classList.toggle('due',due);
      if(due&&!announced.has(key)){announced.add(key);toast(key==='stand'?'站起来活动一下。':'休息一下，喝口水。');}
    }
  }
  for(const key of ['stand','water'])$('#'+key+'-done').addEventListener('click',()=>{const r=reminders[key];r.due=Date.now()+r.minutes*60000;r.enabled=true;announced.delete(key);persist();tickReminders();});
  async function openSettings(editMicrosoft=false){
    editMicrosoft=editMicrosoft===true;
    const dialog=$('#wellness-dialog');if(!dialog.open)dialog.showModal();
    $('#wellness-settings-content').innerHTML='<p>读取连接设置…</p>';
    try{
      const c=await api('/api/microsoft');
      $('#wellness-settings-content').innerHTML=`<section class="setup-section"><h3>Microsoft To Do</h3><p>${c.connected?`已连接 ${esc(c.account)}`:'连接后可同步清单、完成与恢复任务。'}</p>
      ${(!c.configured||editMicrosoft)?`<label for="ms-authority">账号类型</label><select id="ms-authority"><option value="consumers" ${c.authority==='consumers'?'selected':''}>个人 Microsoft 账号</option><option value="common" ${c.authority!=='consumers'?'selected':''}>组织和个人账号</option></select><label for="ms-client-id">Microsoft 应用 ID</label><input id="ms-client-id" placeholder="Application (client) ID" autocomplete="off" spellcheck="false"><p class="setup-help">首次需要在 Microsoft 注册个人桌面应用。回调地址：<code>${esc(c.redirectUri)}</code></p><a href="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade" target="_blank" rel="noreferrer">打开应用注册 ↗</a><button class="primary" id="ms-save">保存应用 ID</button>`:`<div class="account-actions"><button class="primary" id="ms-connect">${c.connected?'重新授权':'登录 Microsoft'}</button>${c.connected?'<button class="soft" id="ms-disconnect">断开</button>':''}</div><button class="soft small" id="ms-change">更换应用 ID</button>`}</section>
      <section class="setup-section"><h3>Apple Health</h3><p>在 iPhone 上读取健康数据，保存到 iCloud Drive 的 JSON 文件。选择一次后，操作台每 10 秒读取更新。</p><p class="setup-help">心率按设备采样时间显示，后台采样并非连续实时；HRV、静息心率与睡眠作为恢复参考。</p><div class="account-actions">${window.webkit?.messageHandlers?.wokyis?'<button class="primary" id="choose-health-file">选择同步文件</button>':''}<label class="file-label">导入快照<input id="health-import" type="file" accept=".json,application/json"></label></div>${health?.connected?'<button class="soft" id="health-disconnect">断开健康数据</button>':''}</section>
      <section class="setup-section"><h3>休息提醒</h3><p>操作台运行时计时；点击“已站立／已喝水”重新计时。</p><div class="reminder-settings">${[['stand','站立'],['water','喝水']].map(([k,label])=>`<label for="${k}-minutes">${label}间隔<select id="${k}-minutes">${[0,30,45,60,90,120].map(n=>`<option value="${n}" ${(reminders[k].enabled?reminders[k].minutes:0)===n?'selected':''}>${n?n+' 分钟':'暂停'}</option>`).join('')}</select></label>`).join('')}</div></section>`;
      $('#ms-change')?.addEventListener('click',()=>openSettings(true));
      $('#ms-save')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{await api('/api/microsoft/configure',{clientId:$('#ms-client-id').value,authority:$('#ms-authority').value});await openSettings();}));
      $('#ms-connect')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{external((await api('/api/microsoft/connect',{})).url);toast('请在浏览器完成 Microsoft 授权');}));
      $('#ms-disconnect')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{await api('/api/microsoft/disconnect',{});await loadTodo(true);await openSettings();}));
      $('#health-disconnect')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{await api('/api/wellness/disconnect',{});await loadHealth();await openSettings();}));
      $('#choose-health-file')?.addEventListener('click',()=>window.webkit.messageHandlers.wokyis.postMessage({action:'health-file'}));
      $('#health-import').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>1000000)throw Error('请选择小于 1 MB 的最新指标文件');health=await api('/api/wellness/import',JSON.parse(await f.text()));renderHealth();toast('已导入健康快照');await openSettings();}catch(err){toast(err.message);}});
      for(const key of ['stand','water'])$('#'+key+'-minutes').addEventListener('change',e=>{const minutes=Number(e.target.value);reminders[key]={minutes:minutes||reminders[key].minutes,enabled:minutes>0,due:Date.now()+minutes*60000};announced.delete(key);persist();tickReminders();});
    }catch(e){$('#wellness-settings-content').textContent=e.message;}
  }
  window.addEventListener('health-source-selected',async e=>{try{health=await api('/api/wellness/configure',{path:e.detail});renderHealth();await openSettings();toast('已连接健康同步文件');}catch(err){toast(err.message);}});
  $('#wellness-settings').addEventListener('click',openSettings);
  document.addEventListener('click',e=>{if(e.target.closest('[data-wellness-settings]'))openSettings();});
  $('#refresh-todo').addEventListener('click',()=>loadTodo(true));
  $('#wellness-dialog').addEventListener('close',()=>{if(visible){loadTodo();loadHealth();}});
  window.addEventListener('focus',()=>{if(visible){loadTodo();loadHealth();}});
  window.wellnessUI={pageChanged(on){visible=on;if(on){loadTodo();loadHealth();}}};
  setInterval(()=>{tickReminders();if(visible){if(Date.now()-lastTodo>60000)loadTodo();if(Date.now()-lastHealth>10000)loadHealth();}},1000);
  renderHealth();tickReminders();if(visible){loadTodo();loadHealth();}
})();
