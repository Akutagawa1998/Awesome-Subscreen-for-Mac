'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const compact = n => n == null ? '—' : new Intl.NumberFormat('en-US', {notation:'compact', maximumFractionDigits:1}).format(n);
const fmtNumber = n => new Intl.NumberFormat('en-US').format(n || 0);
const ny = 'America/New_York';
const {timeParts,dayKey,eventOnDay,uniqueEvents}=window.timeHelpers;
const PAGE = {calendar:2,todo:3,codex:4,claude:5,terminal:6};
const pageCount = $$('.page').length;
let currentPage = Math.min(pageCount-1, Math.max(0, Number(localStorage.getItem('wokyis-page-v2')) || 0));
let statusData = null, selectedThread = null, detailData = null, detailKey = '', statusBusy = false;
let activeApproval = null, replyDrafts = {}, activeThreadFetch = 0;
let calendarData = {events:[],calendars:[],errors:[]}, googleConfig = null, calendarBusy = false, calendarLoaded = false;
let claudeLoadedAt = 0, claudeBusy = false, calendarFetchedAt = 0;
let toastTimer;

async function api(path, body, options = {}) {
  const request = body === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json','X-Wokyis':'1'}, body:JSON.stringify(body)
  };
  if (options.signal) request.signal = options.signal;
  const response = await fetch(path, request);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '暂时无法连接');
  return data;
}
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4500);
}
function external(url) {
  const a = document.createElement('a'); a.href = url; a.target = '_blank'; a.rel = 'noreferrer'; a.click();
}
async function perform(button, task) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try { await task(); } catch (e) { toast(e.message); }
  finally { if (button?.isConnected) button.disabled = false; }
}

function goPage(index) {
  currentPage = (index + pageCount) % pageCount;
  $$('.page').forEach((page, i) => {page.classList.toggle('active', i === currentPage); page.inert = i !== currentPage;});
  $$('[data-nav]').forEach(button => {button.classList.toggle('selected', Number(button.dataset.nav) === currentPage); button.setAttribute('aria-current', Number(button.dataset.nav) === currentPage ? 'page':'false');});
  localStorage.setItem('wokyis-page-v2', currentPage);
  if (currentPage === PAGE.calendar && (!calendarLoaded || Date.now()-calendarFetchedAt>60000)) loadCalendar();
  if (currentPage === PAGE.codex) refreshStatus();
  if (currentPage === PAGE.claude) loadClaude();
  window.wellnessUI?.pageChanged(currentPage === PAGE.todo);
  window.terminalUI?.pageChanged(currentPage === PAGE.terminal);
}
$$('[data-nav]').forEach(button => button.addEventListener('click', () => goPage(Number(button.dataset.nav))));
document.addEventListener('keydown', event => {
  if ($('dialog[open]') || event.target.closest('input,textarea,select,[contenteditable],#terminal-screen')) return;
  if (event.key === 'ArrowRight') { event.preventDefault(); goPage(currentPage + 1); }
  if (event.key === 'ArrowLeft') { event.preventDefault(); goPage(currentPage - 1); }
  if (event.key.toLowerCase() === 'f') toggleFullscreen();
});
let gesture = null;
const pageSwipe = new window.consoleUI.PageSwipe();
$('#deck').addEventListener('pointerdown', e => {
  if (e.button !== 0 || e.target.closest('input,textarea,button,a,dialog,#terminal-screen')) return;
  gesture = {x:e.clientX,y:e.clientY,time:Date.now(),id:e.pointerId};
});
$('#deck').addEventListener('pointerup', e => {
  if (!gesture || gesture.id !== e.pointerId) return;
  const dx=e.clientX-gesture.x, dy=e.clientY-gesture.y, duration=Date.now()-gesture.time;
  gesture=null;
  if (duration<1200 && Math.abs(dx)>75 && Math.abs(dx)>Math.abs(dy)*1.8) {
    window.getSelection()?.removeAllRanges(); goPage(currentPage+(dx<0?1:-1));
  }
});
$('#deck').addEventListener('pointercancel', () => {gesture=null;});
document.addEventListener('wheel', e => {
  if ($('dialog[open]') || e.ctrlKey || e.metaKey) {pageSwipe.reset();return;}
  const {consume,step}=pageSwipe.wheel(e.deltaX,e.deltaY,performance.now(),e.deltaMode);
  if (consume) {e.preventDefault();e.stopPropagation();}
  if (step) goPage(currentPage+step);
}, {passive:false,capture:true});
async function toggleFullscreen() {
  if (window.webkit?.messageHandlers?.wokyis) {window.webkit.messageHandlers.wokyis.postMessage({action:'fullscreen'}); return;}
  try {document.fullscreenElement ? await document.exitFullscreen() : await document.documentElement.requestFullscreen();}
  catch {toast('请使用浏览器的全屏功能');}
}
$('#fullscreen').addEventListener('click', toggleFullscreen);
// Best effort only; system sleep is never changed.
let wakeLock;
async function keepAwake(){try{if(navigator.wakeLock && document.visibilityState==='visible')wakeLock=await navigator.wakeLock.request('screen');}catch{}}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){keepAwake();updateClocks();}});

function half(value, side, extra='') {return `<div class="half ${side} ${extra}" aria-hidden="true"><span>${value}</span></div>`;}
function flipDigit(digit, value) {
  const old = digit.dataset.value;
  if(old === value) return;
  digit.dataset.value=value;
  digit.innerHTML=half(value,'top')+half(value,'bottom');
  if(old!==undefined && !matchMedia('(prefers-reduced-motion: reduce)').matches){
    digit.insertAdjacentHTML('beforeend',half(old,'top','flap')+half(value,'bottom','flap'));
    setTimeout(()=>$$('.flap',digit).forEach(x=>x.remove()),590);
  }
}
function renderClock(id, zone) {
  const now = new Date(), parts=timeParts(zone,now), values=parts.hour+parts.minute, host=$(`#${id}-clock`);
  if(!host.children.length) host.innerHTML='<div class="digit"></div><div class="digit"></div><div class="colon" aria-hidden="true"><i></i><i></i></div><div class="digit"></div><div class="digit"></div>';
  $$('.digit',host).forEach((digit,i)=>flipDigit(digit,values[i]));
  host.setAttribute('aria-label', `${id==='ny'?'纽约':'上海'}时间 ${parts.hour}:${parts.minute}`);
  $(`#${id}-date`).textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:zone,month:'long',day:'numeric',weekday:'long'}).format(now);
  $(`#${id}-seconds`).textContent=`${parts.second} 秒`;
  $(`#${id}-zone`).textContent=parts.timeZoneName+' · '+(id==='ny'?'纽约时间':'中国标准时间');
}
function updateClocks(){renderClock('ny',ny);renderClock('sh','Asia/Shanghai');}
updateClocks();setInterval(updateClocks,250);

const todayKey=()=>dayKey(new Date());
let chosenDate=todayKey(), monthDate=new Date(chosenDate+'T12:00:00Z');
function renderCalendar() {
  const year=monthDate.getUTCFullYear(), month=monthDate.getUTCMonth();
  $('#month-title').textContent=`${year}年 ${month+1}月`;
  const first=new Date(Date.UTC(year,month,1)), gridStart=new Date(+first-first.getUTCDay()*86400000);
  $('#month-grid').innerHTML=Array.from({length:42},(_,i)=>{
    const date=new Date(+gridStart+i*86400000), key=date.toISOString().slice(0,10), has=calendarData.events.some(e=>eventOnDay(e,key));
    return `<button class="day ${date.getUTCMonth()!==month?'outside':''} ${key===todayKey()?'today':''} ${key===chosenDate?'chosen':''}" data-day="${key}" aria-label="${key}${has?' 有日程':''}" ${key===chosenDate?'aria-pressed="true"':''}>${date.getUTCDate()}${has?'<span class="event-dot"></span>':''}</button>`;
  }).join('');
  $('#agenda-day').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'UTC',month:'long',day:'numeric',weekday:'short'}).format(new Date(chosenDate+'T12:00:00Z'));
  const events=uniqueEvents(calendarData.events.filter(e=>eventOnDay(e,chosenDate))).sort((a,b)=>(a.start.date||a.start.dateTime).localeCompare(b.start.date||b.start.dateTime));
  const hasAccounts=googleConfig?.accounts?.length;
  const problems=calendarData.errors.length?`<div class="notice warn">${esc(calendarData.errors[0].message)}${calendarData.errors.length>1?`（${calendarData.errors.length} 个同步问题）`:''}</div>`:'';
  if(!hasAccounts){
    $('#agenda-list').innerHTML='<div class="empty"><span class="empty-symbol">▦</span><h2>让日程来到这里</h2><p>连接 Google Calendar<br>多个账号，合并显示。</p><button class="primary" data-settings>连接 Google</button></div>';
  } else if(calendarBusy){$('#agenda-list').innerHTML='<div class="empty"><p>正在同步日程…</p></div>';}
  else if(events.length){
    $('#agenda-list').innerHTML=problems+events.map(e=>{
      let timing='全天';
      if(e.start.dateTime){
        const f=new Intl.DateTimeFormat('zh-CN',{timeZone:ny,hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
        timing=`${dayKey(new Date(e.start.dateTime))<chosenDate?'前一天':f.format(new Date(e.start.dateTime))} — ${dayKey(new Date(e.end.dateTime))>chosenDate?'次日 ':''}${f.format(new Date(e.end.dateTime))}`;
      }
      return `<article class="event" style="border-color:${esc(e.color)}"><div class="event-time" style="color:${esc(e.color)}">${esc(timing)}</div><h3>${esc(e.title)}</h3>${e.location?`<p class="event-location">${esc(e.location)}</p>`:''}<div class="event-account">${esc(e.account)}</div></article>`;
    }).join('');
  }else{$('#agenda-list').innerHTML=problems+'<div class="empty"><span class="empty-symbol">☀</span><h2>今天留一点空白</h2><p>这一天没有日程。</p></div>';}
  $('#calendar-sync').textContent=calendarBusy?'同步中':calendarData.fetchedAt?'已同步':'';
}
async function loadCalendar(force=false) {
  if(calendarBusy)return;
  calendarBusy=true;renderCalendar();
  try {
    googleConfig=await api('/api/google');
    if(googleConfig.accounts.length){
      const y=monthDate.getUTCFullYear(),m=monthDate.getUTCMonth();
      const start=new Date(Date.UTC(y,m,1)-7*86400000).toISOString(),end=new Date(Date.UTC(y,m+1,8)).toISOString();
      calendarData=await api(`/api/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    }else{calendarData={events:[],calendars:[],errors:[]};}
    calendarLoaded=true;calendarFetchedAt=Date.now();
  } catch(e){calendarData.errors=[{message:e.message}];}
  finally{calendarBusy=false;renderCalendar();}
}
$('#month-grid').addEventListener('click',e=>{const b=e.target.closest('[data-day]');if(b){chosenDate=b.dataset.day;renderCalendar();}});
function moveMonth(delta){if(calendarBusy)return;monthDate=new Date(Date.UTC(monthDate.getUTCFullYear(),monthDate.getUTCMonth()+delta,1,12));chosenDate=monthDate.toISOString().slice(0,10);loadCalendar();}
$('#month-prev').addEventListener('click',()=>moveMonth(-1));$('#month-next').addEventListener('click',()=>moveMonth(1));
$('#month-today').addEventListener('click',()=>{if(calendarBusy)return;chosenDate=todayKey();monthDate=new Date(chosenDate+'T12:00:00Z');loadCalendar();});
$('#refresh-calendar').addEventListener('click',()=>loadCalendar(true));
$('#calendar-settings').addEventListener('click',openSettings);
document.addEventListener('click',e=>{if(e.target.closest('[data-settings]'))openSettings();if(e.target.closest('[data-close]'))e.target.closest('dialog').close();});
async function openSettings(){
  const dialog=$('#settings-dialog');if(!dialog.open)dialog.showModal();
  $('#settings-content').innerHTML='<p>正在读取账号…</p>';
  try{googleConfig=await api('/api/google');renderSettings();}catch(e){$('#settings-content').textContent=e.message;}
}
function renderSettings(){
  const accounts=googleConfig.accounts;
  $('#settings-content').innerHTML=`<p>每个 Google 账号单独授权。日程只读，每条日程显示所属账号。</p>
    ${accounts.map(a=>`<div class="account-row"><span class="account-email"><i class="account-badge"></i>${esc(a.email)}</span><button class="soft small" data-disconnect="${esc(a.id)}">断开</button></div>`).join('')}
    ${googleConfig.configured?'<button class="primary" id="add-google">＋ 添加 Google 账号</button>':`<div class="notice">首次连接需要 Google Cloud 的「桌面应用」OAuth 配置。启用 Calendar API 后下载 JSON，在这里导入一次。</div><label class="file-label">导入 OAuth 配置 JSON<input id="google-file" type="file" accept=".json,application/json"></label><a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">打开 Google Cloud 凭据设置 ↗</a>`}
    <div class="notice">账号授权保存在本机。断开只会移除操作台里的连接，不会删除 Google 日历或日程。</div>`;
  $('#google-file')?.addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;await api('/api/google/configure',JSON.parse(await file.text()));googleConfig=await api('/api/google');renderSettings();toast('配置已导入，现在可以添加账号');}catch(err){toast(err.message);}});
  $('#add-google')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{const {url}=await api('/api/google/connect',{});external(url);toast('请在浏览器中完成 Google 授权');}));
  $$('[data-disconnect]').forEach(b=>b.addEventListener('click',()=>perform(b,async()=>{await api('/api/google/disconnect',{id:b.dataset.disconnect});googleConfig=await api('/api/google');renderSettings();calendarLoaded=false;await loadCalendar();})));
}
$('#settings-dialog').addEventListener('close',()=>loadCalendar());
window.addEventListener('focus',()=>{if(currentPage===PAGE.calendar)loadCalendar();});

function statusInfo(thread) {
  const s=thread.status || {}, flags=s.activeFlags || [];
  if(flags.includes('waitingOnApproval'))return {label:'需要批准',key:'waiting'};
  if(flags.includes('waitingOnUserInput'))return {label:'等待回复',key:'waiting'};
  if(s.type==='active')return {label:'运行中',key:'active'};
  if(s.type==='idle')return {label:'空闲',key:'idle'};
  if(s.type==='systemError')return {label:'发生错误',key:'error'};
  return {label:'运行状态未知',key:'unknown'};
}
function threadName(t){return t.name || (t.preview || '').split('\n')[0].slice(0,80) || '未命名会话';}
function renderStatus(){
  if(!statusData)return;
  $('#codex-connection').textContent=statusData.connected?'本地已连接':'连接中断';
  $('#codex-connection').classList.toggle('online',statusData.connected);
  const hidden=new Set(statusData.hidden||[]), threads=(statusData.threads||[]).filter(t=>!hidden.has(t.id));
  $('#hidden-count').textContent=hidden.size;
  let buckets=Object.values(statusData.limits?.rateLimitsByLimitId || {});
  if(!buckets.length && statusData.limits?.rateLimits)buckets=[statusData.limits.rateLimits];
  const bucket=buckets.find(b=>b.limitId==='codex')||buckets[0];
  $('#plan-name').textContent=(bucket?.planType||'CODEX').toUpperCase();
  const windows=bucket?[bucket.primary,bucket.secondary].filter(Boolean):[];
  let usage=windows.map(w=>{
    const mins=w.windowDurationMins, label=mins===10080?'本周额度':mins===300?'5 小时额度':mins?`${mins>=1440?`${Math.round(mins/1440)} 天`:`${Math.round(mins/60)} 小时`}额度`:'使用额度';
    const known=Number.isFinite(w.usedPercent),percent=known?Math.max(0,Math.min(100,w.usedPercent)):null;
    const reset=w.resetsAt?new Intl.DateTimeFormat('zh-CN',{timeZone:ny,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(w.resetsAt*1000)):null;
    return `<div class="quota-row"><span>${label}</span><strong>${known?`${percent}%`:'—'} 已用</strong></div><div class="meter" data-tone="${window.consoleUI.quotaTone(percent)}" role="meter" aria-label="${label}已用" aria-valuemin="0" aria-valuemax="100" ${known?`aria-valuenow="${percent}"`: 'aria-valuetext="暂无数据"'}><i style="width:${percent||0}%"></i></div>${reset?`<div class="quota-reset">${reset} 纽约时间重置</div>`:''}`;
  }).join('');
  const daily=statusData.usage?.dailyUsageBuckets;
  const dailyRow=daily?.find(b=>b.startDate===todayKey());
  const latest=daily?.length?[...daily].sort((a,b)=>b.startDate.localeCompare(a.startDate))[0]:null;
  const display=dailyRow||latest;
  usage+=`<div class="token-line"><span>${dailyRow?'今日':display?display.startDate.slice(5):'最近每日'} tokens</span><strong>${compact(display?.tokens)}</strong></div>`;
  if(!windows.length)usage='<div class="muted">额度暂不可用</div>'+usage;
  $('#codex-usage').innerHTML=usage;
  const active=threads.filter(t=>statusInfo(t).key==='active').length, waiting=threads.filter(t=>statusInfo(t).key==='waiting').length, unknown=threads.filter(t=>statusInfo(t).key==='unknown').length;
  $('#session-counts').innerHTML=`<span><strong>${active}</strong>运行</span><span class="waiting"><strong>${waiting}</strong>待处理</span><span><strong>${unknown}</strong>未知</span>`;
  $('#session-coverage').textContent=`本机 ${threads.length}${statusData.truncated?'+':''} 个`;
  threads.sort((a,b)=>{const order={waiting:0,active:1,error:2,idle:3,unknown:4};return order[statusInfo(a).key]-order[statusInfo(b).key]||(b.updatedAt-a.updatedAt);});
  $('#session-list').innerHTML=threads.length?threads.map(t=>{const s=statusInfo(t);return `<div class="session-row ${selectedThread===t.id?'chosen':''}"><button class="session-select" data-session="${esc(t.id)}"><span class="session-title">${esc(threadName(t))}</span><span class="session-status status-${s.key}"><i class="status-dot"></i>${s.label}</span></button><button class="hide-session" data-hide="${esc(t.id)}" aria-label="仅从操作台隐藏 ${esc(threadName(t))}" title="仅从操作台隐藏">×</button></div>`;}).join(''):'<div class="notice">这里还没有会话。隐藏的会话可以随时恢复。</div>';
  if(statusData.error && !selectedThread)$('#agent-detail').innerHTML=`<div class="empty"><h2>暂时无法连接</h2><p>${esc(statusData.error)}</p><p>可以先使用时钟和日历。</p></div>`;
  if(selectedThread && hidden.has(selectedThread)){selectedThread=null;detailData=null;detailKey='';$('#agent-detail').innerHTML='<div class="empty"><h2>已从操作台隐藏</h2><p>会话仍保留在 Codex 和 ChatGPT 中。</p></div>';}
  if(activeApproval && !statusData.approvals.some(p=>String(p.id)===String(activeApproval.id))){
    $('#approval-dialog').close(); activeApproval=null; toast('请求已处理或已失效');
  }
}
async function refreshStatus(){
  if(statusBusy)return;statusBusy=true;
  try{statusData=await api('/api/status');renderStatus();if(selectedThread && currentPage===PAGE.codex)await loadDetail(selectedThread,false);}
  catch{ $('#codex-connection').textContent='服务未连接';$('#codex-connection').classList.remove('online');}
  finally{statusBusy=false;}
}
$('#refresh-codex').addEventListener('click',refreshStatus);
$('#restore-sessions').addEventListener('click',e=>perform(e.currentTarget,async()=>{await api('/api/restore',{});await refreshStatus();toast('已恢复操作台内隐藏的会话');}));
$('#session-list').addEventListener('click',e=>{
  const hide=e.target.closest('[data-hide]');if(hide){perform(hide,async()=>{await api('/api/hide',{id:hide.dataset.hide,hidden:true});await refreshStatus();toast('已隐藏，会话仍保留在原应用');});return;}
  const b=e.target.closest('[data-session]');if(b){selectedThread=b.dataset.session;detailKey='';renderStatus();loadDetail(selectedThread,true);}
});
async function loadDetail(id,showLoading){
  const request=++activeThreadFetch;
  if(showLoading)$('#agent-detail').innerHTML='<div class="empty"><p>正在读取会话…</p></div>';
  try{const data=await api(`/api/thread?id=${encodeURIComponent(id)}`);if(selectedThread!==id||request!==activeThreadFetch)return;detailData=data;renderDetail();}
  catch(e){if(selectedThread===id&&showLoading)$('#agent-detail').innerHTML=`<div class="empty"><p>${esc(e.message)}</p><a class="button primary" href="codex://threads/${esc(id)}">在 Codex 中打开 ↗</a></div>`;}
}
function renderDetail(){
  const d=detailData,t=d.thread,s=statusInfo(t),owned=['active','idle'].includes(t.status?.type), pending=d.approvals || [];
  const key=JSON.stringify(d);if(key===detailKey)return;
  // Don't interrupt typing or focus when background polling changes metadata.
  if($('#agent-detail').contains(document.activeElement) && document.activeElement.matches('textarea'))return;
  detailKey=key;
  const recent=d.messages.find(m=>m.type==='agent') || d.messages[0];
  const latest=recent?.text || t.preview || '此会话暂无可用摘要。';
  $('#agent-detail').innerHTML=`<div class="detail-heading"><h2>${esc(threadName(t))}</h2><span class="tag">${s.label}</span></div>
    <div class="detail-body scroll">${pending.map(p=>`<div class="approval-card"><h3>${p.method==='item/tool/requestUserInput'?'有问题需要你回复':'有请求需要你批准'}</h3><p>${esc((p.params.reason||p.params.questions?.[0]?.question||p.params.command||'查看请求详情').slice(0,180))}</p><button class="primary" data-review="${esc(p.id)}">查看完整请求 →</button></div>`).join('')}
    <div><div class="summary-label">${recent?'最近消息':'会话起始内容'} · 可上下滚动</div><div class="summary">${esc(latest)}</div></div>
    <div class="model-line">${esc(t.model||'模型未知')}${t.reasoningEffort?' · '+esc(t.reasoningEffort):''} · 会话配置</div>
    ${!owned?'<div class="notice">这个会话的实时状态由其他运行实例管理。请打开 Codex 查看运行状态、回复或审批。</div>':!d.attached?'<div class="notice">连接后可接收这个会话的审批和回复请求。</div>':''}
    ${d.error?`<p class="muted">${esc(d.error)}</p>`:''}</div>
    ${owned?`<div class="reply-area"><textarea id="quick-reply" rows="2" aria-label="给此会话回复" placeholder="给这个会话回复…">${esc(replyDrafts[t.id]||'')}</textarea><button id="send-reply" class="primary">发送</button></div>`:''}
    <div class="detail-actions">${owned&&!d.attached?'<button id="attach-thread" class="soft">连接审批</button>':''}<a class="button soft" href="codex://threads/${esc(t.id)}">在 Codex 中打开 ↗</a></div>`;
  $('#quick-reply')?.addEventListener('input',e=>{replyDrafts[t.id]=e.target.value;});
  $('#send-reply')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{await api('/api/reply',{id:t.id,text:replyDrafts[t.id]||''});replyDrafts[t.id]='';$('#quick-reply').value='';toast('回复已发送');detailKey='';await loadDetail(t.id,false);}));
  $('#attach-thread')?.addEventListener('click',e=>perform(e.currentTarget,async()=>{await api('/api/attach',{id:t.id});toast('已连接这个会话');await loadDetail(t.id,false);}));
  $$('[data-review]').forEach(b=>b.addEventListener('click',()=>reviewApproval(pending.find(p=>String(p.id)===b.dataset.review))));
}
function reviewApproval(p){
  if(!p)return;activeApproval=p;
  const params=p.params, method=p.method;
  const supported=['item/commandExecution/requestApproval','item/fileChange/requestApproval','item/permissions/requestApproval','item/tool/requestUserInput'].includes(method) &&
    (method!=='item/commandExecution/requestApproval' || params.command || params.networkApprovalContext);
  let content=`<div class="notice">${esc(threadName(detailData.thread))}</div>`;
  if(params.reason)content+=`<p>${esc(params.reason)}</p>`;
  if(params.networkApprovalContext)content+=`<h3>网络访问</h3><pre class="request-code">${esc(JSON.stringify(params.networkApprovalContext,null,2))}</pre>`;
  if(params.command)content+=`<h3>将执行的命令</h3><pre class="request-code">${esc(params.command)}</pre>`;
  if(params.cwd)content+=`<p>工作目录</p><pre class="request-code">${esc(params.cwd)}</pre>`;
  if(params.grantRoot)content+=`<p>请求访问的目录</p><pre class="request-code">${esc(params.grantRoot)}</pre>`;
  for(const field of ['permissions','additionalPermissions'])if(params[field])content+=`<h3>请求的权限范围</h3><pre class="request-code">${esc(JSON.stringify(params[field],null,2))}</pre>`;
  if(method==='item/fileChange/requestApproval')content+='<div class="notice warn">此请求未附完整文件差异，请在 Codex 中检查修改后批准。</div>';
  if(method==='item/tool/requestUserInput'){
    content+=(params.questions||[]).map(q=>`<div class="question" data-question="${esc(q.id)}"><h3>${esc(q.question)}</h3><div class="choices">${(q.options||[]).map(o=>`<button class="soft" data-choice="${esc(o.label)}" title="${esc(o.description||'')}">${esc(o.label)}</button>`).join('')}</div><textarea rows="2" aria-label="${esc(q.question)}" placeholder="输入你的回复"></textarea></div>`).join('');
    content+='<div class="approval-buttons"><button class="primary" id="answer-questions">发送回复</button></div>';
  }else if(supported && method!=='item/fileChange/requestApproval'){
    content+='<div class="notice">仅批准上面显示的这次请求。不会启用永久授权或自动批准。</div><div class="approval-buttons"><button class="danger" id="deny-request">拒绝</button><button class="primary" id="allow-request">本次允许</button></div>';
  }else{content+=`<a class="button primary" href="codex://threads/${esc(params.threadId)}">在 Codex 中处理 ↗</a>`;}
  $('#approval-content').innerHTML=content;$('#approval-dialog').showModal();
  $$('[data-choice]').forEach(b=>b.addEventListener('click',()=>{const q=b.closest('[data-question]');$('textarea',q).value=b.dataset.choice;$$('button',q).forEach(x=>x.classList.toggle('selected',x===b));}));
  const respond=async (button,extra)=>perform(button,async()=>{await api('/api/approval',{requestId:p.id,threadId:params.threadId,...extra});$('#approval-dialog').close();activeApproval=null;toast('已提交，等待 Codex 确认');await refreshStatus();});
  $('#allow-request')?.addEventListener('click',e=>respond(e.currentTarget,{decision:'accept'}));
  $('#deny-request')?.addEventListener('click',e=>respond(e.currentTarget,{decision:'decline'}));
  $('#answer-questions')?.addEventListener('click',e=>{const answers=Object.fromEntries($$('[data-question]').map(q=>[q.dataset.question,$('textarea',q).value.trim()]));if(Object.values(answers).some(v=>!v)){toast('请回答每个问题');return;}respond(e.currentTarget,{answers});});
}

async function loadClaude(){
  if(claudeBusy || Date.now()-claudeLoadedAt<60000)return;claudeBusy=true;
  try{
    const data=await api('/api/claude');claudeLoadedAt=Date.now();
    const metrics=[['输入',data.totals.input_tokens],['输出',data.totals.output_tokens],['缓存读取',data.totals.cache_read_input_tokens],['缓存写入',data.totals.cache_creation_input_tokens]];
    $('#claude-content').innerHTML=`<div class="panel claude-total"><h2>今日 tokens <span class="muted">· 纽约日期</span></h2><div class="big-number">${data.available?compact(data.totalTokens):'—'}</div><div class="metric-list">${metrics.map(([label,n])=>`<div class="metric"><span>${label}</span><strong>${data.available?compact(n):'—'}</strong></div>`).join('')}</div></div>
    <div class="panel claude-sessions"><h2>今日 ${data.sessions.length} 个会话</h2><div class="scroll" style="flex:1;min-height:0">${data.sessions.length?data.sessions.map(s=>`<div class="claude-session"><h3>${esc(s.project)}</h3><strong>${compact(s.tokens)}</strong></div>`).join(''):'<div class="empty"><span class="empty-symbol">✳</span><h2>今天还没有记录</h2><p>使用 Claude Code 后<br>这里会自动更新。</p></div>'}</div><p class="muted">${esc(data.date)} · 每分钟刷新</p></div>`;
  }catch(e){$('#claude-content').innerHTML=`<div class="panel empty"><h2>暂时读不到用量</h2><p>${esc(e.message)}</p></div>`;}
  finally{claudeBusy=false;}
}
renderCalendar();goPage(currentPage);refreshStatus();keepAwake();
setInterval(()=>{if(currentPage===PAGE.codex)refreshStatus();if(currentPage===PAGE.claude)loadClaude();if(currentPage===PAGE.calendar && Date.now()-calendarFetchedAt>60000)loadCalendar();},8000);
