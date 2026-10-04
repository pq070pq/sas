const tg=window.Telegram&&window.Telegram.WebApp;
if(tg){try{tg.ready();tg.expand();tg.setHeaderColor('#020912');tg.setBackgroundColor('#020912');}catch(e){}}
const getInitData=()=>tg?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')||new URLSearchParams(location.search).get('tgWebAppData')||'';
const headers=()=>({'X-Telegram-Init-Data':getInitData()});
async function api(path,opt={}){
 const controller=new AbortController();
 const timeout=setTimeout(()=>controller.abort(),8000);
 try{
  opt.headers=Object.assign(headers(),opt.headers||{});
  opt.signal=controller.signal;
  const r=await fetch(path,opt);
  if(!r.ok){
   let msg='تعذر تنفيذ العملية';
   try{const d=await r.json();msg=d.detail||d.message||msg;}
   catch(e){try{const t=(await r.text()).trim();if(t)msg=t;}catch(_){}}
   throw new Error(msg);
  }
  return r.json();
 }catch(e){
  if(e?.name==='AbortError')throw new Error('انتهت مهلة الاتصال بالخادم. تحقق من اتصال Telegram وحاول مرة أخرى.');
  throw e;
 }finally{clearTimeout(timeout);}
}
const fmtDate=v=>v?new Date(v).toLocaleDateString('ar-SA'):'—';
const fmtDays=(a,b)=>{if(!a||!b)return'—';const n=Math.ceil((new Date(b)-new Date(a))/86400000);return n>0?n+' يوم':'منتهي';};
let me=null,plans=null,termAction=null;
let loadStarted=false;

async function load(){
 if(loadStarted)return;
 loadStarted=true;
 const watchdog=setTimeout(()=>{
  if(!loadStarted)return;
  loadStarted=false;
  document.body.innerHTML='<div class="fatal"><b>⚠️ تعذر فتح SAS PRO</b><br><small>الخادم لم يُرجع نتيجة التحقق خلال 8 ثوانٍ. المشكلة في اتصال Mini App بالخادم وليست في الاشتراك.</small><br><button onclick="location.reload()">إعادة المحاولة</button></div>';
 },8500);
 try{
  if(!tg)throw new Error('تعذر الوصول إلى Telegram WebApp. افتح SAS PRO من داخل Telegram.');
  const initData=getInitData();
  if(!initData)throw new Error('لم تصل بيانات Telegram إلى التطبيق. أغلق Mini App وافتحه من زر SAS PRO داخل Telegram.');
  me=await api('/api/me');
  // Verification succeeded; stop the startup watchdog before loading the dashboard.
  clearTimeout(watchdog);
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
  clearTimeout(watchdog);
  loadStarted=false;
  const msg=e?.message||'تعذر التحقق من Telegram.';
  document.body.innerHTML='<div class="fatal"><b>⚠️ تعذر فتح SAS PRO</b><br><small>'+escHtml(msg)+'</small><br><button onclick="location.reload()">إعادة المحاولة</button></div>';
 }
}

const terminalState={ticker:[],radar:[],watch:JSON.parse(localStorage.getItem('saspro_watchlist')||'[]'),timer:null,tab:'dashboard'};
function escHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}
function money(v){const n=Number(v);return Number.isFinite(n)&&n>0?n.toLocaleString('en-US',{minimumFractionDigits:n<10?2:0,maximumFractionDigits:4}):'—';}
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
async function fetchPublicGoldFallback(){
 try{
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),6000);
  const r=await fetch('https://xaus.com/api/v1/spot?currency=USD&fresh='+Date.now(),{headers:{'Accept':'application/json'},signal:controller.signal,cache:'no-store'});
  clearTimeout(timer);
  if(!r.ok)throw new Error('XAUS HTTP '+r.status);
  const d=await r.json();
  const price=Number(d?.spot_usd_oz||d?.xau?.price);
  if(!Number.isFinite(price)||price<=0)return null;
  const state=d?.data_state||{};
  return {symbol:'XAU/USD',label:'GOLD',price,change_pct:null,source:state.status==='stale'?'XAUS Public — آخر سعر حقيقي محفوظ':'XAUS Public',is_extended_hours:false,datetime:d?.price_as_of||d?.updated_at,stale:Boolean(d?.stale||state.status==='stale')};
 }catch(e){
  console.warn('SAS PRO direct gold fallback failed:',e?.message||e);
  return null;
 }
}

async function refreshTerminal(){
 document.getElementById('terminalClock').textContent=new Date().toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'});
 const results=await Promise.allSettled([
  api('/api/market/radar-status'),
  api('/api/dashboard/home'),
  api('/api/market/ticker')
 ]);
 const status=results[0]?.status==='fulfilled'?results[0].value:null;
 const home=results[1]?.status==='fulfilled'?results[1].value:null;
 const ticker=results[2]?.status==='fulfilled'?results[2].value:null;
 if(Array.isArray(ticker)){
  const gold=ticker.find(x=>String(x?.symbol||'').toUpperCase()==='XAU/USD' || String(x?.label||'').toUpperCase()==='GOLD');
  if(!gold || !(Number(gold.price)>0)){
   const directGold=await fetchPublicGoldFallback();
   if(directGold){
    const idx=ticker.findIndex(x=>String(x?.symbol||'').toUpperCase()==='XAU/USD' || String(x?.label||'').toUpperCase()==='GOLD');
    if(idx>=0)ticker[idx]=directGold;else ticker.push(directGold);
   }
  }
 }

 if(status) renderMarketStrip(status);
 if(home) renderDashboard(home);
 if(Array.isArray(ticker)) terminalState.ticker=ticker;
 renderMacro();

 const errors=results.filter(x=>x.status==='rejected').map(x=>x.reason?.message||'خطأ غير معروف');
 if(errors.length && !ticker){
  console.warn('SAS PRO market ticker refresh failed:',errors);
 }
 if(terminalState.tab==='radar' && !errors.length) await runRadar(false);
 if(terminalState.tab==='watch') renderWatchlist();
}
function renderMarketStrip(s){
 const label=s.open?'🟢 السوق مفتوح':'🔴 السوق مغلق';
 document.getElementById('marketStrip').innerHTML='<div class="market-state '+(s.open?'open':'closed')+'"><b>'+label+'</b><span>'+escHtml(s.label_ar||'')+'</span></div><div class="market-state"><b>📡 الرادار</b><span>'+(s.stock_radar_enabled?'يعمل':'متوقف')+'</span></div><div class="market-state"><b>🕒 الجلسة</b><span>'+escHtml(s.session||'—')+'</span></div>';
}
function renderDashboard(d){
 const r=d.radar||{};
 if(Array.isArray(r.stocks)) terminalState.radar=r.stocks;
 const historical=Boolean(r.historical);
 const enabled=Boolean(r.enabled);
 document.getElementById('radarStatusText').textContent=enabled
  ? '🟢 الرصد الآلي يعمل — يبحث عن الأسهم التي تستوفي بوابة SAS PRO.'
  : (historical ? '🟡 السوق مغلق — هذه آخر بيانات رصد محفوظة من الجلسة السابقة، ويعود الفحص الحي مع الافتتاح.' : '🔴 الرصد متوقف حاليًا خارج جلسة الأسهم الأمريكية.');
 document.getElementById('dashboardMetrics').innerHTML=
  '<div><small>'+(historical?'آخر جلسة':'فرص اليوم')+'</small><strong>'+Number(r.opportunities||terminalState.radar.length||0)+'</strong></div>'+
  '<div><small>أعلى حركة</small><strong>'+pct(r.top_move_pct)+'</strong></div>'+
  '<div><small>أعلى حجم</small><strong>'+(r.top_volume?Number(r.top_volume).toLocaleString('en-US'):'—')+'</strong></div>'+
  '<div><small>آخر إشارة</small><strong>'+formatDateTime(r.last_signal_at)+'</strong></div>';
 const el=document.getElementById('dashboardRadar');
 if(terminalState.radar.length) renderCards(el,terminalState.radar.slice(0,6));
 else el.innerHTML='<div class="empty-state">لا توجد إشارات محفوظة من آخر جلسة.</div>';
}
function renderMacro(){
 const wanted=[
  {label:'S&P 500',symbols:['SPX','^GSPC','SPY']},
  {label:'NASDAQ',symbols:['IXIC','^IXIC','QQQ']},
  {label:'DOW JONES',symbols:['DJI','^DJI','DIA']},
  {label:'VIX',symbols:['VIX','^VIX','VIXY']},
  {label:'BTC',symbols:['BTC/USD','BTCUSD','BTCUSDT']},
  {label:'GOLD',symbols:['XAU/USD','XAUUSD','GCUSD','GLD']}
 ];
 const rows=wanted.map(item=>{
  const found=terminalState.ticker.find(x=>{
   const label=String(x?.label||'').toUpperCase();
   const symbol=String(x?.symbol||'').toUpperCase();
   return label===item.label.toUpperCase() || item.symbols.some(s=>s.toUpperCase()===symbol);
  });
  if(found)return {...found,label:item.label};
  try{
   const cached=JSON.parse(localStorage.getItem('saspro_macro_'+item.label.replace(/\\s+/g,'_'))||'null');
   if(cached && Number(cached.price)>0)return {label:item.label,price:cached.price,change_pct:cached.change_pct,source:'آخر سعر محفوظ'};
  }catch(e){}
  return null;
 }).filter(Boolean);
 const cards=rows.map(x=>{
  const valid=Number(x.price)>0;
  return '<div class="macro-card '+(valid?'':'macro-unavailable')+'"><span>'+escHtml(x.label)+'</span><b>'+ (valid?money(x.price):'غير متاح') +'</b><em class="'+(Number(x.change_pct)>=0?'up':'down')+'">'+pct(x.change_pct)+'</em><small class="macro-source">'+escHtml(x.source||'غير متوفر')+'</small>'+(x.diagnostic?'<small class="macro-diagnostic">'+escHtml(x.diagnostic)+'</small>':'')+'</div>';
 }).join('');
 document.getElementById('macroGrid').innerHTML=cards || '<div class="empty-state">لا تتوفر بيانات السوق حاليًا.</div>';
 const ticker=document.getElementById('marketTicker');
 if(ticker){
   ticker.innerHTML=rows.map(x=>{
     const valid=Number(x.price)>0;
     const cls=Number(x.change_pct)>=0?'up':'down';
     const cachedKey='saspro_macro_'+String(x.label).replace(/\s+/g,'_');
     let displayPrice=valid?money(x.price):'—';
     let displayChange=valid?pct(x.change_pct):'—';
     if(valid){
       try{localStorage.setItem(cachedKey,JSON.stringify({price:x.price,change_pct:x.change_pct,updated_at:Date.now()}));}catch(e){}
     }else{
       try{
         const cached=JSON.parse(localStorage.getItem(cachedKey)||'null');
         if(cached && Number(cached.price)>0){
           displayPrice=money(cached.price);
           displayChange=Number.isFinite(Number(cached.change_pct))?pct(cached.change_pct):'—';
         }
       }catch(e){}
     }
     return '<div class="ticker-item"><span class="ticker-label">'+escHtml(x.label)+'</span><b>'+displayPrice+'</b><em class="'+cls+'">'+displayChange+'</em></div>';
   }).join('') || '<div class="empty-state">لا تتوفر أسعار السوق حاليًا.</div>';
 }
}
async function runRadar(show=true){
 try{
  if(show){document.getElementById('radarGrid').innerHTML='<div class="loading">🔎 يجري تحميل أحدث بيانات الرادار...</div>';switchTerminalTab('radar');}
  const d=await api('/api/radar/scan');
  terminalState.radar=d.stocks||[];
  const diag=d.diagnostics||{};
  const mode=d.historical?'🗂️ آخر رصد محفوظ — السوق مغلق':'🔴 فحص حي';
  document.getElementById('radarDiagnostics').innerHTML='<b class="radar-mode">'+mode+'</b><span>مرشحون '+Number(diag.candidates||0)+'</span><span>اجتازوا '+Number(diag.passed||0)+'</span><span>مستبعدون '+Number(diag.filtered||0)+'</span><span>أخطاء '+Number(diag.errors||0)+'</span>';
  const candidateEl=document.getElementById('radarCandidates');
  const candidates=Array.isArray(diag.filtered_examples)?diag.filtered_examples:[];
  if(candidateEl && candidates.length){
    candidateEl.hidden=false;
    candidateEl.innerHTML='<div class="candidate-title"><b>🔎 تحليل المرشحين</b><small>هذه الأسهم اجتازت مرحلة البحث الأولي ولم تدخل الإشارة النهائية. سبب الاستبعاد ظاهر لكل سهم.</small></div>'+
      '<div class="candidate-grid">'+candidates.slice(0,12).map(x=>{
        const sym=String(x.symbol||'—');
        return '<article class="candidate-card"><div><b>'+escHtml(sym)+'</b><small>'+escHtml(x.data_source||'مصدر الرصد')+'</small></div><p>'+escHtml(x.reason||'تم استبعاده في مرحلة لاحقة')+'</p><button onclick="openSymbol(\''+escHtml(sym)+'\')">🧠 تحليل</button></article>';
      }).join('')+'</div>';
  }else if(candidateEl){
    candidateEl.hidden=true;
    candidateEl.innerHTML='';
  }
  renderRadar();
  renderDashboard({radar:{enabled:d.enabled,opportunities:terminalState.radar.length,top_move_pct:Math.max(...terminalState.radar.map(x=>Number(x.change_pct)||-Infinity)),top_volume:Math.max(...terminalState.radar.map(x=>Number(x.volume)||-Infinity))}});
 }catch(e){document.getElementById('radarGrid').innerHTML='<div class="fatal">'+escHtml(e.message)+'</div>';}
}
function renderRadar(){renderCards(document.getElementById('radarGrid'),terminalState.radar);}
function renderCards(el,rows){
 if(!rows.length){el.innerHTML='<div class="empty-state">لا توجد فرص مكتملة حاليًا. إذا كان السوق مغلقًا ستظهر آخر إشارات محفوظة تلقائيًا.</div>';return;}
 el.innerHTML=rows.map(stockCard).join('');
}
function stockCard(x){
 const raw=String(x.symbol||'—'), s=escHtml(raw);
 const cls=x.classification||{};
 const tgt=(x.targets && !Array.isArray(x.targets) ? x.targets : {})||{};
 const targetList=Array.isArray(x.targets)?x.targets:(Array.isArray(tgt.targets)?tgt.targets:[]);
 const price=Number.isFinite(Number(x.live_price))&&Number(x.live_price)>0?x.live_price:(x.price??x.entry_price??tgt.price);
 const change=x.live_change_pct??x.change_pct;
 const target=targetList[0]??tgt.target1??x.target1??x.target;
 const stop=x.exit??tgt.exit??x.stop_loss??x.stop??x.stop_price;
 const volume=Number(x.volume);
 const rvol=Number(x.rvol??cls.rvol??x.momentum_rvol_10d);
 const rr=x.risk_reward??tgt.risk_reward;
 const warning=Boolean(x.risk_reward_warning??tgt.risk_reward_warning);
 const section=x.momentum_section||x.section;
 const score=Number(cls.score);
 const ai=x.ai_analysis||x.ai||{};
 const gates=x.radar_checks||{};
 const gate=(ok,label)=>'<i class="'+(ok?'gate-ok':'gate-warn')+'">'+(ok?'✓ ':'⚠️ ')+label+'</i>';
 return '<article class="stock-card"><div class="stock-head"><div><b>'+s+'</b><small>'+(section==='large'?'سهم كبير / متوسط':'سهم صغير')+(x.created_at?' • '+formatTime(x.created_at):'')+'</small></div><span class="'+(Number(change)>=0?'up':'down')+'">'+pct(change)+'</span></div>'+
 '<strong>$'+money(price)+'</strong>'+
 '<div class="stock-levels"><span>دخول <b>$'+money(price)+'</b></span><span>وقف <b>$'+money(stop)+'</b></span><span>هدف 1 <b>$'+money(target)+'</b></span></div>'+
 '<div class="stock-meta"><span>RVOL '+(Number.isFinite(rvol)&&rvol>0?rvol.toFixed(2):'—')+'×</span><span>حجم '+(Number.isFinite(volume)&&volume>0?volume.toLocaleString('en-US'):'—')+'</span><span>R:R '+(rr!=null?Number(rr).toFixed(2):'—')+(warning?' ⚠️':'')+'</span></div>'+
 '<div class="stock-gates">'+gate(gates.sas_core!==false,'SAS Core')+gate(gates.liquidity!==false,'السيولة')+gate(gates.target!==false,'الهدف')+gate(gates.live_levels!==false,'المستويات')+'</div>'+
 '<div class="stock-summary">'+(Number.isFinite(score)?'<span>⭐ قوة '+score.toFixed(0)+'/100</span>':'')+(x.live_price_source?'<span>📡 '+escHtml(x.live_price_source)+'</span>':'')+'</div>'+
 '<div class="stock-ai">'+escHtml(ai.key_takeaway||ai.headline_summary||cls.reason||'تحليل AI يظهر عند فتح التحليل الكامل.')+'</div>'+
 '<div class="stock-actions"><button onclick="event.stopPropagation();openSymbol(\''+raw+'\')">🧠 تحليل كامل</button><button onclick="event.stopPropagation();toggleWatch(\''+raw+'\')">'+(terminalState.watch.includes(raw)?'★ محفوظ':'☆ حفظ')+'</button></div></article>';
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
 const input=document.getElementById('symbolSearch');
 const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,'');
 if(!symbol)return;
 const el=document.getElementById('symbolResult');
 el.innerHTML='<div class="loading">🧠 يجري تحليل '+escHtml(symbol)+'...</div>';
 const [qR,chartR,newsR,analysisR]=await Promise.allSettled([
   api('/api/stocks/'+encodeURIComponent(symbol)+'/quote'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/chart'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/news'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/analyze',{method:'POST'})
 ]);
 const q=qR.status==='fulfilled'?qR.value:{symbol,price:null,change_pct:null,source:'غير متاح'};
 const chart=chartR.status==='fulfilled'?chartR.value:{candles:[]};
 const news=newsR.status==='fulfilled'&&Array.isArray(newsR.value)?newsR.value:[];
 const analysis=analysisR.status==='fulfilled'?analysisR.value:null;
 if(!analysis){
   const errors=[qR,chartR,newsR,analysisR].filter(x=>x.status==='rejected').map(x=>x.reason?.message).filter(Boolean);
   el.innerHTML='<div class="fatal">⚠️ تعذر إكمال التحليل الكامل<br><small>لكن تم إبقاء البيانات التي نجح تحميلها.</small>'+(errors.length?'<br><small>'+escHtml(errors[0])+'</small>':'')+'</div>';
   if(Number(q.price)>0||news.length||chart.candles?.length) renderPartialAnalysis(el,symbol,q,chart,news);
   return;
 }
 const tech=analysis.sas_pro?.targets||{};
 const ai=analysis.analysis||{};
 const fcc=analysis.sas_pro?.targets?.fcc_review||ai.fcc_review||{};
 const fccHtml=fcc.available ? '<div class="ai-box"><b>🧠 مراجعة الذكاء الاصطناعي للسهم</b><p><b>التقييم:</b> '+escHtml(fcc.review_level||'محايد')+'</p>'+((fcc.strengths||[]).length?'<p><b>💪 نقاط القوة:</b><br>'+fcc.strengths.slice(0,4).map(x=>'• '+escHtml(x)).join('<br>')+'</p>':'')+((fcc.contradictions||[]).length?'<p><b>⚠️ نقاط تحتاج انتباه:</b><br>'+fcc.contradictions.slice(0,4).map(x=>'• '+escHtml(x)).join('<br>')+'</p>':'')+(fcc.note?'<p><b>📌 الخلاصة:</b> '+escHtml(fcc.note)+'</p>':'')+'<small>مراجعة مساعدة لفهم البيانات فقط، ولا تغيّر مستويات SAS PRO.</small></div>' : '';
 const targets=Array.isArray(tech.targets)?tech.targets:[];
 const newsFromAnalysis=Array.isArray(analysis.news)?analysis.news:news;
 const partial=Boolean(analysis.partial);
 const aiAvailable=Boolean(ai.ai_available||ai.enabled);
 const entry=Number(tech.price||q.price);
 const stop=Number(tech.exit||tech.stop);
 const target1=Number(targets[0]||tech.target1);
 const computedRR=Number.isFinite(entry)&&Number.isFinite(stop)&&Number.isFinite(target1)&&entry>stop&&target1>entry?((target1-entry)/(entry-stop)):null;
 const notice=partial
   ? '<div class="partial-note">🟡 بيانات السعر أو المستويات غير مكتملة من المصدر. لم يتم تخمين أي قيمة.</div>'
   : (!aiAvailable ? '<div class="partial-note">ℹ️ تحليل AI غير متاح حاليًا؛ تم عرض الخلاصة الفنية من بيانات السهم.</div>' : '');
 const rr=computedRR!=null?computedRR.toFixed(2):(tech.risk_reward!=null?Number(tech.risk_reward).toFixed(2):'—');
 const summary=ai.key_takeaway||ai.headline_summary||'لا توجد خلاصة موثقة متاحة حاليًا.';
 el.innerHTML=
   '<div class="detail-head"><div><span class="eyebrow">SAS PRO STOCK</span><h2>'+escHtml(symbol)+'</h2></div><button onclick="toggleWatch(\''+escHtml(symbol)+'\')">'+(terminalState.watch.includes(symbol)?'★ محفوظ':'☆ حفظ')+'</button></div>'+
   '<div class="quote-line"><strong>$'+money(q.price)+'</strong><span class="'+(Number(q.change_pct)>=0?'up':'down')+'">'+pct(q.change_pct)+'</span><span>'+escHtml(q.source||'')+'</span></div>'+
   notice+
   '<div class="chart-box"><canvas id="stockCanvas" height="230"></canvas></div>'+
   '<div class="level-grid"><div><small>🟦 الدخول</small><b>$'+money(entry)+'</b></div><div><small>🛑 الوقف</small><b>$'+money(stop)+'</b></div><div><small>🎯 الهدف 1</small><b>$'+money(target1)+'</b></div><div><small>⚖️ R:R</small><b>'+rr+'</b></div></div>'+
   '<div class="ai-box"><b>'+(aiAvailable?'🧠 زبدة تحليل AI':'📐 الخلاصة الفنية')+'</b><p>'+escHtml(summary)+'</p>'+(aiAvailable&&ai.provider?'<small>المزود: '+escHtml(ai.provider)+'</small>':'')+'</div>'+fccHtml+
   '<div class="news-list">'+(newsFromAnalysis.length?newsFromAnalysis.slice(0,5).map(n=>'<a href="'+escHtml(n.url||'#')+'" target="_blank"><b>'+escHtml(n.headline||n.title||'خبر')+'</b><small>'+escHtml(n.source||'مصدر')+'</small></a>').join(''):'<div class="empty-state">📰 لا توجد أخبار موثقة متاحة حاليًا.</div>')+'</div>'+
   '<div class="terminal-disclaimer">🛡️ AI يفسّر الأدلة فقط ولا يغيّر قرار الرادار أو المستويات.</div>';
 drawChart(chart.candles||[]);
}
function renderPartialAnalysis(el,symbol,q,chart,news){
 el.innerHTML='<div class="detail-head"><div><span class="eyebrow">SAS PRO STOCK</span><h2>'+escHtml(symbol)+'</h2></div></div>'+
 '<div class="quote-line"><strong>$'+money(q.price)+'</strong><span class="'+(Number(q.change_pct)>=0?'up':'down')+'">'+pct(q.change_pct)+'</span><span>'+escHtml(q.source||'')+'</span></div>'+
 '<div class="partial-note">🟡 تم تحميل البيانات المتاحة فقط. التحليل الفني الكامل سيظهر عند توفر مصدر التحليل.</div>'+
 '<div class="chart-box"><canvas id="stockCanvas" height="230"></canvas></div>'+
 '<div class="news-list">'+(news.length?news.slice(0,5).map(n=>'<a href="'+escHtml(n.url||'#')+'" target="_blank"><b>'+escHtml(n.headline||n.title||'خبر')+'</b><small>'+escHtml(n.source||'مصدر')+'</small></a>').join(''):'')+'</div>';
 drawChart(chart.candles||[]);
}
function drawChart(candles){
 const canvas=document.getElementById('stockCanvas'); if(!canvas)return;
 const dpr=Math.max(1,window.devicePixelRatio||1),w=canvas.clientWidth||600,h=230;
 canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
 const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
 ctx.clearRect(0,0,w,h);
 const bg=ctx.createLinearGradient(0,0,0,h);bg.addColorStop(0,'#071a2a');bg.addColorStop(1,'#020b14');
 ctx.fillStyle=bg;ctx.fillRect(0,0,w,h);
 if(!candles.length){
   ctx.fillStyle='#a9c1cf';ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('لا توجد بيانات شموع متاحة',w/2,40);return;
 }
 const vals=candles.map(x=>Number(x.close)).filter(Number.isFinite);
 if(!vals.length){ctx.fillStyle='#a9c1cf';ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('لا توجد أسعار صالحة للرسم',w/2,40);return;}
 const min=Math.min(...vals),max=Math.max(...vals),range=max-min||Math.max(max*.02,1),pad=range*.08;
 ctx.strokeStyle='rgba(91,211,255,.12)';ctx.lineWidth=1;
 for(let g=0;g<=4;g++){const y=18+g*(h-42)/4;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
 const points=candles.map((x,i)=>{
   const v=Number(x.close),px=i*(w-30)/Math.max(1,candles.length-1)+15,py=h-20-((v-(min-pad))/(range+2*pad))*(h-44);return [px,py,v];
 }).filter(p=>Number.isFinite(p[2]));
 const grad=ctx.createLinearGradient(0,0,w,0);grad.addColorStop(0,'#28d7ff');grad.addColorStop(1,'#55f2b1');
 ctx.strokeStyle='rgba(40,215,255,.18)';ctx.lineWidth=9;ctx.beginPath();
 points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
 ctx.strokeStyle=grad;ctx.lineWidth=2.5;ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();
 points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
 ctx.fillStyle='#d9f5ff';ctx.font='bold 11px sans-serif';ctx.textAlign='left';
 ctx.fillText('$'+money(max),10,14);ctx.fillText('$'+money(min),10,h-4);
}
function formatTime(v){return v?new Date(v).toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'}):'—';}
function formatDateTime(v){return v?new Date(v).toLocaleString('ar-SA',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';}
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

async function loadDeployStatus(){
 try{
  const d=await api('/api/admin/deploy-status');
  const el=document.getElementById('deployStatus');
  if(!el)return;
  if(!d.ok){el.innerHTML='<div class="fatal">'+esc(d.message||'تعذر قراءة الحالة')+'</div>';return;}
  const running=d.status==='in_progress'||d.status==='queued';
  const ok=d.conclusion==='success';
  const icon=running?'🟡':(ok?'🟢':'🔴');
  const label=running?'قيد التنفيذ':(ok?'تم النشر بنجاح':(d.conclusion==='failure'?'فشل النشر':'غير معروف'));
  el.innerHTML='<div class="deploy-state"><b>'+icon+' '+label+'</b><span>Run #'+esc(d.run_number||'—')+' • محاولة '+esc(d.attempt||'—')+'</span><span>Commit: '+esc(d.sha||'—')+'</span><small>آخر تحديث: '+(d.updated_at?new Date(d.updated_at).toLocaleString('ar-SA'):'—')+'</small></div>';
 }catch(e){const el=document.getElementById('deployStatus');if(el)el.innerHTML='<div class="fatal">'+esc(e.message)+'</div>';}
}
function openDeployActions(){
 const url='https://github.com/pq070pq/sas/actions/workflows/deploy.yml';
 if(tg?.openLink)tg.openLink(url);else window.open(url,'_blank');
}

function adminSection(id,btn){
 const el=document.getElementById(id);
 if(!el || el.hidden)return;
 document.querySelectorAll('.admin-nav button').forEach(x=>x.classList.remove('active'));
 if(btn)btn.classList.add('active');
 el.scrollIntoView({behavior:'smooth',block:'start'});
}
async function adminRefresh(){
 const p=me?.admin_permissions||[];
 const tasks=[];
 if(p.includes('users')){tasks.push(loadAdminStats(),adminSearch(),loadAdminMonthlyReport());}
 if(p.includes('settings')){document.getElementById('planEditor').closest('.admin-panel').hidden=false;document.getElementById('plansEditorPanel').hidden=false;document.getElementById('deployPanel').hidden=false;tasks.push(loadAdminPlans(),loadSubscriptionConfig(),loadDeployStatus());}
 else {document.getElementById('planEditor').closest('.admin-panel').hidden=true;document.getElementById('plansEditorPanel').hidden=true;document.getElementById('deployPanel').hidden=true;document.getElementById('planEditor').closest('.admin-panel').previousElementSibling.hidden=true;}
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