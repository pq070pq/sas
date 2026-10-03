const tg=window.Telegram&&window.Telegram.WebApp;
if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#020912');tg.setBackgroundColor('#020912');}catch(e){}}
const getInitData=()=>tg?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')||new URLSearchParams(location.search).get('tgWebAppData')||'';
const headers=()=>({'X-Telegram-Init-Data':getInitData()});
async function api(path,opt={}){opt.headers=Object.assign(headers(),opt.headers||{});const r=await fetch(path,opt);if(!r.ok){let msg='تعذر تنفيذ العملية';try{const d=await r.json();msg=d.detail||d.message||msg;}catch(e){try{const t=(await r.text()).trim();if(t)msg=t;}catch(_){} }throw new Error(msg);}return r.json();}
const fmtDate=v=>v?new Date(v).toLocaleDateString('ar-SA'):'—';
const fmtDays=(a,b)=>{if(!a||!b)return'—';const n=Math.ceil((new Date(b)-new Date(a))/86400000);return n>0?n+' يوم':'منتهي';};
let me=null,plans=null,termAction=null;
let loadStarted=false;

async function load(){
 if(loadStarted)return;
 loadStarted=true;
 try{
  me=await api('/api/me');
  if(me.admin){
   document.getElementById('subscriptionPage').hidden=true;
   document.getElementById('adminPage').hidden=false;
   if((me.admin_permissions||[]).includes('admins')){document.getElementById('staffPanel').hidden=false;await loadStaff();}
   await adminRefresh();return;
  }
  if(me.pro){
   document.getElementById('subscriptionPage').hidden=true;
   document.getElementById('terminalPage').hidden=false;
   await initTerminal();
   return;
  }
  document.getElementById('userName').textContent=me.user?.first_name||me.user?.username||'مستخدم SAS PRO';
  renderStatus(me);
  const cfg=await api('/api/subscription/config');
  plans=await api('/api/subscription/plans');
  renderTrialCard(cfg.trial_days);
  document.getElementById('paidPlansSection').hidden=!cfg.paid_plans_visible;
  renderPlans(plans.plans||{});
 }catch(e){
  document.body.innerHTML='<div class="fatal">تعذر التحقق من Telegram. افتح SAS PRO من داخل Telegram.</div>';
 }
}

const terminalState={ticker:[],radar:[],watch:JSON.parse(localStorage.getItem('saspro_watchlist')||'[]'),timer:null,tab:'dashboard'};
function escHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}
function money(v){const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{minimumFractionDigits:n<10?2:0,maximumFractionDigits:4}):'—';}
function pct(v){const n=Number(v);return Number.isFinite(n)?(n>=0?'+':'')+n.toFixed(2)+'%':'—';}
function switchTerminalTab(tab){
 terminalState.tab=tab;
 document.querySelectorAll('.terminal-tab').forEach(x=>x.classList.toggle('active',x.id==='tab-'+tab));
 document.querySelectorAll('.terminal-tabs button').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
 if(tab==='radar')renderRadar();
 if(tab==='watch')renderWatchlist();
}
async function initTerminal(){
 document.getElementById('terminalGreeting').textContent='مرحبًا '+(me.user?.first_name||me.user?.username||'في SAS PRO');
 document.getElementById('accountName').textContent=me.user?.first_name||me.user?.username||'مستخدم SAS PRO';
 document.getElementById('accountPlan').textContent=(me.user?.plan||'SAS PRO')+' • وصول فعّال';
 renderAccount();
 await refreshTerminal();
 terminalState.timer=setInterval(refreshTerminal,20000);
}
async function refreshTerminal(){
 document.getElementById('terminalClock').textContent=new Date().toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'});
 try{
  const [status,home,ticker]=await Promise.all([api('/api/market/radar-status'),api('/api/dashboard/home'),api('/api/market/ticker')]);
  terminalState.ticker=ticker||[];
  renderMarketStrip(status);
  renderDashboard(home);
  renderMacro();
  if(terminalState.tab==='radar') await runRadar(false);
  if(terminalState.tab==='watch') renderWatchlist();
 }catch(e){document.getElementById('radarStatusText').textContent='تعذر تحديث بيانات السوق: '+e.message;}
}
function renderMarketStrip(s){
 const label=s.open?'🟢 السوق مفتوح':'🔴 السوق مغلق';
 document.getElementById('marketStrip').innerHTML='<div class="market-state '+(s.open?'open':'closed')+'"><b>'+label+'</b><span>'+escHtml(s.label_ar||'')+'</span></div><div class="market-state"><b>📡 الرادار</b><span>'+(s.stock_radar_enabled?'يعمل':'متوقف')+'</span></div><div class="market-state"><b>🕒 الجلسة</b><span>'+escHtml(s.session||'—')+'</span></div>';
}
function renderDashboard(d){
 const r=d.radar||{};
 if(Array.isArray(r.stocks)&&r.stocks.length) terminalState.radar=r.stocks;
 const historical=Boolean(r.historical);
 document.getElementById('radarStatusText').textContent=r.enabled
  ? '🟢 الرصد الآلي يعمل — يبحث عن الأسهم التي تستوفي بوابة SAS PRO.'
  : (historical ? '🟡 السوق مغلق — معروض آخر رصد محفوظ من آخر جلسة.' : '🔴 الرصد متوقف حاليًا خارج جلسة الأسهم الأمريكية.');
 document.getElementById('dashboardMetrics').innerHTML=
  '<div><small>'+(historical?'آخر جلسة':'فرص اليوم')+'</small><strong>'+Number(r.opportunities||terminalState.radar.length||0)+'</strong></div>'+
  '<div><small>أعلى حركة</small><strong>'+pct(r.top_move_pct)+'</strong></div>'+
  '<div><small>أعلى حجم</small><strong>'+(r.top_volume?money(r.top_volume):'—')+'</strong></div>'+
  '<div><small>آخر إشارة</small><strong>'+formatTime(r.last_signal_at)+'</strong></div>';
 const el=document.getElementById('dashboardRadar');
 if(terminalState.radar.length) renderCards(el,terminalState.radar.slice(0,6));
 else el.innerHTML='<div class="empty-state">لا توجد إشارات محفوظة من آخر جلسة.</div>';
}
function renderMacro(){
 const wanted=['S&P 500','NASDAQ','DOW JONES','VIX','BTC','GOLD','OIL'];
 const rows=wanted.map(label=>terminalState.ticker.find(x=>String(x.label).toUpperCase()===label.toUpperCase())).filter(Boolean);
 document.getElementById('macroGrid').innerHTML=rows.map(x=>'<div class="macro-card"><span>'+escHtml(x.label)+'</span><b>'+money(x.price)+'</b><em class="'+(Number(x.change_pct)>=0?'up':'down')+'">'+pct(x.change_pct)+'</em><small class="macro-source">'+escHtml(x.source||'')+'</small></div>').join('');
}
async function runRadar(show=true){
 try{
  if(show){document.getElementById('radarGrid').innerHTML='<div class="loading">🔎 يجري فحص الرادار...</div>';switchTerminalTab('radar');}
  const d=await api('/api/radar/scan');
  terminalState.radar=d.stocks||[];
  const diag=d.diagnostics||{};
  const mode=d.historical?'🗂️ آخر رصد محفوظ — السوق مغلق':'🔴 فحص حي';
  document.getElementById('radarDiagnostics').innerHTML='<b class="radar-mode">'+mode+'</b><span>مرشحون '+Number(diag.candidates||0)+'</span><span>اجتازوا '+Number(diag.passed||0)+'</span><span>مستبعدون '+Number(diag.filtered||0)+'</span><span>أخطاء '+Number(diag.errors||0)+'</span>';
  renderRadar();
  renderDashboard({radar:{enabled:d.enabled,opportunities:terminalState.radar.length,top_move_pct:Math.max(...terminalState.radar.map(x=>Number(x.change_pct)||-Infinity)),top_volume:Math.max(...terminalState.radar.map(x=>Number(x.volume)||-Infinity))}});
 }catch(e){document.getElementById('radarGrid').innerHTML='<div class="fatal">'+escHtml(e.message)+'</div>';}
}
function renderRadar(){renderCards(document.getElementById('radarGrid'),terminalState.radar);}
function renderCards(el,rows){
 if(!rows.length){el.innerHTML='<div class="empty-state">لا توجد فرص مكتملة حاليًا.</div>';return;}
 el.innerHTML=rows.map(stockCard).join('');
}
function stockCard(x){
 const s=escHtml(x.symbol||'—'), price=x.price??x.entry_price, change=x.change_pct, rr=x.risk_reward;
 const warning=Boolean(x.risk_reward_warning);
 return '<article class="stock-card" onclick="openSymbol(\''+s+'\')"><div class="stock-head"><div><b>'+s+'</b><small>'+(x.section==='large'?'سهم كبير':'سهم صغير')+'</small></div><span class="'+(Number(change)>=0?'up':'down')+'">'+pct(change)+'</span></div><strong>$'+money(price)+'</strong><div class="stock-meta"><span>RVOL '+money(x.rvol)+'×</span><span>R:R '+(rr!=null?Number(rr).toFixed(2):'—')+(warning?' ⚠️':'')+'</span></div><div class="stock-gates"><i>✓ SAS Core</i><i>✓ السيولة</i><i>✓ الهدف</i></div><button onclick="event.stopPropagation();toggleWatch(\''+s+'\')">'+(terminalState.watch.includes(s)?'★ محفوظ':'☆ حفظ')+'</button></article>';
}
function toggleWatch(symbol){symbol=symbol.toUpperCase();terminalState.watch=terminalState.watch.includes(symbol)?terminalState.watch.filter(x=>x!==symbol):[...terminalState.watch,symbol];localStorage.setItem('saspro_watchlist',JSON.stringify(terminalState.watch));renderWatchlist();renderRadar();}
async function renderWatchlist(){
 const el=document.getElementById('watchGrid'); if(!terminalState.watch.length){el.innerHTML='<div class="empty-state">أضف الأسهم من الرادار أو صفحة التحليل.</div>';return;}
 el.innerHTML='<div class="loading">جاري تحديث المحفوظة...</div>';
 const rows=await Promise.all(terminalState.watch.slice(0,20).map(async s=>{try{return await api('/api/stocks/'+encodeURIComponent(s)+'/quote');}catch(e){return {symbol:s};}}));
 el.innerHTML=rows.map(x=>stockCard(x)).join('');
}
function openSymbol(symbol){document.getElementById('symbolSearch').value=symbol;switchTerminalTab('search');analyzeSymbol();}
async function analyzeSymbol(){
 const input=document.getElementById('symbolSearch'); const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,''); if(!symbol)return;
 const el=document.getElementById('symbolResult'); el.innerHTML='<div class="loading">🧠 يجري تحليل '+escHtml(symbol)+'...</div>';
 try{
  const [q,chart,news,analysis]=await Promise.all([api('/api/stocks/'+encodeURIComponent(symbol)+'/quote'),api('/api/stocks/'+encodeURIComponent(symbol)+'/chart'),api('/api/stocks/'+encodeURIComponent(symbol)+'/news'),api('/api/stocks/'+encodeURIComponent(symbol)+'/analyze',{method:'POST'})]);
  const tech=analysis.sas_pro?.targets||{}; const ai=analysis.analysis||{};
  el.innerHTML='<div class="detail-head"><div><span class="eyebrow">SAS PRO STOCK</span><h2>'+escHtml(symbol)+'</h2></div><button onclick="toggleWatch(\''+escHtml(symbol)+'\')">'+(terminalState.watch.includes(symbol)?'★ محفوظ':'☆ حفظ')+'</button></div><div class="quote-line"><strong>$'+money(q.price)+'</strong><span class="'+(Number(q.change_pct)>=0?'up':'down')+'">'+pct(q.change_pct)+'</span><span>'+escHtml(q.source||'')+'</span></div><div class="chart-box"><canvas id="stockCanvas" height="230"></canvas></div><div class="level-grid"><div><small>الدخول</small><b>$'+money(tech.price||q.price)+'</b></div><div><small>الوقف</small><b>$'+money(tech.exit)+'</b></div><div><small>الهدف 1</small><b>$'+money((tech.targets||[])[0])+'</b></div><div><small>R:R</small><b>'+(tech.risk_reward!=null?Number(tech.risk_reward).toFixed(2):'—')+'</b></div></div><div class="ai-box"><b>🧠 زبدة التحليل</b><p>'+escHtml(ai.key_takeaway||ai.headline_summary||'لا يوجد تحليل مختصر موثق.')+'</p></div><div class="news-list">'+(news||[]).slice(0,5).map(n=>'<a href="'+escHtml(n.url||'#')+'" target="_blank"><b>'+escHtml(n.headline||n.title||'خبر')+'</b><small>'+escHtml(n.source||'مصدر')+'</small></a>').join('')+'</div><div class="terminal-disclaimer">🛡️ AI يفسّر الأدلة فقط ولا يغيّر قرار الرادار أو المستويات.</div>';
  drawChart(chart.candles||[]);
 }catch(e){el.innerHTML='<div class="fatal">'+escHtml(e.message)+'</div>';}
}
function drawChart(candles){
 const canvas=document.getElementById('stockCanvas'); if(!canvas)return; const dpr=window.devicePixelRatio||1,w=canvas.clientWidth||600,h=230; canvas.width=w*dpr;canvas.height=h*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
 if(!candles.length){ctx.font='14px sans-serif';ctx.fillText('لا توجد بيانات شموع متاحة',20,40);return;}
 const vals=candles.map(x=>Number(x.close)).filter(Number.isFinite),min=Math.min(...vals),max=Math.max(...vals),pad=(max-min||1)*.08;
 ctx.lineWidth=2;ctx.beginPath();candles.forEach((x,i)=>{const v=Number(x.close),px=i*(w-20)/(candles.length-1)+10,py=h-20-((v-(min-pad))/(max-min+2*pad))*(h-35);i?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.stroke();
 ctx.font='11px sans-serif';ctx.fillText('$'+money(max),10,14);ctx.fillText('$'+money(min),10,h-4);
}
function formatTime(v){return v?new Date(v).toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'}):'—';}
function renderAccount(){
 const exp=me.expires_at||me.trial_expires; document.getElementById('accountCards').innerHTML='<div><small>الحالة</small><b>🟢 فعال</b></div><div><small>الباقة</small><b>'+escHtml(me.user?.plan||'SAS PRO')+'</b></div><div><small>الانتهاء</small><b>'+escHtml(exp?fmtDate(exp):'—')+'</b></div>';
}

function renderTrialCard(days){
 const d=Number(days||30);
 document.querySelector("#trialCard p").textContent=d+" يومًا للمستخدم الجديد • تبدأ تلقائيًا بعد الموافقة على الشروط، ثم يفتح رابط القناة مباشرة.";
 document.getElementById("trialBtn").textContent=d===30?"قراءة الشروط وبدء الشهر المجاني":"قراءة الشروط وبدء التجربة";
}

function renderStatus(x){
 const active=!!x.pro;
 const source=x.access?.source||null;
 const expiry=x.expires_at||null;
 document.getElementById('subStatus').textContent=active?'🟢 فعال':'🔒 غير مشترك';
 document.getElementById('subStatus').className=active?'ok':'bad';
 document.getElementById('subPlan').textContent=active?(x.user?.plan||'اشتراك'):'—';
 document.getElementById('subStart').textContent='—';
 document.getElementById('subEnd').textContent=expiry?fmtDate(expiry):(source==='free'?'غير محدد':'—');
 document.getElementById('subDays').textContent=expiry?fmtDays(new Date(),expiry):(source==='free'?'غير محدد':'—');
 if(source==='trial'&&expiry){
  document.getElementById('trialState').textContent='🎁 فعالة حتى '+fmtDate(expiry);
 }else if(source==='free'){
  document.getElementById('trialState').textContent='صلاحية مجانية غير محددة';
 }else{
  document.getElementById('trialState').textContent=x.trial_available?'متاحة مرة واحدة':(x.trial_expires?'منتهية/مستخدمة':'غير متاحة');
 }
 document.getElementById('channelBtn').hidden=!active;
 if(!x.trial_available)document.getElementById('trialBtn').disabled=true;
}

function renderPlans(p){
 const names={monthly:'شهري','3month':'3 أشهر','6month':'6 أشهر',yearly:'سنة'};
 const icons={monthly:'🟢','3month':'🔷','6month':'💎',yearly:'👑'};
 document.getElementById('plans').innerHTML=Object.entries(p).filter(([k,x])=>x.visible!==false).map(([k,x])=>
  '<article class="plan"><div class="plan-icon">'+(icons[k]||'💠')+'</div><b>'+names[k]+'</b><strong>'+x.sar+' ريال</strong><span>'+x.days+' يوم</span>'+
  '<em>'+(x.stars>0?x.stars+' ⭐':'سعر Stars غير مضبوط')+'</em>'+
  '<button '+(x.stars>0?'':'disabled')+' onclick="buyPlan(\''+k+'\')">الدفع عبر Stars</button></article>'
 ).join('');
}

function openTerms(action){termAction=action;document.getElementById('termsAgree').checked=false;document.getElementById('termsContinue').disabled=action==='view';document.getElementById('termsText').textContent='جاري تحميل الشروط...';document.getElementById('termsModal').hidden=false;api('/api/subscription/plans').then(d=>{document.getElementById('termsText').textContent=d.terms_text||'';}).catch(e=>{document.getElementById('termsText').textContent=e.message;});}
function toggleTermsButton(){if(termAction!=='view')document.getElementById('termsContinue').disabled=!document.getElementById('termsAgree').checked;}
function closeTerms(){document.getElementById('termsModal').hidden=true;termAction=null;}
async function continueTerms(){
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(!agree.checked||button.disabled)return;
 const action=termAction;
 const originalText=button.textContent;
 button.disabled=true;
 button.textContent='⏳ جاري التفعيل...';
 try{
  const accepted=await api('/api/terms/accept',{method:'POST'});

  // موافقة الشروط وحدها لا تعني نجاح التجربة؛ يجب أن يعيد الخادم trial_started=true.
  if(action==='trial'){
   if(!accepted.trial_started||!accepted.trial?.channel_link){
    throw new Error('تم حفظ موافقتك على الشروط، لكن تعذر تفعيل التجربة أو إنشاء رابط القناة. حاول مرة أخرى.');
   }
   closeTerms();
   if(tg?.openTelegramLink)tg.openTelegramLink(accepted.trial.channel_link);
   else if(tg?.openLink)tg.openLink(accepted.trial.channel_link);
   else window.open(accepted.trial.channel_link,'_blank');
   setTimeout(()=>{loadStarted=false;load();},900);
   return;
  }

  if(action?.startsWith('buy:')){
   const plan=action.slice(4);
   const d=await api('/api/subscription/invoice/'+encodeURIComponent(plan),{method:'POST'});
   closeTerms();
   if(tg?.openInvoice)tg.openInvoice(d.invoice_link,()=>setTimeout(()=>{loadStarted=false;load();},1200));
   else if(tg?.openLink)tg.openLink(d.invoice_link);
   else throw new Error('لا يمكن فتح نافذة الدفع داخل Telegram.');
   return;
  }

  closeTerms();
 }catch(e){
  button.disabled=false;
  button.textContent=originalText;
  alert(e.message);
 }
}
async function openChannel(){
 try{
  const d=await api('/api/channel/access');
  if(tg?.openTelegramLink)tg.openTelegramLink(d.url);
  else if(tg?.openLink)tg.openLink(d.url);
  else window.open(d.url,'_blank');
 }catch(e){alert(e.message);}
}
async function buyPlan(k){
 if(!me.terms_accepted){openTerms('buy:'+k);return;}
 try{
  const d=await api('/api/subscription/invoice/'+encodeURIComponent(k),{method:'POST'});
  if(tg?.openInvoice)tg.openInvoice(d.invoice_link,()=>setTimeout(load,1200));
  else if(tg?.openLink)tg.openLink(d.invoice_link);
 }catch(e){alert(e.message);}
}

async function adminRefresh(){
 const p=me?.admin_permissions||[];
 const tasks=[];
 if(p.includes('users')){tasks.push(loadAdminStats(),adminSearch(),loadAdminMonthlyReport());}
 if(p.includes('settings')){document.getElementById('planEditor').closest('.admin-panel').hidden=false;document.getElementById('plansEditorPanel').hidden=false;tasks.push(loadAdminPlans(),loadSubscriptionConfig());}
 else {document.getElementById('planEditor').closest('.admin-panel').hidden=true;document.getElementById('plansEditorPanel').hidden=true;document.getElementById('planEditor').closest('.admin-panel').previousElementSibling.hidden=true;}
 if(p.includes('payments')){document.getElementById('starsPanel').hidden=false;tasks.push(loadStarsWallet());}else{document.getElementById('starsPanel').hidden=true;}
 await Promise.all(tasks);
}
async function loadStaff(){
 try{
  const d=await api('/api/admin/staff');
  const mePerms=me?.admin_permissions||[];
  const catalog=d.find(x=>x.role==='owner')?.permissions||[];
  const labels={users:'إدارة المشتركين',subscriptions:'إدارة الاشتراكات',channel:'إدارة القناة',radar:'إدارة الرصد',payments:'إدارة المدفوعات',admins:'إدارة المشرفين',settings:'إعدادات النظام',audit:'سجل العمليات'};
  document.getElementById('staffPermissions').innerHTML=Object.entries(labels).map(([k,v])=>
   '<label><input type="checkbox" class="perm" value="'+k+'"> '+v+'</label>'
  ).join('');
  document.getElementById('staffList').innerHTML=d.map(x=>{
   const canEdit=x.role!=='owner';
   return '<div class="user-row"><div><b>'+esc(x.role)+' — '+esc(x.telegram_id)+'</b><br><small>'+esc((x.permissions||[]).join('، '))+' — '+(x.enabled?'🟢 فعال':'🔴 معطل')+'</small></div>'+
    (canEdit?'<div class="user-actions">'+(x.enabled?'<button class="danger" onclick="staffToggle('+x.telegram_id+',false)">تعطيل</button>':'<button onclick="staffToggle('+x.telegram_id+',true)">تفعيل</button>')+'<button class="danger" onclick="staffDelete('+x.telegram_id+')">حذف</button></div>':'')+'</div>';
  }).join('');
 }catch(e){document.getElementById('staffList').textContent=e.message;}
}
async function addStaff(){
 const id=Number(document.getElementById('staffId').value); const role=document.getElementById('staffRole').value;
 const permissions=[...document.querySelectorAll('.perm:checked')].map(x=>x.value);
 if(!id){alert('أدخل Telegram ID');return;}
 try{await api('/api/admin/staff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({telegram_id:id,role,permissions})});await loadStaff();alert('تم حفظ صلاحيات المشرف');}catch(e){alert(e.message);}
}
async function staffToggle(id,enabled){try{await api('/api/admin/staff/'+id+'/'+(enabled?'enable':'disable'),{method:'POST'});await loadStaff();}catch(e){alert(e.message);}}
async function staffDelete(id){if(!confirm('حذف هذا المشرف؟'))return;try{await api('/api/admin/staff/'+id,{method:'DELETE'});await loadStaff();}catch(e){alert(e.message);}}
async function loadStarsWallet(){
 try{
  const [b,t]=await Promise.all([api('/api/admin/stars/balance'),api('/api/admin/stars/transactions')]);
  document.getElementById('starsBalance').textContent=(Number(b.balance||0).toLocaleString('en-US'))+' ⭐';
  document.getElementById('starsUpdated').textContent=new Date().toLocaleTimeString('ar-SA');
  const rows=t.transactions||[];
  document.getElementById('starsTransactions').innerHTML=rows.length?rows.map(x=>{
   const amount=Number(x.amount||0);
   const incoming=amount>=0;
   const date=x.date?new Date(Number(x.date)*1000).toLocaleString('ar-SA'):'—';
   const id=esc(x.id||'');
   const partner=incoming?(x.source?.type||'دفع/إيراد'):(x.receiver?.type||'سحب/مصروف');
   return '<div class="stars-row"><div><b>'+(incoming?'➕':'➖')+' '+Math.abs(amount).toLocaleString('en-US')+' ⭐</b><small>'+esc(partner)+' — '+date+'</small></div><code>'+id+'</code></div>';
  }).join(''):'<div class="loading">لا توجد عمليات Stars ظاهرة حاليًا.</div>';
 }catch(e){
  document.getElementById('starsBalance').textContent='غير متاح';
  document.getElementById('starsTransactions').innerHTML='<div class="fatal">تعذر قراءة بيانات Stars: '+esc(e.message)+'</div>';
 }
}
async function openStarsWithdrawal(){
 try{
  const d=await api('/api/admin/stars/withdrawal');
  if(tg?.openLink)tg.openLink(d.fragment_url);else window.open(d.fragment_url,'_blank');
 }catch(e){alert(e.message);}
}

async function loadAdminMonthlyReport(){
 try{
  const input=document.getElementById('reportMonth');
  if(!input)return;
  if(!input.value){
   const d=new Date();
   input.value=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');
  }
  const d=await api('/api/admin/monthly-report?month='+encodeURIComponent(input.value));
  renderAdminMonthlyReport(d);
 }catch(e){
  const el=document.getElementById('monthlyReport');
  if(el)el.innerHTML='<div class="fatal">تعذر تحميل التقرير الشهري: '+esc(e.message)+'</div>';
 }
}
function renderAdminMonthlyReport(d){
 const r=d.radar||{},s=d.subscribers||{},v=d.revenue||{},c=d.comparison||{};
 const n=x=>x===null||x===undefined?'—':Number(x).toLocaleString('en-US');
 const pct=x=>x===null||x===undefined?'—':Number(x).toFixed(2)+'%';
 const ret=x=>x===null||x===undefined?'—':(Number(x)>0?'+':'')+Number(x).toFixed(2)+'%';
 const top=(d.top_symbols||[]).map(x=>'<span class="report-chip">'+esc(x.symbol)+' <b>'+n(x.count)+'</b></span>').join('')||'<span class="muted">لا توجد نتائج</span>';
 document.getElementById('monthlyReport').innerHTML=
  '<div class="report-toolbar"><div><b>📅 التقرير الشهري</b><small>بيانات فعلية من قاعدة SAS PRO</small></div>'+
  '<div class="report-month-actions"><input id="reportMonth" type="month" value="'+esc(d.month)+'"><button onclick="loadAdminMonthlyReport()">عرض</button></div></div>'+
  '<div class="report-grid">'+
   '<div><small>إشارات الرصد</small><strong>'+n(r.signals)+'</strong><em>مرسلة: '+n(r.sent)+'</em></div>'+
   '<div><small>الأهداف المحققة</small><strong>'+n(r.target_hits)+'</strong><em>نسبة: '+pct(r.target_hit_rate)+'</em></div>'+
   '<div><small>متوسط العائد المسجل</small><strong>'+ret(r.avg_return_pct)+'</strong><em>نتائج قابلة للحساب: '+n(r.tracked_returns)+'</em></div>'+
   '<div><small>أفضل نتيجة مسجلة</small><strong>'+ret(r.best_return_pct)+'</strong><em>أسوأ: '+ret(r.worst_return_pct)+'</em></div>'+
   '<div><small>مكتملة / موقوفة</small><strong>'+n(r.completed)+' / '+n(r.failed)+'</strong><em>نشطة: '+n(r.active)+'</em></div>'+
   '<div><small>الإيراد</small><strong>'+n(v.sar)+' ريال</strong><em>'+n(v.stars)+' ⭐ — '+n(v.payments)+' عملية</em></div>'+
   '<div><small>مستخدمون جدد</small><strong>'+n(s.new_users)+'</strong><em>تجارب: '+n(s.trial_users)+' — مدفوعة: '+n(s.paid_users)+'</em></div>'+
  '</div>'+
  '<div class="report-compare"><b>↔️ مقارنة بالشهر السابق '+esc(c.previous_month||'—')+'</b><span>الإشارات: '+n(c.signals)+' | النتائج: '+n(c.outcomes)+' | نسبة الأهداف: '+pct(c.target_hit_rate)+' | متوسط العائد: '+ret(c.avg_return_pct)+' | Stars: '+n(c.stars)+'</span></div>'+
  '<div class="report-top"><b>🔥 أكثر الأسهم ظهورًا في نتائج الرصد</b><div>'+top+'</div></div>'+
  '<div class="report-note">ℹ️ '+esc(d.note||'')+'</div>';
}
async function loadAdminStats(){
 const d=await api('/api/admin/overview');
 document.getElementById('adminStats').innerHTML=[['active','🟢 النشطون'],['expired','🔴 المنتهية'],['trial_users','🎁 التجارب'],['new_users','👥 الجدد'],['payments','💳 المدفوعات'],['stars','⭐ Stars']].map(x=>'<div><small>'+x[1]+'</small><strong>'+d[x[0]]+'</strong></div>').join('');
 const o=d.owner||{};
 const full=[o.first_name,o.last_name].filter(Boolean).join(' ')||'مالك SAS PRO';
 document.getElementById('ownerName').textContent=full;
 document.getElementById('ownerUsername').textContent=o.username?'@'+o.username:'بدون Username';
 document.getElementById('ownerId').textContent='Telegram ID: '+(o.telegram_id||'—');
}
async function adminSearch(){try{const q=document.getElementById('adminSearch').value.trim();const rows=await api('/api/admin/users'+(q?'?q='+encodeURIComponent(q):''));document.getElementById('adminUsers').innerHTML=rows.map(u=>{
 const status=u.free_access?'♾️ دائم':(u.subscription_expires&&new Date(u.subscription_expires)>new Date()?'🟢 فعال':'🔴 منتهي');
 return '<div class="user-row"><div><b>'+esc(u.first_name||u.username||'بدون اسم')+'</b><br><small>ID: '+esc(u.telegram_id)+' '+(u.username?'@'+esc(u.username):'')+'</small><br><small>'+status+' — '+fmtDate(u.subscription_expires)+'</small></div>'+
 '<div class="user-actions"><button onclick="adminGrant('+u.telegram_id+',30)">30 يوم</button><button onclick="adminGrant('+u.telegram_id+',90)">3 أشهر</button><button onclick="adminGrant('+u.telegram_id+',365)">سنة</button><button onclick="adminFreeExtend('+u.telegram_id+',3)">مجاني +3</button><button onclick="adminFreeExtend('+u.telegram_id+',7)">مجاني +7</button><button onclick="adminFreeExtend('+u.telegram_id+',30)">مجاني +30</button><button onclick="adminGrant('+u.telegram_id+',\'forever\')">دائم</button><button class="danger" onclick="adminRevoke('+u.telegram_id+')">إلغاء</button></div></div>';
 }).join('')||'<p>لا توجد نتائج.</p>';}catch(e){document.getElementById('adminUsers').textContent=e.message;}}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
async function adminFreeExtend(id,d){try{await api('/api/admin/free-extend/'+id+'?days='+d,{method:'POST'});await adminRefresh();alert('تم تمديد المجاني للمستخدم.');}catch(e){alert(e.message);}}
async function adminGrant(id,d){try{await api('/api/admin/grant/'+id+'?days='+encodeURIComponent(d),{method:'POST'});await adminRefresh();}catch(e){alert(e.message);}}
async function adminRevoke(id){if(!confirm('إلغاء وصول هذا المستخدم؟'))return;try{await api('/api/admin/revoke/'+id,{method:'POST'});await adminRefresh();}catch(e){alert(e.message);}}
async function loadSubscriptionConfig(){
 const d=await api('/api/admin/subscription-config');
 document.getElementById('paidPlansVisible').checked=!!d.paid_plans_visible;
 document.getElementById('trialDays').value=d.trial_days||3;
 document.getElementById('plansEditorPanel').hidden=!d.paid_plans_visible;
}
async function loadAdminPlans(){
 const p=await api('/api/admin/plans');
 const names={monthly:'شهري','3month':'3 أشهر','6month':'6 أشهر',yearly:'سنة'};
 document.getElementById('planVisibility').innerHTML=Object.entries(p).map(([k,x])=>'<label><input class="plan-vis" data-plan="'+k+'" type="checkbox" '+(x.visible?'checked':'')+'> '+names[k]+'</label>').join('');
 document.getElementById('planEditor').innerHTML=Object.entries(p).map(([k,x])=>
  '<div class="plan-edit"><b>'+names[k]+'</b><label>ريال<input id="sar_'+k+'" type="number" value="'+x.sar+'"></label><label>أيام<input id="days_'+k+'" type="number" value="'+x.days+'"></label><label>Stars<input id="stars_'+k+'" type="number" value="'+x.stars+'"></label></div>'
 ).join('');
}
async function savePlans(){
 const p={};
 for(const k of ['monthly','3month','6month','yearly']){
  p[k]={sar:Number(document.getElementById('sar_'+k).value),days:Number(document.getElementById('days_'+k).value),stars:Number(document.getElementById('stars_'+k).value)};
 }
 try{
  await api('/api/admin/plans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  alert('تم حفظ الباقات');
  await loadAdminPlans();
 }catch(e){alert(e.message);}
}

async function saveSubscriptionConfig(){
 const paid=!!document.getElementById('paidPlansVisible').checked;
 const days=Number(document.getElementById('trialDays').value||30);
 try{
  const d=await api('/api/admin/subscription-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paid_plans_visible:paid,trial_days:days})});
  document.getElementById('plansEditorPanel').hidden=!d.paid_plans_visible;
  alert(d.paid_plans_visible?'تم إظهار الاشتراكات المدفوعة.':'تم إخفاء الاشتراكات المدفوعة وإبقاء المجاني فقط.');
 for(const el of document.querySelectorAll('.plan-vis')){await api('/api/admin/plan-visibility/'+el.dataset.plan,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({visible:el.checked})});}
 }catch(e){alert(e.message);}
}
document.addEventListener('change',e=>{if(e.target?.id==='paidPlansVisible')document.getElementById('plansEditorPanel').hidden=!e.target.checked;});
load();