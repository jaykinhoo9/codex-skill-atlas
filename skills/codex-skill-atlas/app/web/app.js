'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const DEFAULT_COLORS = {builtin:'#568aaf',local:'#49a580',installed:'#bc995e',plugin:'#9485b8',project:'#bc7b88'};
const COLORS = SourcePalette.load(DEFAULT_COLORS);
let palette=null;
const SOURCES = {builtin:'Codex 内置',local:'本地 / 自建',installed:'下载安装',plugin:'插件技能',project:'项目技能'};
const SOURCE_EN = {builtin:'CODEX ESSENTIALS',local:'YOUR COLLECTION',installed:'INSTALLED SKILLS',plugin:'PLUGIN ECOSYSTEM',project:'PROJECT SKILLS'};
const STATUS = {inProgress:'运行中',completed:'已完成',interrupted:'已中断',failed:'出错',unknown:'无轮次记录',unconfirmed:'状态待确认'};
const state = {data:null,section:'skills',source:'all',query:'',trashQuery:'',view:'graph',scope:'recent',sessionTab:'recent',selected:null,live:true,detailTab:'overview',detail:null,detailToken:0,visibleSkills:[],visibleSessions:[],links:[],nodes:[],nodeMap:new Map(),groups:[],scale:1,pan:{x:0,y:0},fitted:false,hover:null,inspector:false,boxSelected:new Set(),version:0};
const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = (s,n=22) => s.length>n?s.slice(0,n-1)+'…':s;
const date = (n,full=false) => n?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(full?{year:'numeric',second:'2-digit'}:{})}).format(new Date(n)):'—';
const relative = n => {const d=Math.max(0,(Date.now()-n)/1000);return d<60?'刚刚':d<3600?`${Math.floor(d/60)} 分钟前`:d<86400?`${Math.floor(d/3600)} 小时前`:`${Math.floor(d/86400)} 天前`;};
const hash = s => [...s].reduce((a,c)=>((a*31+c.charCodeAt(0))>>>0),0);
const skillById = id => state.data?.skills.find(s=>s.id===id);
const sessionById = id => state.data?.sessions.find(s=>s.id===id);
const linksForSkill = id => state.data?.links.filter(l=>l.skillId===id)||[];
const skillUsage = id => linksForSkill(id).reduce((total,link)=>total+link.count,0);
const linksForSession = id => state.data?.links.filter(l=>l.sessionId===id)||[];
const allCounts = () => Object.fromEntries(Object.keys(SOURCES).map(k=>[k,state.data.skills.filter(s=>s.source===k).length]));

function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,2500);}
function inspectorOpen(open,preserveView=false){open=!!open&&!!state.selected;const changed=state.inspector!==open;state.inspector=open;$('.explorer').classList.toggle('inspector-closed',!open);$('#inspector-toggle').hidden=!state.selected;$('#inspector-toggle').setAttribute('aria-expanded',String(open));if(changed)requestAnimationFrame(()=>{resize();if(!preserveView)fit();});}
function reset(){clearGraphSelection();state.source='all';state.query='';state.selected=null;state.detail=null;state.scope='recent';$('#search').value='';renderScope();closeScope();renderAll();state.fitted=false;layout();}

function renderScope(){
  const options=$$('[data-scope]');
  for(const option of options)option.setAttribute('aria-checked',String(option.dataset.scope===state.scope));
  const label=options.find(option=>option.dataset.scope===state.scope).textContent.trim();
  $('#scope-label').textContent=label;$('#scope').setAttribute('aria-label',`会话显示范围：${label}`);
}
function closeScope(restoreFocus=false){
  $('#scope-menu').hidden=true;$('#scope').setAttribute('aria-expanded','false');
  if(restoreFocus)$('#scope').focus();
}
function openScope(position='selected'){
  $('#scope-menu').hidden=false;$('#scope').setAttribute('aria-expanded','true');
  const options=$$('[data-scope]');
  const option=position==='first'?options[0]:position==='last'?options.at(-1):options.find(option=>option.dataset.scope===state.scope);
  option.focus();
}
function chooseScope(value){
  graphInput.cancel();state.scope=value;renderScope();closeScope(true);filterData();layout();fit();
}

let fetching=false,mutationVersion=0;
async function sync(manual=false){
  if(fetching||(!state.live&&!manual))return;
  fetching=true;const revision=mutationVersion;
  try{
    const response=await fetch('/api/graph'+(manual?'?refresh=1':''),{signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const data=await response.json();
    if(revision!==mutationVersion)return;
    state.data=data;
    const fingerprint=JSON.stringify([data.skills.map(s=>[s.id,s.modifiedAt]),data.sessions.map(s=>[s.id,s.status,s.turnId,s.title,s.indexed]),data.links.map(l=>[l.skillId,l.sessionId,l.count,l.active]),data.trash]);
    if(fingerprint!==sync.fingerprint){sync.fingerprint=fingerprint;renderAll();}
    updateStatus();
    renderActivity();
    renderTrash();
    if(manual)toast('已同步，正在重新扫描技能目录');
  }catch(e){$('#sync-status').textContent='本地服务连接中断，正在重试';$('#status-dot').style.background='#c3a063';$('#warning').hidden=false;$('#warning').textContent='未能连接本地服务。请让 Agent 重新启动技能看板，或运行本 Skill 的 scripts/launch.py；页面会自动重连。';}
  finally{fetching=false;}
}

function updateStatus(){
  const d=state.data;if(!d)return;
  const running=d.sessions.filter(s=>s.status==='inProgress'&&!s.archived);
  $('#stat-skills').textContent=d.skills.length;$('#stat-running').textContent=running.length;$('#stat-links').textContent=d.links.length;
  $('#stat-running').closest('.stat').classList.toggle('is-active',running.length>0);
  $('#stat-used').textContent=new Set(d.links.map(l=>l.skillId)).size;
  $('#stat-running-note').textContent=running.length?`${d.links.filter(l=>l.active).length} 项技能参与当前轮次`:'现在没有运行中的会话';
  $('#index-progress').textContent=d.status.indexing?`历史索引 ${d.status.indexed} / ${d.status.total}`:`已索引 ${d.status.indexed} 个本机会话`;
  $('#sync-status').textContent=state.live?`已连接本机 · ${new Date(d.status.updatedAt||Date.now()).toLocaleTimeString('zh-CN')} 同步${d.status.indexing?' · 正在索引历史记录':''}`:'已暂停页面同步';
  $('#status-dot').style.background=state.live?'#8fac7e':'#c3a063';
  $('#warning').hidden=!d.status.warnings?.length;
  $('#warning').textContent=(d.status.warnings||[]).join('；');
  $('#source-total').textContent=d.skills.length;$('#session-total').textContent=d.sessions.filter(s=>!s.archived).length;$('#running-badge').textContent=running.length;
}

function renderSources(){
  if(!state.data)return;
  const counts=allCounts();
  $('#source-filters').innerHTML=[['all','全部技能',state.data.skills.length],...Object.entries(SOURCES).map(([key,name])=>[key,name,counts[key]])].map(([key,name,count])=>{
    const active=state.section==='skills'&&state.source===key;
    if(key==='all')return `<button class="source-filter ${active?'active':''}" data-source="all"><i class="source-dot" style="background:#72866a"></i>${name}<span class="filter-count">${count}</span></button>`;
    return `<div class="source-row"><button type="button" class="source-color-button" data-color-source="${key}" aria-label="设置${name}颜色" title="设置${name}颜色" aria-haspopup="dialog" aria-controls="source-palette" aria-expanded="${palette?.source===key}"><i class="source-dot" style="background:var(--source-${key})"></i></button><button class="source-filter ${active?'active':''}" data-source="${key}">${name}<span class="filter-count">${count}</span></button></div>`;
  }).join('');
  $('#low-usage-count').textContent=state.data.skills.filter(s=>skillUsage(s.id)<3).length;
  $('#trash-count').textContent=state.data.trash?.entries.length||0;
  $('#low-usage-filter').classList.toggle('active',state.section==='skills'&&state.source==='low');
  $('#trash-filter').classList.toggle('active',state.section==='trash');
  $$('.source-filter').forEach(button=>button.setAttribute('aria-pressed',String(button.classList.contains('active'))));
}

function showSection(section){
  graphInput.cancel();state.section=section;closeScope();
  $('.explorer').hidden=section==='trash';$('#trash-panel').hidden=section!=='trash';
  $('#tooltip').hidden=true;
  renderSources();renderTrash();
  if(section==='skills')requestAnimationFrame(()=>{resize();fit();});
}
const restoringTrash=new Set();
function renderTrash(){
  if(!state.data)return;
  const trash=state.data.trash||{entries:[],warnings:[]},query=state.trashQuery.toLowerCase().trim();
  const signature=JSON.stringify([trash,query,Math.floor(Date.now()/60000),[...restoringTrash]]);
  if(signature===renderTrash.signature)return;renderTrash.signature=signature;
  const entries=trash.entries.filter(item=>item.name.toLowerCase().includes(query));
  $('#trash-visible-count').textContent=trash.entries.length;
  const warnings=(trash.warnings||[]).join('；');
  if(warnings){$('#trash-error').textContent=warnings;$('#trash-error').hidden=false;}
  $('#trash-list').innerHTML=entries.length?entries.map(item=>{
    const days=Math.max(0,Math.ceil((item.expiresAt-Date.now())/86400000));
    const busy=restoringTrash.has(item.id),remaining=!item.expiresAt?'待检查':days===0?'已到期':days===1?'不足 1 天':`剩余 ${days} 天`;
    return `<article class="trash-card"><div class="trash-item-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M5.6 18.4 18.4 5.6"/></svg></div><div class="trash-item-copy"><div class="trash-item-heading"><h2>${esc(item.name)}</h2><span class="trash-source">${esc(SOURCES[item.source]||'本机技能')}${item.skillCount>1?` · 含 ${item.skillCount} 项技能`:''}</span></div><div class="trash-dates"><span>删除于 ${date(item.createdAt)}</span><span>自动清理 ${date(item.expiresAt)}</span></div>${item.issue?`<p class="trash-item-issue">${esc(item.issue)}</p>`:''}<details class="trash-paths"><summary>原始位置${item.paths.length>1?` · ${item.paths.length} 处`:''}</summary>${item.paths.map(path=>`<code>${esc(path)}</code>`).join('')}</details></div><div class="trash-item-actions"><span class="trash-remaining ${days<=3?'expiring':''}">${remaining}</span><button type="button" data-restore="${item.id}" aria-label="恢复 ${esc(item.name)}" ${!item.canRestore||busy?'disabled':''}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 7a5.2 5.2 0 1 1 .8 4M2.5 3.5V7H6"/></svg>${busy?'恢复中…':'恢复'}</button></div></article>`;
  }).join(''):`<div class="trash-empty"><svg viewBox="0 0 40 40" aria-hidden="true"><path d="M9 12h22M12 12l1.5 23h13L28 12M16 12V7h8v5M17 18v11m6-11v11"/></svg><h2>${query?'没有匹配的技能':'回收站是空的'}</h2><p>${query?'换个名称再试试。':'删除的技能会保留在这里，30 天内随时恢复。'}</p></div>`;
}
async function restoreTrash(id){
  if(restoringTrash.has(id))return;
  restoringTrash.add(id);mutationVersion++;$('#trash-error').hidden=true;renderTrash();
  try{
    const result=await skillOperation('restore',{trashId:id});
    applyMutation(result);
    if(recovery?.trashId===id){recovery=null;$('#recovery-notice').hidden=true;}
    toast(`已恢复 ${result.name}`);$('#trash-filter').focus();
  }catch(error){$('#trash-error').textContent=error.message;$('#trash-error').hidden=false;sync(true);}
  finally{restoringTrash.delete(id);renderTrash();}
}
function renderSessions(){
  let sessions=state.data.sessions.filter(s=>!s.archived);
  if(state.sessionTab==='running')sessions=sessions.filter(s=>s.status==='inProgress');
  else sessions=sessions.filter(s=>s.kind!=='subagent'||s.status==='inProgress');
  sessions=sessions.sort((a,b)=>(b.status==='inProgress')-(a.status==='inProgress')||b.updatedAt-a.updatedAt).slice(0,12);
  $('#session-list').innerHTML=sessions.length?sessions.map(s=>`<button class="session-item ${s.status==='inProgress'?'running':''} ${state.selected?.id===s.id?'selected':''}" data-session="${s.id}"><i class="session-dot"></i><div><strong title="${esc(s.title)}">${esc(s.title)}</strong><small>${STATUS[s.status]||esc(s.status)} · ${linksForSession(s.id).length} 项技能</small></div></button>`).join(''):'<div class="empty-small muted">现在没有运行中的会话。<br>开始使用 Skill 后，连线会自动出现。</div>';
  $$('[data-session-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.sessionTab===state.sessionTab));
}

function renderActivity(){
  if(!state.data)return;
  const events=(state.data.events||[]).filter(e=>skillById(e.skillId)&&sessionById(e.sessionId)).slice(0,5);
  const signature=JSON.stringify(events);
  if(renderActivity.signature!==signature){
    renderActivity.signature=signature;
    $('#activity-list').innerHTML=events.map(e=>{const skill=skillById(e.skillId),session=sessionById(e.sessionId);return `<div class="activity-item ${e.active?'active':''}"><button class="activity-skill" data-skill="${skill.id}" title="${esc(skill.name)}">${esc(skill.name)}</button><button class="activity-session" data-session="${session.id}" title="${esc(session.title)}">→ ${esc(session.title)}</button><time data-time="${e.time}" datetime="${new Date(e.time).toISOString()}" title="${date(e.time,true)}">${relative(e.time)}</time></div>`;}).join('')||'<p class="empty-small muted">暂无调用记录。使用 Skill 后会自动显示在这里。</p>';
  }else{
    $$('#activity-list time').forEach(el=>el.textContent=relative(Number(el.dataset.time)));
  }
}

function filterData(){
  if(!state.data)return;
  const q=state.query.toLowerCase().trim();
  let skills=state.data.skills.filter(s=>state.source==='all'||(state.source==='low'?skillUsage(s.id)<3:s.source===state.source));
  let sessions=state.data.sessions.filter(s=>!s.archived);
  if(state.scope==='running')sessions=sessions.filter(s=>s.status==='inProgress');
  else if(state.scope==='recent')sessions=sessions.filter(s=>s.kind!=='subagent'||s.status==='inProgress').sort((a,b)=>(b.status==='inProgress')-(a.status==='inProgress')||b.updatedAt-a.updatedAt).slice(0,12);
  else if(state.scope==='skills')sessions=[];
  if(state.selected?.type==='session'&&state.scope!=='skills'){
    const selected=sessionById(state.selected.id);if(selected&&!sessions.some(s=>s.id===selected.id))sessions.unshift(selected);
  }
  if(q){
    const matchingSessions=state.data.sessions.filter(s=>s.title.toLowerCase().includes(q));
    const sessionIds=new Set(matchingSessions.map(s=>s.id));
    const relatedSkills=new Set(state.data.links.filter(l=>sessionIds.has(l.sessionId)).map(l=>l.skillId));
    skills=skills.filter(s=>`${s.name} ${s.description} ${s.plugin} ${s.family}`.toLowerCase().includes(q)||relatedSkills.has(s.id));
    const skillIds=new Set(skills.map(s=>s.id));
    const relatedSessions=new Set(state.data.links.filter(l=>skillIds.has(l.skillId)).map(l=>l.sessionId));
    sessions=state.scope==='skills'?[]:state.data.sessions.filter(s=>sessionIds.has(s.id)||relatedSessions.has(s.id)).filter(s=>state.scope!=='running'||s.status==='inProgress');
  }
  const skillIds=new Set(skills.map(s=>s.id)),sessionIds=new Set(sessions.map(s=>s.id));
  state.links=state.data.links.filter(l=>skillIds.has(l.skillId)&&sessionIds.has(l.sessionId)&&(state.scope!=='running'||l.active));
  updateFlowIndicator();
  state.visibleSkills=skills;state.visibleSessions=sessions;
  $('#graph-title').textContent=state.source==='all'?'全部技能':state.source==='low'?'低频使用':SOURCES[state.source];
  $('#visible-count').textContent=`${skills.length} 个技能 · ${sessions.length} 个会话${state.source==='low'?' · 累计少于 3 次':''}`;
  $('#graph-empty').hidden=!!(skills.length||sessions.length);
  $('#clear-search').hidden=!q;
}

function renderAll(){if(!state.data)return;updateStatus();renderSources();renderSessions();filterData();layout();renderList();renderInspector();renderTrash();}
function renderList(){
  const sorted=[...state.visibleSkills].sort((a,b)=>linksForSkill(b.id).filter(l=>l.active).length-linksForSkill(a.id).filter(l=>l.active).length||a.name.localeCompare(b.name));
  $('#skills-list').innerHTML=sorted.map(s=>{const links=linksForSkill(s.id);return `<button class="skill-card ${state.selected?.id===s.id?'selected':''}" data-skill="${s.id}"><div class="skill-card-title"><i class="source-dot" style="background:var(--source-${s.source})"></i>${esc(s.name)}</div><p>${esc(s.description||'该 Skill 未填写描述，点击查看完整说明。')}</p><footer><span>${esc(s.sourceLabel)}</span><span>${state.source==='low'?`累计 ${skillUsage(s.id)} 次 · `:''}${links.length} 个会话${links.some(l=>l.active)?' · 使用中':''}</span></footer></button>`;}).join('')||'<div class="empty-small">没有匹配的 Skill，请调整搜索或来源筛选。</div>';
}

async function select(type,id){
  graphInput.cancel();setBoxSelection(new Set());
  if(state.section==='trash')showSection('skills');
  state.selected={type,id};state.detailTab='overview';state.detail=null;inspectorOpen(true);
  renderSessions();filterData();layout();renderList();renderInspector();
  const token=++state.detailToken;
  if(type==='skill'){
    try{const r=await fetch(`/api/skill?id=${encodeURIComponent(id)}`);if(!r.ok)throw Error();const data=await r.json();if(token===state.detailToken){state.detail=data;renderInspector();}}
    catch{if(token===state.detailToken)toast('详情暂时无法读取，请重新选择');}
  }
}

function connectionHTML(l,kind){
  const item=kind==='session'?sessionById(l.sessionId):skillById(l.skillId);if(!item)return'';
  const title=kind==='session'?item.title:item.name;
  return `<button class="connection-card ${l.active?'active':''}" data-${kind}="${item.id}"><strong>${esc(title)}</strong><small>${l.active?'当前轮次使用中':`${l.count} 次读取`} · ${date(l.lastSeen)}</small></button>`;
}
function renderInspector(){
  const selected=state.selected;
  if(!selected){clearInspector();return;}
  const isSkill=selected.type==='skill',item=isSkill?skillById(selected.id):sessionById(selected.id);
  if(!item){state.selected=null;clearInspector();return;}
  const links=(isSkill?linksForSkill(item.id):linksForSession(item.id)).sort((a,b)=>b.active-a.active||b.lastSeen-a.lastSeen);
  const header=`<div class="inspector-top"><span>${isSkill?'SKILL DETAILS / 技能详情':'SESSION DETAILS / 会话详情'}</span><button data-action="close" title="关闭详情">×</button></div>`;
  if(!isSkill){
    $('#detail-content').innerHTML=header+`<div class="detail-body"><div class="detail-symbol">◉</div><h2 class="detail-title">${esc(item.title)}</h2><div class="pill-row"><span class="pill">${STATUS[item.status]||esc(item.status)}</span>${item.archived?'<span class="pill">已归档</span>':''}</div><p class="detail-desc">本机会话的 Skill 使用记录。绿色连线表示当前正在运行的轮次中已读取的技能。</p><div class="detail-metrics"><div><strong>${links.length}</strong><span>使用过的技能</span></div><div><strong>${links.filter(l=>l.active).length}</strong><span>当前轮次技能</span></div></div><div class="detail-section"><h3>工作目录</h3><div class="path-box">${esc(item.cwd)}</div></div><div class="detail-section"><h3>关联技能 <span>${links.length}</span></h3>${links.map(l=>connectionHTML(l,'skill')).join('')||`<p class="detail-sub">${item.indexed?'暂未观测到成功读取 SKILL.md 的事件。':'这个会话的历史记录还在索引中。'}</p>`}</div><div class="detail-section"><h3>会话标识</h3><div class="path-box">${esc(item.id)}</div><button class="detail-button" data-action="copy-id">复制会话 ID</button></div><div class="info-note">最近更新 ${date(item.updatedAt,true)}。<br>状态来自本地轮次记录；长时间没有更新的未完成轮次会标为“状态待确认”。</div></div>`;
    return;
  }
  const content=state.detail?.content;
  $('#detail-content').innerHTML=header+`<div class="detail-body"><div class="detail-symbol" style="color:var(--source-${item.source})">✳</div><div class="skill-title-row"><h2 class="detail-title">${esc(item.name)}</h2><button type="button" class="copy-name-icon" data-action="copy-name" aria-label="复制技能名称" title="复制名称"><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 3.5v-1h-8v8h1"/></svg></button></div><div class="skill-actions"><div class="pill-row"><span class="pill" style="color:var(--source-${item.source})">${esc(item.sourceLabel)}</span>${item.plugin?`<span class="pill">${esc(item.plugin)}</span>`:''}${links.some(l=>l.active)?'<span class="pill">● 使用中</span>':''}</div><button type="button" class="skill-delete" data-action="delete-skill"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4.5h11m-9.5 0 .5 9h7l.5-9M6 4.5v-2h4v2M6.5 7v4m3-4v4"/></svg>删除</button></div><div class="detail-tabs"><button class="${state.detailTab==='overview'?'selected':''}" data-detail-tab="overview">概览</button><button class="${state.detailTab==='content'?'selected':''}" data-detail-tab="content">完整 SKILL.md</button></div>${state.detailTab==='overview'?`<p class="detail-desc">${esc(item.description||'该 Skill 未填写描述。')}</p><div class="detail-metrics"><div><strong>${links.length}</strong><span>关联会话</span></div><div><strong>${links.reduce((sum,l)=>sum+l.count,0)}</strong><span>已记录读取</span></div></div><div class="detail-section"><h3>技能来源</h3><div class="source-key">${esc(item.provenance)}${item.version?` · ${esc(item.version)}`:''}</div><div class="path-box">${esc(item.path)}</div><button class="detail-button" data-action="copy-path">⧉ 复制文件路径</button>${item.sourceUrl&&/^https?:\/\//.test(item.sourceUrl)?`<a class="detail-button" target="_blank" rel="noopener noreferrer" href="${esc(item.sourceUrl)}">查看安装来源 ↗</a>`:''}</div><div class="detail-section"><h3>关联会话 <span>${links.length}</span></h3>${links.slice(0,24).map(l=>connectionHTML(l,'session')).join('')||'<p class="detail-sub">还没有发现调用记录。会话读取这项 Skill 后，连线会自动出现在图谱中。</p>'}${links.length>24?`<p class="detail-sub">展示最近 24 个关联会话，共 ${links.length} 个。</p>`:''}</div>${item.copies.length>1?`<div class="detail-section"><h3>本地缓存版本 <span>${item.copies.length}</span></h3>${item.copies.map(c=>`<div class="version-row">${esc(c.version||'本地')}<br>${esc(c.path)}</div>`).join('')}</div>`:''}<div class="info-note">识别依据：成功读取 SKILL.md。<br>目录分类不代表作者身份或当前已启用状态；未观测到连线也不代表从未使用。</div>`:`<button class="detail-button" data-action="copy-content" ${content?'':'disabled'}>⧉ 复制完整说明</button><div class="md-content">${content?markdown(content):'<p>正在读取说明…</p>'}</div>`}</div>`;
}
function clearInspector(){
  $('#detail-content').replaceChildren();
  inspectorOpen(false);
}

let deletion=null,recovery=null;
async function skillOperation(route,body){
  const response=await fetch(`/api/skill/${route}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||'操作未完成，请重试。');
  return data;
}
async function openDeleteDialog(skill,batch=null){
  if(!skill&&!batch?.skills.length)return;
  const target=batch?{ids:batch.skills.map(s=>s.id)}:{id:skill.id};
  const request={target,batch:!!batch,token:null,busy:false,opener:document.activeElement};deletion=request;
  $('#delete-title').textContent=batch?'删除选中的技能':'删除技能';
  $('#delete-description').textContent=(batch?'仅删除技能，会话不会被删除。':'')+'确认后移入回收站，30 天内可恢复，到期后永久删除。';
  $('#delete-skill-name').textContent=batch?`${batch.skills.length} 项技能${batch.sessions?` · ${batch.sessions} 个会话将保留`:''}`:skill.name;
  $('#delete-scope').textContent='正在核对删除范围…';
  $('#delete-error').hidden=true;$('#delete-confirm').disabled=true;$('#delete-confirm').textContent='确认删除';$('#delete-cancel').disabled=false;
  $('#delete-dialog').showModal();$('#delete-cancel').focus();
  try{
    const plan=await skillOperation(batch?'batch-deletion-plan':'deletion-plan',target);
    if(deletion!==request||!$('#delete-dialog').open)return;
    request.token=plan.token;
    const kinds={directory:'技能目录及资源',file:'技能入口文件（保留共享目录）',link:'安装链接（保留链接目标）'};
    const paths=`<ul>${plan.paths.map(p=>`<li><span>${kinds[p.kind]}</span><code>${esc(p.path)}</code></li>`).join('')}</ul>`;
    const selectedIds=new Set(batch?target.ids:[skill.id]);
    const extra=plan.affectedSkills.filter(s=>!selectedIds.has(s.id));
    $('#delete-scope').innerHTML=(batch?`<p class="delete-list-label">选中的技能</p><ul class="delete-skill-list">${plan.selectedSkills.map(s=>`<li>${esc(s.name)}</li>`).join('')}</ul>`:'')+
      (extra.length?`<div class="delete-related"><strong>同时涉及 ${extra.length} 项子技能</strong><p>以下技能位于所选目录内，也会一起移入回收站：${extra.map(s=>esc(s.name)).join('、')}</p></div>`:'')+
      (batch?`<details class="delete-paths"><summary>查看 ${plan.paths.length} 个文件位置</summary>${paths}</details>`:`<p>将移入回收区的 ${plan.paths.length} 个位置：</p>${paths}`)+
      `<p class="delete-note">确认后这些技能将从看板移除，Codex 后续也将无法从这些位置加载。${plan.managed?'内置或插件技能可能在应用、插件更新后重新出现。':''}</p>`;
    if(batch)$('#delete-confirm').textContent=`确认删除 ${plan.affectedSkills.length} 项技能`;
    $('#delete-confirm').disabled=false;
  }catch(error){if(deletion===request){$('#delete-scope').textContent='';$('#delete-error').textContent=error.message;$('#delete-error').hidden=false;}}
}
function closeDeleteDialog(){
  if(deletion?.busy)return;
  const opener=deletion?.opener;deletion=null;$('#delete-dialog').close();
  if(opener?.isConnected)opener.focus();else $('[data-action="delete-skill"]')?.focus();
}
function applyMutation(data){
  graphInput.cancel();setBoxSelection(new Set());
  mutationVersion++;state.detailToken++;state.data=data.graph;
  state.selected=null;state.detail=null;state.hover=null;state.fitted=false;
  sync.fingerprint=null;renderAll();renderActivity();fit();
}
async function confirmDelete(){
  const request=deletion;if(!request?.token||request.busy)return;
  request.busy=true;mutationVersion++;
  $('#delete-confirm').disabled=true;$('#delete-confirm').textContent='正在移入回收区…';$('#delete-cancel').disabled=true;$('#delete-error').hidden=true;
  try{
    const result=await skillOperation(request.batch?'batch-delete':'delete',{...request.target,token:request.token,confirmed:true});
    deletion=null;$('#delete-dialog').close();applyMutation(result);
    recovery=result;$('#recovery-message').textContent=`已删除 ${result.name}`;
    $('#recovery-path').textContent='已移入回收站 · 30 天内可恢复，到期后永久删除';
    $('#undo-delete').disabled=false;$('#undo-delete').textContent='撤销';$('#recovery-notice').hidden=false;
    $('#undo-delete').focus();
  }catch(error){
    request.busy=false;request.token=null;$('#delete-confirm').textContent='确认删除';$('#delete-cancel').disabled=false;
    $('#delete-error').textContent=error.message+' 请关闭窗口后重试。';$('#delete-error').hidden=false;
  }
}
async function undoDelete(){
  if(!recovery)return;const item=recovery;
  $('#undo-delete').disabled=true;$('#undo-delete').textContent='恢复中…';mutationVersion++;
  try{
    const result=await skillOperation('restore',{trashId:item.trashId});applyMutation(result);
    recovery=null;$('#recovery-notice').hidden=true;toast(`已恢复 ${result.name}`);$('#scope').focus();
  }catch(error){$('#recovery-message').textContent=error.message;$('#undo-delete').disabled=false;$('#undo-delete').textContent='重试恢复';}
}


function markdown(raw){
  const text=raw.replace(/^---[\s\S]*?\n---\s*\n/,'');
  const lines=text.split('\n');let out='',code=false,buf=[];
  function inline(s){return esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
  for(const l of lines){if(/^\s*```/.test(l)){if(code){out+=`<pre><code>${esc(buf.join('\n'))}</code></pre>`;buf=[];}code=!code;continue;}if(code){buf.push(l);continue;}if(/^#{1,6} /.test(l)){const n=Math.min(l.match(/^#+/)[0].length,4);out+=`<h${n}>${inline(l.replace(/^#+ /,''))}</h${n}>`;}else if(/^\s*[-*+] /.test(l)||/^\s*\d+\. /.test(l)){out+=`<div class="md-li">${inline(l.replace(/^\s*(?:[-*+]|\d+\.) /,''))}</div>`;}else if(l.startsWith('> ')){out+=`<blockquote>${inline(l.slice(2))}</blockquote>`;}else if(l.trim()){out+=`<p>${inline(l)}</p>`;}}
  if(buf.length)out+=`<pre><code>${esc(buf.join('\n'))}</code></pre>`;return out;
}

// A stable spatial atlas. All edges come from observations, never similarity.
const canvas=$('#graph'),ctx=canvas.getContext('2d');let width=0,height=0,dpr=1;
const positionSets={compact:new Map(),expanded:new Map()};
let savedPositions=positionSets.compact;
let graphInput=null,layoutPending=false;
function nodeBounds(nodes){
  return {left:Math.min(...nodes.map(n=>n.x-n.r)),right:Math.max(...nodes.map(n=>n.x+n.r)),top:Math.min(...nodes.map(n=>n.y-n.r)),bottom:Math.max(...nodes.map(n=>n.y+n.r))};
}
function expandSkillGroups(nodes,groups,sessionNodes){
  const sessions=nodeBounds(sessionNodes),midY=(sessions.top+sessions.bottom)/2;
  for(const group of groups){
    const skills=nodes.filter(n=>n.type==='skill'&&n.data.source===group.source),bounds=nodeBounds(skills);
    const upper=group.source==='builtin'||group.source==='plugin';
    const left=group.source==='builtin'||group.source==='local';
    const rowOffset=Math.max(170,(sessions.bottom-sessions.top)*.32,(bounds.bottom-bounds.top)/2+70);
    // Keep the entire skill cluster beyond the session cloud, including large circles.
    const dx=group.source==='project'?(sessions.left+sessions.right-bounds.left-bounds.right)/2:
      left?sessions.left-100-bounds.right:sessions.right+100-bounds.left;
    const dy=group.source==='project'?sessions.bottom+130-bounds.top:
      midY+(upper?-rowOffset:rowOffset)-(bounds.top+bounds.bottom)/2;
    group.x+=dx;group.y+=dy;
    for(const skill of skills){skill.x+=dx;skill.y+=dy;}
  }
}
function layout(){
  if(!state.data)return;
  if(graphInput?.gesture){layoutPending=true;return;}
  layoutPending=false;
  state.expandedLayout=state.visibleSessions.length>24;
  savedPositions=positionSets[state.expandedLayout?'expanded':'compact'];
  const usageCounts=new Map(state.data.skills.map(s=>[s.id,0]));
  for(const link of state.data.links)usageCounts.set(link.skillId,(usageCounts.get(link.skillId)||0)+link.count);
  const maxUsage=Math.max(1,...usageCounts.values());
  const clusters={builtin:{x:-440,y:-165},local:{x:-440,y:160},installed:{x:450,y:170},plugin:{x:420,y:-165},project:{x:0,y:390}};
  const nodes=[],groups=[];
  for(const source of Object.keys(SOURCES)){
    const skills=state.visibleSkills.filter(s=>s.source===source).sort((a,b)=>a.family.localeCompare(b.family)||a.name.localeCompare(b.name));
    if(!skills.length)continue;
    const center=clusters[source];const radius=Math.max(110,Math.sqrt(skills.length)*29);
    groups.push({source,...center,radius,count:skills.length});
    skills.forEach((s,i)=>{
      const angle=i*2.3999632297+(hash(source)%10);const r=Math.sqrt((i+.5)/skills.length)*radius;
      const active=state.data.links.some(l=>l.skillId===s.id&&l.active),used=usageCounts.get(s.id)||0;
      const offset=savedPositions.get(s.id)||{x:Math.cos(angle)*r,y:Math.sin(angle)*r*.52};
      const pos={x:center.x+offset.x,y:center.y+offset.y};
      // A global square-root scale keeps frequency comparable across filters.
      // The minimum keeps unused skills visible; current activity uses a halo only.
      nodes.push({id:s.id,type:'skill',data:s,...pos,r:4.8+26*Math.sqrt(used/maxUsage),color:COLORS[source],active,used});
    });
  }
  // Give larger skill circles enough room without moving manually placed nodes.
  for(let pass=0;pass<36;pass++){
    for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
      const a=nodes[i],b=nodes[j];if(a.data.source!==b.data.source)continue;
      const dx=b.x-a.x,dy=b.y-a.y,distance=Math.hypot(dx,dy)||.01,spacing=a.r+b.r+7;
      if(distance>=spacing)continue;
      const aFixed=savedPositions.has(a.id),bFixed=savedPositions.has(b.id);if(aFixed&&bFixed)continue;
      const shift=(spacing-distance)/(aFixed||bFixed?1:2),ux=dx/distance||1,uy=dy/distance;
      if(!aFixed){a.x-=ux*shift;a.y-=uy*shift;}if(!bFixed){b.x+=ux*shift;b.y+=uy*shift;}
    }
  }
  const sessions=state.visibleSessions;
  sessions.forEach((s,i)=>{
    const angle=i*2.3999632297-.5;const r=sessions.length>24?Math.sqrt(i+1)*35:Math.sqrt(i+1)*58;
    const pos=savedPositions.get(s.id)||{x:Math.cos(angle)*r*.78,y:Math.sin(angle)*r*.58-12};
    nodes.push({id:s.id,type:'session',data:s,...pos,r:s.status==='inProgress'?20:13,color:s.status==='inProgress'?'#326d50':'#74816c',active:s.status==='inProgress'});
  });
  if(state.expandedLayout)expandSkillGroups(nodes,groups,nodes.filter(n=>n.type==='session'));
  for(const node of nodes)if(node.type==='skill')node.layoutCenter=groups.find(g=>g.source===node.data.source);
  state.nodes=nodes;state.nodeMap=new Map(nodes.map(n=>[n.id,n]));state.groups=groups;state.version++;
  setBoxSelection(new Set([...state.boxSelected].filter(id=>state.nodeMap.has(id))));
  if(!state.fitted&&nodes.length){fit();state.fitted=true;}
}
function resize(){
  const rect=$('#graph-container').getBoundingClientRect();width=rect.width;height=rect.height;dpr=Math.min(window.devicePixelRatio||1,2);
  canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=width+'px';canvas.style.height=height+'px';
  if(!state.fitted&&state.nodes.length)fit();
}
new ResizeObserver(resize).observe($('#graph-container'));
function fit(){
  graphInput?.cancel({releaseSpace:false});
  if(!width||!height||!state.nodes.length)return;
  const xs=state.nodes.map(n=>n.x),ys=state.nodes.map(n=>n.y);
  const minX=Math.min(...xs)-75,maxX=Math.max(...xs)+90,minY=Math.min(...ys)-95,maxY=Math.max(...ys)+65;
  state.scale=Math.max(.12,Math.min((width-40)/(maxX-minX),(height-65)/(maxY-minY),1.7));
  state.pan={x:width/2-(minX+maxX)/2*state.scale,y:height/2-(minY+maxY)/2*state.scale};zoomLabel();
}
function zoomLabel(){$('#zoom-level').textContent=Math.round(state.scale*100)+'%';}
function zoom(factor,cx=width/2,cy=height/2){graphInput.cancel({releaseSpace:false});const next=Math.max(.12,Math.min(4,state.scale*factor));const f=next/state.scale;state.pan.x=cx-(cx-state.pan.x)*f;state.pan.y=cy-(cy-state.pan.y)*f;state.scale=next;zoomLabel();}
function screen(n){return{x:n.x*state.scale+state.pan.x,y:n.y*state.scale+state.pan.y};}
function nodeRadius(n){return n.type==='session'?Math.max(state.expandedLayout&&!n.active?3.5:8,n.r*state.scale):Math.max(3,4.8*state.scale)+(n.r-4.8)*state.scale;}
function roundRect(x,y,w,h,r){ctx.beginPath();ctx.roundRect(x,y,w,h,r);}
function curvePoint(p,m,q,t){const u=1-t;return{x:u*u*p.x+2*u*t*m.x+t*t*q.x,y:u*u*p.y+2*u*t*m.y+t*t*q.y};}
function updateFlowIndicator(){
  document.body.classList.toggle('has-live-flow',state.live&&state.links.some(l=>l.active));
  document.body.classList.toggle('has-history-flow',state.live&&state.links.some(l=>!l.active));
}
function drawFlowBand(p,m,q,head,span,color,opacity,lineWidth){
  const start=Math.max(0,head-span),end=Math.min(1,head);
  if(end<=start)return;
  const a=curvePoint(p,m,q,start),b=curvePoint(p,m,q,end);
  // Restrict the same quadratic curve so the light stays exactly on its track.
  const control={
    x:a.x+(end-start)*((1-start)*(m.x-p.x)+start*(q.x-m.x)),
    y:a.y+(end-start)*((1-start)*(m.y-p.y)+start*(q.y-m.y))
  };
  const tail=curvePoint(p,m,q,head-span),tip=curvePoint(p,m,q,head);
  const gradient=ctx.createLinearGradient(tail.x,tail.y,tip.x,tip.y);
  for(const [position,alpha] of [[0,0],[.18,.025],[.42,.15],[.65,.42],[.83,.82],[.91,1],[.97,.55],[1,0]]){
    gradient.addColorStop(position,`rgba(${color},${alpha})`);
  }
  ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(control.x,control.y,b.x,b.y);
  ctx.strokeStyle=gradient;
  ctx.globalAlpha=opacity*.07;ctx.lineWidth=lineWidth+2.4;ctx.stroke();
  ctx.globalAlpha=opacity*.13;ctx.lineWidth=lineWidth+1;ctx.stroke();
  ctx.globalAlpha=opacity;ctx.lineWidth=lineWidth;ctx.stroke();
}
function drawConnection(link,source,target,selected,clock){
  const p=screen(source),q=screen(target),highlight=selected.has(link.skillId)||selected.has(link.sessionId);
  const active=link.active,dim=selected.size>0&&!highlight;
  const length=Math.hypot(q.x-p.x,q.y-p.y);
  const bend=(hash(link.skillId)%2?1:-1)*Math.min(40,length*.1),m={x:(p.x+q.x)/2+bend,y:(p.y+q.y)/2-bend};
  ctx.save();ctx.lineCap='round';ctx.setLineDash([]);
  // Keep the entire connection visible underneath the moving light band.
  ctx.globalAlpha=active?(dim?.32:.65):(dim?.18:highlight?.65:.42);
  ctx.strokeStyle=active?'#67aa89':'#8c9f82';ctx.lineWidth=active?.85:highlight?.8:.6;
  ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.quadraticCurveTo(m.x,m.y,q.x,q.y);ctx.stroke();
  if(!reduced&&length>1){
    // One continuous, softly tapered light band per link, matching the reference.
    const span=Math.min(.38,Math.max(.18,100/length));
    const duration=active?Math.max(2600,Math.min(5500,length/155*1000)):Math.max(3200,Math.min(7000,length/120*1000));
    const offset=(hash(link.skillId+link.sessionId)%1000)/1000;
    const head=((clock/duration+offset)%1)*(1+span);
    const opacity=active?(dim?.35:1):(dim?.07:highlight?.95:.45);
    drawFlowBand(p,m,q,head,span,active?'38,151,105':'101,134,83',opacity,active?1.25:highlight?1.1:.85);
  }
  ctx.restore();
}
let lastFrame=0,flowClock=0;const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
function draw(time){
  requestAnimationFrame(draw);if(time-lastFrame<15||state.section==='trash'||state.view!=='graph'||document.hidden)return;
  const elapsed=Math.min(80,time-lastFrame);lastFrame=time;if(state.live&&!reduced)flowClock+=elapsed;
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
  const boxing=graphInput?.gesture?.mode==='box'&&graphInput.gesture.moved;
  const selected=boxing||state.boxSelected.size?state.boxSelected:new Set(state.selected?[state.selected.id]:[]);
  const related=new Set(selected);
  if(selected.size){for(const l of state.links)if(selected.has(l.skillId)||selected.has(l.sessionId)){related.add(l.skillId);related.add(l.sessionId);}}
  for(const g of state.groups){
    const p=screen(g);const radius=g.radius*state.scale;
    const grad=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,Math.max(1,radius*1.4));grad.addColorStop(0,COLORS[g.source]+'08');grad.addColorStop(1,COLORS[g.source]+'00');ctx.fillStyle=grad;ctx.beginPath();ctx.ellipse(p.x,p.y,radius*1.45,radius*.8,0,0,Math.PI*2);ctx.fill();
    ctx.globalAlpha=selected.size?.55:1;ctx.textAlign='center';ctx.fillStyle=COLORS[g.source]+'aa';ctx.font='8px -apple-system, sans-serif';ctx.fillText(`${SOURCES[g.source]}  /  ${g.count}`,p.x,p.y-radius*.6-19);ctx.font='5.5px -apple-system, sans-serif';ctx.fillStyle='#a8b69b';ctx.fillText(SOURCE_EN[g.source],p.x,p.y-radius*.6-9);ctx.globalAlpha=1;
  }
  for(const l of state.links){
    const a=state.nodeMap.get(l.skillId),b=state.nodeMap.get(l.sessionId);if(!a||!b)continue;
    drawConnection(l,a,b,selected,flowClock);
  }
  ctx.globalAlpha=1;const labels=[];
  const ordered=[...state.nodes].sort((a,b)=>Number(selected.has(a.id))-Number(selected.has(b.id))||(a.type==='session')-(b.type==='session'));
  for(const n of ordered){
    const p=screen(n),r=nodeRadius(n);if(p.x< -150||p.x>width+150||p.y< -30||p.y>height+30)continue;
    const emphasis=selected.has(n.id)||n.id===state.hover;const dim=selected.size>0&&!related.has(n.id);ctx.globalAlpha=dim?.4:1;
    if(n.active){
      const pulse=reduced?0:Math.sin(flowClock/650)*2;ctx.beginPath();ctx.arc(p.x,p.y,r+5+pulse,0,Math.PI*2);ctx.fillStyle=n.type==='skill'?n.color+'20':'#38ad781a';ctx.fill();
      if(!reduced){const phase=(flowClock/2000+(hash(n.id)%100)/100)%1;ctx.save();ctx.globalAlpha=(dim?.4:1)*(1-phase)*.5;ctx.strokeStyle='#55b486';ctx.lineWidth=1.2;ctx.beginPath();ctx.arc(p.x,p.y,r+4+phase*12,0,Math.PI*2);ctx.stroke();ctx.restore();}
    }
    if(emphasis){ctx.beginPath();ctx.arc(p.x,p.y,r+5,0,Math.PI*2);ctx.strokeStyle=n.color+'77';ctx.lineWidth=1;ctx.stroke();}
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fillStyle=n.color;ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=n.type==='session'?2:1.1;ctx.stroke();
    if(n.type==='session'&&r>8){ctx.fillStyle='#ffffffe0';ctx.font=`${Math.max(8,r*.95)}px -apple-system`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(n.active?'✳':'·',p.x,p.y);ctx.textBaseline='alphabetic';}
    labels.push({n,p,r,emphasis,dim});
  }
  ctx.globalAlpha=1;const occupied=[];
  labels.sort((a,b)=>Number(b.emphasis)-Number(a.emphasis)||(b.n.type==='session')-(a.n.type==='session')||Number(b.n.active)-Number(a.n.active)||b.n.used-a.n.used);
  for(const {n,p,r,emphasis,dim} of labels){
    const session=n.type==='session';if(dim&&!emphasis&&state.scale<.25)continue;
    const full=session?n.data.title:n.data.name;const text=emphasis?short(full,44):short(full,session?18:24);
    const size=session?9:Math.min(11,Math.max(7.3,state.scale*11));ctx.font=`${session||emphasis?'500':'400'} ${size}px -apple-system, "PingFang SC", sans-serif`;const w=ctx.measureText(text).width;
    const box={x:p.x-w/2-2,y:p.y+r+4,w:w+4,h:size+4};
    if(!emphasis&&occupied.some(o=>box.x<o.x+o.w&&box.x+box.w>o.x&&box.y<o.y+o.h&&box.y+box.h>o.y))continue;
    if(box.x+box.w<0||box.x>width||box.y>height)continue;
    occupied.push(box);ctx.globalAlpha=dim?.4:1;
    ctx.textAlign='center';ctx.lineWidth=3;ctx.strokeStyle='#fdfefbe8';ctx.strokeText(text,p.x,box.y+size);ctx.fillStyle=emphasis?'#344e2b':session?'#6c7c61':'#909e84';ctx.fillText(text,p.x,box.y+size);
    if(n.active&&session){ctx.font='6px -apple-system';ctx.fillStyle='#73a451';ctx.fillText('RUNNING',p.x,box.y+size+10);}
  }
  ctx.globalAlpha=1;
}
function hit(x,y,precise=false){
  let target=null,dist=Infinity;
  for(const n of state.nodes){
    const p=screen(n),d=Math.hypot(p.x-x,p.y-y),radius=precise?nodeRadius(n):Math.max(9,nodeRadius(n)+5);
    if(d<=radius&&d<dist){target=n;dist=d;}
  }
  return target;
}
function setBoxSelection(ids){
  state.boxSelected=ids;
  const nodes=state.nodes.filter(n=>ids.has(n.id)),skills=nodes.filter(n=>n.type==='skill').length,sessions=nodes.length-skills;
  $('#selection-status').hidden=!nodes.length;
  $('#selection-count').textContent='已选 '+[skills?`${skills} 个技能`:'',sessions?`${sessions} 个会话`:''].filter(Boolean).join(' · ');
  $('#delete-selection').hidden=state.section!=='skills'||state.source!=='low'||!skills;
}
function deleteSelectedSkills(){
  if(state.section!=='skills'||state.source!=='low')return;
  const nodes=state.nodes.filter(n=>state.boxSelected.has(n.id));
  const skills=nodes.filter(n=>n.type==='skill').map(n=>skillById(n.id)).filter(s=>s&&skillUsage(s.id)<3);
  if(!skills.length){toast('请先框选低频使用的技能');return;}
  openDeleteDialog(null,{skills,sessions:nodes.filter(n=>n.type==='session').length});
}
function dismissGraphDetail(){
  state.selected=null;state.detail=null;state.detailToken++;
  inspectorOpen(false,true);renderInspector();if(state.data){renderSessions();renderList();}
}
function clearGraphSelection(){graphInput.cancel();setBoxSelection(new Set());dismissGraphDetail();}
function updateGraphCursor(){canvas.style.cursor=graphInput.cursor(!!state.hover);}
function pointerPoint(e){const rect=canvas.getBoundingClientRect();return{x:e.clientX-rect.left,y:e.clientY-rect.top};}
function hideGraphTooltip(){state.hover=null;$('#tooltip').hidden=true;}
graphInput=new GraphInput({
  getView:()=>({pan:state.pan,scale:state.scale}),getNodes:()=>state.nodes,getSelection:()=>state.boxSelected,
  getNodeGeometry:n=>({...screen(n),radius:nodeRadius(n)}),
  setPan:pan=>state.pan=pan,setSelection:setBoxSelection,
  moveNodes:positions=>{for(const pos of positions){const n=state.nodeMap.get(pos.id);if(!n)continue;n.x=pos.x;n.y=pos.y;const origin=n.layoutCenter||{x:0,y:0};savedPositions.set(n.id,{x:n.x-origin.x,y:n.y-origin.y});}},
  showBox:box=>{const el=$('#selection-rectangle');el.hidden=!box;if(box)Object.assign(el.style,{left:box.x+'px',top:box.y+'px',width:box.width+'px',height:box.height+'px'});},
  updateCursor:updateGraphCursor,onClickNode:n=>select(n.type,n.id),onClickBlank:clearGraphSelection,
  onSelectBox:dismissGraphDetail,
  onFinish:pointerId=>{if(canvas.hasPointerCapture(pointerId))canvas.releasePointerCapture(pointerId);if(layoutPending)layout();}
});
canvas.addEventListener('pointerdown',e=>{
  if(!e.isPrimary||e.button!==0)return;
  const point=pointerPoint(e),dragNode=hit(point.x,point.y,true),clickNode=dragNode||hit(point.x,point.y);
  if(graphInput.down(point,clickNode,{pointerId:e.pointerId,button:e.button,additive:e.shiftKey,dragNode})){
    e.preventDefault();canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);hideGraphTooltip();
  }
});
canvas.addEventListener('pointermove',e=>{
  const p=pointerPoint(e);
  if(graphInput.gesture){graphInput.move(p,e.pointerId);return;}
  if(graphInput.spaceHeld){hideGraphTooltip();updateGraphCursor();return;}
  const n=hit(p.x,p.y);state.hover=n?.id;updateGraphCursor();$('#tooltip').hidden=!n;
  if(n){$('#tooltip').innerHTML=`${esc(n.type==='skill'?n.data.name:n.data.title)}<small>${n.type==='skill'?`${esc(n.data.sourceLabel)} · 累计 ${n.used} 次使用 · ${linksForSkill(n.id).length} 个会话`:STATUS[n.data.status]||'会话'}</small>`;$('#tooltip').style.left=Math.min(p.x+15,width-240)+'px';$('#tooltip').style.top=Math.max(8,Math.min(p.y+15,height-80))+'px';}
});
canvas.addEventListener('pointerup',e=>graphInput.up(pointerPoint(e),e.pointerId));
for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,e=>{if(graphInput.gesture?.pointerId===e.pointerId)graphInput.cancel();});
canvas.addEventListener('pointerleave',()=>{hideGraphTooltip();updateGraphCursor();});
canvas.addEventListener('wheel',e=>{e.preventDefault();const p=pointerPoint(e);zoom(Math.exp(-e.deltaY*.0014),p.x,p.y);},{passive:false});
canvas.addEventListener('dblclick',e=>{if(!graphInput.spaceHeld){const p=pointerPoint(e);zoom(1.4,p.x,p.y);}});
canvas.addEventListener('keydown',e=>{
  if(e.key==='+'||e.key==='='){e.preventDefault();zoom(1.2);}
  if(e.key==='-'){e.preventDefault();zoom(1/1.2);}
  if(e.key==='0'){e.preventDefault();fit();}
});
function graphSpaceAllowed(e){
  return state.section==='skills'&&state.view==='graph'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&
    !document.querySelector('dialog[open]')&&!e.target.closest('input,textarea,select,button,a,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="menuitemradio"]');
}
document.addEventListener('keydown',e=>{
  if(e.code==='Space'&&graphSpaceAllowed(e)){e.preventDefault();graphInput.setSpace(true);hideGraphTooltip();}
});
document.addEventListener('keyup',e=>{if(e.code==='Space')graphInput.setSpace(false);});
window.addEventListener('blur',()=>{graphInput.cancel();hideGraphTooltip();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){graphInput.cancel();hideGraphTooltip();}});
document.addEventListener('focusin',e=>{if(e.target!==canvas)graphInput.cancel();});
$('#clear-selection').onclick=()=>{clearGraphSelection();canvas.focus({preventScroll:true});};
$('#delete-selection').onclick=deleteSelectedSkills;

document.addEventListener('click',async e=>{
  const trash=e.target.closest('[data-section="trash"]');if(trash){showSection('trash');return;}
  const restore=e.target.closest('[data-restore]');if(restore){restoreTrash(restore.dataset.restore);return;}
  const skill=e.target.closest('[data-skill]');if(skill){select('skill',skill.dataset.skill);return;}
  const session=e.target.closest('[data-session]');if(session){select('session',session.dataset.session);return;}
  const source=e.target.closest('[data-source]');if(source){state.source=source.dataset.source;state.fitted=false;showSection('skills');renderAll();return;}
  const view=e.target.closest('[data-view]');if(view){graphInput.cancel();state.view=view.dataset.view;$$('[data-view]').forEach(b=>b.classList.toggle('selected',b===view));$('#graph-container').hidden=state.view!=='graph';$('#skills-list').hidden=state.view!=='list';resize();return;}
  const tab=e.target.closest('[data-session-tab]');if(tab){state.sessionTab=tab.dataset.sessionTab;renderSessions();return;}
  const detailTab=e.target.closest('[data-detail-tab]');if(detailTab){state.detailTab=detailTab.dataset.detailTab;renderInspector();return;}
  const action=e.target.closest('[data-action]')?.dataset.action;if(!action)return;
  if(action==='close'){inspectorOpen(false);return;}
  if(action==='delete-skill'){openDeleteDialog(skillById(state.selected.id));return;}
  let value='';if(action==='copy-name')value=skillById(state.selected.id)?.name;if(action==='copy-path')value=skillById(state.selected.id)?.path;if(action==='copy-id')value=state.selected.id;if(action==='copy-content')value=state.detail?.content;
  if(value){try{await navigator.clipboard.writeText(value);toast('已复制');}catch{toast('浏览器未允许复制，请手动选择文字');}}
});
$('#search').addEventListener('input',e=>{state.query=e.target.value;filterData();layout();renderList();fit();});
$('#trash-search').addEventListener('input',e=>{state.trashQuery=e.target.value;renderTrash();});
$('#clear-search').onclick=()=>{$('#search').value='';state.query='';filterData();layout();renderList();fit();};
$('#scope').onclick=()=>$('#scope-menu').hidden?openScope():closeScope();
$('#scope').addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();openScope(e.key==='ArrowDown'?'first':'last');}
});
$('#scope-menu').addEventListener('click',e=>{const option=e.target.closest('[data-scope]');if(option)chooseScope(option.dataset.scope);});
$('.scope-picker').addEventListener('keydown',e=>{
  if($('#scope-menu').hidden)return;
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeScope(true);return;}
  if(e.key==='Tab'){
    // Return to the trigger before the browser moves focus to the next control.
    closeScope(true);return;
  }
  if(!e.target.matches('[data-scope]'))return;
  const options=$$('[data-scope]'),index=options.indexOf(e.target);
  let next;if(e.key==='ArrowDown')next=(index+1)%options.length;
  if(e.key==='ArrowUp')next=(index-1+options.length)%options.length;
  if(e.key==='Home')next=0;if(e.key==='End')next=options.length-1;
  if(next!==undefined){e.preventDefault();options[next].focus();}
});
document.addEventListener('pointerdown',e=>{if(!e.target.closest('.scope-picker'))closeScope();});
$('.scope-picker').addEventListener('focusout',e=>{if(!e.currentTarget.contains(e.relatedTarget))closeScope();});
$('#live-button').onclick=()=>{state.live=!state.live;$('#live-button').classList.toggle('paused',!state.live);$('#live-button span').textContent=state.live?'实时同步':'已暂停';updateFlowIndicator();if(state.live)sync(true);else updateStatus();};
$('#reset-filters').onclick=reset;
$('#delete-cancel').onclick=closeDeleteDialog;$('#delete-confirm').onclick=confirmDelete;
$('#delete-dialog').addEventListener('cancel',e=>{e.preventDefault();closeDeleteDialog();});
$('#delete-dialog').addEventListener('keydown',e=>{if(e.key==='Escape')e.stopPropagation();});
$('#delete-dialog').addEventListener('click',e=>{if(e.target===$('#delete-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDeleteDialog();}});
$('#undo-delete').onclick=undoDelete;$('#dismiss-recovery').onclick=()=>$('#recovery-notice').hidden=true;
$('#inspector-toggle').onclick=()=>inspectorOpen(!state.inspector);
$('#focus-button').onclick=()=>{const enabled=document.body.classList.toggle('focus-mode');$('#focus-button').setAttribute('aria-pressed',String(enabled));$('#focus-button').title=enabled?'退出展开':'展开图谱';requestAnimationFrame(()=>{resize();fit();});};
$('#zoom-in').onclick=()=>zoom(1.25);$('#zoom-out').onclick=()=>zoom(.8);$('#fit-button').onclick=fit;
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();const input=state.section==='trash'?$('#trash-search'):$('#search');input.focus();input.select();}if(e.key==='Escape'){clearGraphSelection();$('#search').blur();}});
palette=new SourcePalette({defaults:DEFAULT_COLORS,colors:COLORS,sources:SOURCES,onChange:(source,color)=>{for(const n of state.nodes)if(n.type==='skill'&&n.data.source===source)n.color=color;}});
if(window.innerWidth<1000)inspectorOpen(false);
clearInspector();resize();requestAnimationFrame(draw);sync();setInterval(sync,2000);
