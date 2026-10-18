'use strict';
(() => {
  const sessions = new Map();
  const viewer=crypto.randomUUID().replaceAll('-','');
  const terminalApi=(action,data={})=>api('/api/terminal/'+action,{...data,viewer});
  let rows = [], selected = localStorage.getItem('wokyis-terminal'), loading = false, polling = false, visible = currentPage === PAGE.terminal, mutation = false;
  const screen = $('#terminal-screen');
  const theme = {background:'#0a0e0b',foreground:'#9ce6b0',cursor:'#8fe5a6',cursorAccent:'#080c09',selectionBackground:'#294b34',
    black:'#17241b',red:'#ff7f82',green:'#8fe5a6',yellow:'#efd477',blue:'#91bcec',magenta:'#c7a2df',cyan:'#91d8cf',white:'#e1ece4',
    brightBlack:'#88968c',brightRed:'#ffa0a2',brightGreen:'#b1f3c2',brightYellow:'#ffe6a0',brightBlue:'#b0d1f5',brightMagenta:'#ddbaef',brightCyan:'#b0eee5',brightWhite:'#f3fff6'};
  function status(text){$('#terminal-status').textContent=text;}
  function render(){
    $('#terminal-tabs').innerHTML = rows.map(row => `<button role="tab" aria-selected="${row.id===selected}" class="terminal-tab ${row.id===selected?'selected':''}" data-terminal="${row.id}">${esc(row.name)}<span>${esc(row.command||'shell')}</span></button>`).join('');
    $('#terminal-empty').hidden=rows.length>0;
    for(const [id,s] of sessions)s.host.hidden=id!==selected;
    for(const name of ['prev','next','close'])$('#terminal-'+name).disabled=!rows.length||mutation;
    $('#new-terminal').disabled=mutation||rows.length>=12;
    $('#reconnect-terminal').disabled=mutation||!rows.length;
  }
  function fit(s){
    if(!visible || s.id!==selected || !s.host.isConnected)return;
    s.fit.fit();
    const size=`${s.term.cols}:${s.term.rows}`;
    if(size===s.size)return;
    s.size=size;
    terminalApi('resize',{id:s.id,cols:s.term.cols,rows:s.term.rows}).catch(()=>{s.size='';});
  }
  function input(s,data){
    if(s.failed || !data)return;
    // Preserve input order, including paste. Never retry an uncertain command.
    const chunks=[];let part='';
    for(const c of data){part+=c;if(part.length>=8000){chunks.push(part);part='';}}
    if(part)chunks.push(part);
    for(const chunk of chunks)s.inputQueue=s.inputQueue.then(async()=>{
      if(s.failed)return;
      await terminalApi('input',{id:s.id,data:chunk});
    }).catch(e=>{s.failed=true;status('输入已暂停');toast(e.message+'；点击 ↻ 重新连接后继续。');});
  }
  function makeSession(id){
    const host=document.createElement('div');host.className='terminal-host';host.hidden=true;screen.append(host);
    const term=new Terminal({fontFamily:'Hack, "Sarasa Mono SC", monospace',fontSize:27,fontWeight:'400',fontWeightBold:'700',lineHeight:1.15,
      cursorStyle:'block',cursorBlink:false,scrollback:5000,theme,allowProposedApi:false,screenReaderMode:true,convertEol:false});
    const fitAddon=new FitAddon.FitAddon();term.loadAddon(fitAddon);term.open(host);
    const s={id,host,term,fit:fitAddon,cursor:0,stream:'',size:'',inputQueue:Promise.resolve(),failed:false};
    term.onData(data=>input(s,data));
    // Leave Command keys to the application; arrow keys stay inside the shell.
    term.attachCustomKeyEventHandler(e=>!(e.metaKey&&['F7','F8','F9'].includes(e.key)));
    sessions.set(id,s);return s;
  }
  async function select(id, focus=true){
    if(!rows.some(r=>r.id===id))return;
    selected=id;localStorage.setItem('wokyis-terminal',id);status('连接中');render();
    await terminalApi('open',{id});
    if(selected!==id)return;
    await Promise.all(['400 27px Hack','700 27px Hack','400 27px \"Sarasa Mono SC\"','700 27px \"Sarasa Mono SC\"'].map(font=>document.fonts.load(font)));
    if(selected!==id)return;
    const s=sessions.get(id)||makeSession(id);s.failed=false;s.size='';render();
    requestAnimationFrame(()=>{fit(s);if(visible&&focus)s.term.focus();});
    status('常驻 · 本机');poll();
  }
  async function refresh(){
    if(loading)return;loading=true;
    try{
      const result=await api('/api/terminals');rows=result.sessions;
      for(const [id,s] of sessions){if(!rows.some(r=>r.id===id)){s.term.dispose();s.host.remove();sessions.delete(id);}}
      render();
      if(!result.available){status('未连接');toast(result.error);return;}
      if(rows.length)await select(rows.some(r=>r.id===selected)?selected:rows[0].id);
      else{selected=null;localStorage.removeItem('wokyis-terminal');status('等待新终端');}
    }catch(e){status('连接中断');toast(e.message);}
    finally{loading=false;}
  }
  async function poll(){
    if(polling||!visible||!selected)return;
    const s=sessions.get(selected);if(!s)return;
    polling=true;
    try{
      const d=await api(`/api/terminal/output?id=${s.id}&cursor=${s.cursor}&stream=${s.stream}&viewer=${viewer}`);
      if(!sessions.has(s.id))return;
      if(d.reset)s.term.reset();
      const bytes=Uint8Array.from(atob(d.data),c=>c.charCodeAt(0));
      await new Promise(resolve=>s.term.write(bytes,resolve));
      s.cursor=d.cursor;s.stream=d.stream;
      if(!d.alive){status('会话已退出');await refresh();}
      else if(!s.failed)status('常驻 · 本机');
    }catch(e){status('连接中断 · 可重连');}
    finally{polling=false;}
  }
  async function create(){
    if(mutation)return;mutation=true;render();
    try{const d=await terminalApi('create');rows=d.sessions;await select(d.id);}
    catch(e){toast(e.message);}finally{mutation=false;render();}
  }
  async function close(){
    if(!selected||mutation)return;
    const id=selected,index=rows.findIndex(r=>r.id===id);mutation=true;render();
    try{
      const d=await terminalApi('close',{id});
      const s=sessions.get(id);s?.term.dispose();s?.host.remove();sessions.delete(id);
      rows=d.sessions;selected=rows[Math.min(index,rows.length-1)]?.id||null;render();
      if(selected)await select(selected);else{localStorage.removeItem('wokyis-terminal');status('等待新终端');}
    }catch(e){toast(e.message);}finally{mutation=false;render();}
  }
  async function step(delta){
    if(mutation||!rows.length)return;
    const next=(rows.findIndex(r=>r.id===selected)+delta+rows.length)%rows.length;
    try{await select(rows[next].id);}catch(e){toast(e.message);}
  }
  $('#new-terminal').addEventListener('click',create);
  $('#terminal-close').addEventListener('click',close);
  $('#terminal-prev').addEventListener('click',()=>step(-1));
  $('#terminal-next').addEventListener('click',()=>step(1));
  $('#reconnect-terminal').addEventListener('click',refresh);
  $('#terminal-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-terminal]');if(b)select(b.dataset.terminal).catch(e=>toast(e.message));});
  window.terminalShortcut=action=>{
    if($('dialog[open]'))return;
    // A close shortcut must never close an unseen terminal from a clock/calendar.
    if(currentPage!==PAGE.terminal){goPage(PAGE.terminal);return;}
    if(action==='close')close();else step(action==='prev'?-1:1);
  };
  document.addEventListener('keydown',e=>{
    if(e.metaKey&&['F7','F8','F9'].includes(e.key)){e.preventDefault();e.stopPropagation();if(!e.repeat)window.terminalShortcut({F7:'prev',F8:'close',F9:'next'}[e.key]);}
  },true);
  new ResizeObserver(()=>{const s=sessions.get(selected);if(s)fit(s);}).observe(screen);
  window.addEventListener('pagehide',()=>{
    fetch('/api/terminal/detach',{method:'POST',headers:{'Content-Type':'application/json','X-Wokyis':'1'},body:JSON.stringify({viewer}),keepalive:true}).catch(()=>{});
  });
  window.addEventListener('focus',()=>{if(visible)refresh();});
  window.terminalUI={pageChanged(on){visible=on;if(on){refresh();}else{sessions.get(selected)?.term.blur();}}};
  setInterval(poll,120);
  render();if(visible)refresh();
})();
