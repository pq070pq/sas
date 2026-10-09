/* SAS PRO deployment trigger */
/* SAS PRO final deploy trigger */
let tg=window.Telegram&&window.Telegram.WebApp;
function refreshTelegramWebApp(){
 tg=window.Telegram&&window.Telegram.WebApp||tg;
 if(tg){try{tg.ready();tg.expand();tg.setHeaderColor('#020912');tg.setBackgroundColor('#020912');}catch(e){}}
 return tg;
}
refreshTelegramWebApp();
const digitObserver=new MutationObserver(()=>normalizeEnglishDigits(document.body));
digitObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
const getInitData=()=>tg?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')||new URLSearchParams(location.search).get('tgWebAppData')||'';
const headers=()=>{const d=getInitData();return {'X-Telegram-Init-Data':d,'Authorization':d?'tma '+d:''};};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitForTelegramInitData(maxWait=5000){
 const started=Date.now();
 while(Date.now()-started<maxWait){
  refreshTelegramWebApp();
  const data=getInitData();
  if(data)return data;
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 return '';
}
async function api(path,opt={}){
 const timeoutMs=Number(opt.timeoutMs||15000);
 const retries=Number.isFinite(Number(opt.retries))?Number(opt.retries):(String(opt.method||'GET').toUpperCase()==='GET'?1:0);
 const {timeoutMs:_,retries:__,...fetchOptions}=opt;
 let lastError=null;
 for(let attempt=0;attempt<=retries;attempt++){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{
   refreshTelegramWebApp();
   fetchOptions.headers=Object.assign(headers(),fetchOptions.headers||{});
   fetchOptions.signal=controller.signal;
   fetchOptions.cache='no-store';
   const r=await fetch(path,fetchOptions);
   if(!r.ok){
    let msg='تعذر تنفيذ العملية';
    try{const d=await r.json();msg=d.detail||d.message||msg;}
    catch(e){try{const t=(await r.text()).trim();if(t)msg=t;}catch(_){}}
    // إعادة المحاولة فقط للأخطاء المؤقتة، وليس أخطاء الصلاحيات/الاشتراك.
    if(attempt<retries && (r.status===408||r.status===429||r.status>=500)){
     await sleep(350*(attempt+1));
     continue;
    }
    throw new Error(msg);
   }
   return await r.json();
  }catch(e){
   lastError=e;
   if(attempt<retries){
    await sleep(350*(attempt+1));
    continue;
   }
  }finally{clearTimeout(timeout);}
 }
 if(lastError?.name==='AbortError')throw new Error('تعذر تحديث البيانات في الوقت المحدد. سيُعاد الاتصال تلقائيًا عند المحاولة التالية.');
 throw lastError||new Error('تعذر تنفيذ العملية');
}
const enDigits=v=>String(v??'').replace(/[٠-٩]/g,d=>String(d.charCodeAt(0)-0x0660));
const normalizeEnglishDigits=root=>{if(!root)return;const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const nodes=[];while(w.nextNode())nodes.push(w.currentNode);nodes.forEach(n=>{const x=enDigits(n.nodeValue);if(x!==n.nodeValue)n.nodeValue=x;});};
const fmtDate=v=>v?enDigits(new Date(v).toLocaleDateString('ar-SA')):'—';
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
  refreshTelegramWebApp();
  if(!tg)throw new Error('تعذر الوصول إلى Telegram WebApp. افتح SAS PRO من داخل Telegram.');
  const initData=await waitForTelegramInitData(5000);
  if(!initData)throw new Error('لم تصل بيانات Telegram الموثقة إلى التطبيق. أغلق Mini App وافتحه من زر SAS PRO داخل Telegram ثم أعد المحاولة.');
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
  // لا يدخل المستخدم الرادار قبل الموافقة على النسخة الحالية من الشروط.
  if(!me.terms_accepted){
   openTerms('trial');
   return;
  }
 }catch(e){
  clearTimeout(watchdog);
  loadStarted=false;
  const msg=e?.message||'تعذر التحقق من Telegram.';
  document.body.innerHTML='<div class="fatal"><b>⚠️ تعذر فتح SAS PRO</b><br><small>'+escHtml(msg)+'</small><br><button onclick="location.reload()">إعادة المحاولة</button></div>';
 }
}

const terminalState={ticker:[],radar:[],watch:JSON.parse(localStorage.getItem('saspro_watchlist')||'[]'),timer:null,tab:'dashboard',refreshing:false,radarScanning:false};
function escHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}
function safeNewsUrl(value){
 try{
  const raw=String(value||'').trim();
  if(!raw||raw.split('').some(ch=>ch.charCodeAt(0)<=32)||raw.includes(String.fromCharCode(92)))return '';
  const url=new URL(raw);
  if(!['http:','https:'].includes(url.protocol)||!url.hostname||url.username||url.password)return '';
  return url.href;
 }catch{return '';}
}
function renderNewsItems(items,limit=5,translatedItems=[]){
  if(!Array.isArray(items)||!items.length)return '<div class="empty-state">📰 لا توجد أخبار موثقة متاحة حاليًا.</div>';
  const translations=Array.isArray(translatedItems)?translatedItems:[];
  const byUrl=new Map(translations.filter(x=>x&&typeof x==='object'&&safeNewsUrl(x.url)).map(x=>[safeNewsUrl(x.url),x]));
  return items.slice(0,limit).map(n=>{
   n=n&&typeof n==='object'?n:{};
   const url=safeNewsUrl(n.url);
   const originalHeadline=String(n.headline||n.title||'خبر');
   const translated=(url&&byUrl.get(url))||translations.find(x=>x&&typeof x==='object'&&String(x.headline||'').trim()===originalHeadline.trim());
   const translatedHeadline=String(translated?.translated_headline||'').trim();
   const headline=escHtml(translatedHeadline||originalHeadline);
   const originalTitle=translatedHeadline&&translatedHeadline!==originalHeadline?'<small class="news-original-title">العنوان الأصلي: '+escHtml(originalHeadline)+'</small>':'';
   const source=escHtml(n.source||translated?.source||'مصدر غير محدد');
   const aiSummary=String(translated?.summary||n.ai_summary||'').trim();
   const sourceSummary=String(n.summary||'').trim();
   const summary=aiSummary||sourceSummary;
   const label=aiSummary?'الملخص بالعربية':sourceSummary?'ملخص المصدر':'الملخص';
   const basis=(translated?.basis==='headline_only'||n.ai_summary_basis==='headline_only')?' <small>(مبني على العنوان فقط)</small>':'';
   const untranslated=!translatedHeadline&&!aiSummary?' <small>(الترجمة غير متاحة لهذا الخبر حاليًا)</small>':'';
   const body='<b>'+headline+'</b>'+originalTitle+'<small>'+source+(url?' • ↗ اضغط لفتح المصدر الأصلي':'')+'</small><span style="display:block;white-space:normal;margin-top:6px;line-height:1.55"><strong>'+label+':</strong> '+escHtml(summary||'لا يتوفر مختصر موثوق لهذا الخبر.')+basis+untranslated+'</span>';
   return url?'<a class="news-entry" href="'+escHtml(url)+'" target="_blank" rel="noopener noreferrer">'+body+'</a>':'<div class="news-entry">'+body+'</div>';
  }).join('');
}
function money(v){const n=Number(v);return Number.isFinite(n)&&n>0?n.toLocaleString('en-US',{minimumFractionDigits:n<10?2:0,maximumFractionDigits:4}):'—';}
function pct(v){const n=Number(v);return Number.isFinite(n)?(n>=0?'+':'')+n.toFixed(2)+'%':'—';}
function switchTerminalTab(tab){
 terminalState.tab=tab;
 document.querySelectorAll('.terminal-tab').forEach(x=>x.classList.toggle('active',x.id==='tab-'+tab));
 document.querySelectorAll('.terminal-tabs button').forEach(x=>{
  const active=x.dataset.tab===tab;
  x.classList.toggle('active',active);
  x.setAttribute('aria-selected',active?'true':'false');
 });
 if(tab==='radar')renderRadar();
 if(tab==='watch')renderWatchlist();
}
async function initTerminal(){
 const backBtn=document.getElementById('adminBackBtn');
 if(backBtn)backBtn.hidden=!Boolean(me?.admin);
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

async function openAdminUserView(){
 if(!me?.admin)return;
 document.getElementById('adminPage').hidden=true;
 document.getElementById('terminalPage').hidden=false;
 const backBtn=document.getElementById('adminBackBtn');
 if(backBtn)backBtn.hidden=false;
 await initTerminal();
}

function returnToAdminView(){
 if(!me?.admin)return;
 if(terminalState.timer){clearInterval(terminalState.timer);terminalState.timer=null;}
 document.getElementById('terminalPage').hidden=true;
 document.getElementById('adminPage').hidden=false;
 adminRefresh();
 window.scrollTo({top:0,behavior:'smooth'});
}

async function refreshTerminal(){
 if(terminalState.refreshing)return;
 terminalState.refreshing=true;
 try{
 const clock=document.getElementById('terminalClock');
 if(clock){
  const now=new Date();
  clock.innerHTML='<span class="clock-time">'+enDigits(now.toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'}))+'</span><small>🇸🇦 الرياض</small><i aria-hidden="true"></i>';
  clock.title='آخر تحديث للواجهة: '+enDigits(now.toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit',second:'2-digit'}));
 }
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
 if(terminalState.tab==='radar') await runRadar(false);
 if(terminalState.tab==='watch') renderWatchlist();
 }finally{
  terminalState.refreshing=false;
 }
}
function renderMarketStrip(s){
 const el=document.getElementById('marketStrip');
 if(el)el.remove();
}
function renderDashboard(d){
 const r=d.radar||{};
 if(Array.isArray(r.stocks)) terminalState.radar=r.stocks;
 const historical=Boolean(r.historical);
 const enabled=Boolean(r.enabled);
 const market=d.market||{};
 const session=String(market.session||'');
 const extended=['premarket','afterhours','night'].includes(session);
 const premarket=session==='premarket';
 const preopen=['overnight','weekend','holiday'].includes(session);
 const sessionLabel=market.label_ar||({
  premarket:'قبل الافتتاح 🟡',
  regular:'السوق مفتوح الآن 🟢',
  afterhours:'بعد الإغلاق 🟠',
  night:'التداول الإلكتروني الليلي 🟣',
  night_pending:'🟣 التداول الليلي يفتح قريبًا'
 }[session]||'');
 const nightPending=session==='night_pending';
 const nightMinutes=Number(market.minutes_to_night);
 const nightCountdown=nightPending && Number.isFinite(nightMinutes)
  ? ' — يفتح بعد '+Math.max(0,Math.ceil(nightMinutes))+' دقيقة'
  : '';
 const nextPremarket=market.next_premarket_riyadh||'11:00';
 const regularOpen=market.regular_open_riyadh||'16:30';
 const regularClose=market.regular_close_riyadh||'23:00';
 const afterClose=market.afterhours_close_riyadh||'03:00';
 document.getElementById('radarStatusText').textContent=nightPending
  ? '🟣 التداول الليلي يفتح قريبًا'+nightCountdown+' — الرصد لا ينشر إشارة إلا بعد تحقق السعر والسيولة والزخم.'
  : premarket
   ? '🟡 Pre-Market — الرصد المبكر يعمل حتى '+regularOpen+' بتوقيت الرياض، مع تأكيد السعر والسيولة والزخم.'
   : session==='regular'
    ? '🟢 السوق الأمريكي مفتوح — الجلسة الرسمية حتى '+regularClose+' بتوقيت الرياض.'
    : session==='afterhours'
     ? '🟠 After-Hours — التداول الممتد حتى '+afterClose+' بتوقيت الرياض.'
     : session==='night'
      ? '🟣 التداول الإلكتروني الليلي — الرصد يعمل ضمن الجلسة الليلية.'
      : preopen
       ? '🌙 السوق مغلق — يبدأ Pre-Market الساعة '+nextPremarket+' بتوقيت الرياض.'
       : enabled
     ? (extended
       ? '🟡 الرصد الآلي يعمل في '+sessionLabel+' — لا تُنشر الإشارة إلا بعد تأكيد السعر والسيولة والزخم.'
       : '🟢 الرصد الآلي يعمل — يبحث عن الأسهم التي تستوفي بوابة SAS PRO.')
     : (historical ? '🟡 هذه آخر بيانات رصد محفوظة من الجلسة السابقة.'
       : '🔴 الرصد متوقف حاليًا خارج جلسات الرصد.');
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
 if(terminalState.radarScanning)return;
 terminalState.radarScanning=true;
 try{
  if(show){document.getElementById('radarGrid').innerHTML='<div class="loading">🔎 يجري تحميل أحدث بيانات الرادار...</div>';switchTerminalTab('radar');}
  // التطبيق يعرض فرص اليوم المحفوظة من الرادار؛ الفحص الحي لا يُعاد تشغيله
  // عند كل فتح حتى لا نستهلك موارد مزود البيانات ولا نكرر الرصد.
  const d=await api('/api/radar/scan?fresh=0',{timeoutMs:30000});
  if(d?.error){
    const reason=String(d.error.message||d.error.type||'خطأ غير معروف');
    const diag=d.diagnostics||{};
    const label='تعذر تحديث بيانات الرادار: '+reason;
    const box=document.getElementById('radarGrid');
    if(box)box.innerHTML='<div class="fatal"><b>🔴 تعذر تحديث الرادار</b><br><small>'+escHtml(label)+'</small><br><small>تم الاحتفاظ بالبيانات السابقة إن وُجدت.</small></div>';
    console.error('SAS PRO radar API error:',d.error);
    return;
  }
  terminalState.radar=d.stocks||[];
  const diag=d.diagnostics||{};
  const nextPremarket=d.next_premarket_riyadh||'11:00';
  const mode=d.historical
   ? '🗂️ آخر رصد محفوظ'
   : d.session==='premarket'
    ? '🟡 فحص حي — Pre-Market'
    : d.session==='regular'
     ? '🟢 فحص حي — السوق الرسمي'
     : d.session==='afterhours'
      ? '🟠 فحص حي — After-Hours'
      : d.session==='night'
       ? '🟣 فحص حي — التداول الليلي'
       : d.session==='night_pending'
        ? '🟣 التداول الليلي يفتح قريبًا'
        : '🌙 السوق مغلق — يبدأ Pre-Market الساعة '+nextPremarket+' بتوقيت الرياض';
  const scanAt=d.scan_at?formatDateTime(d.scan_at):null;
  const sessionDate=d.session_date?formatSessionDate(d.session_date):null;
  const scanLabel=d.historical
   ? (sessionDate ? 'جلسة '+sessionDate+(scanAt?' — آخر تحديث '+scanAt:'') : 'غير متوفر')
   : (scanAt ? scanAt+' بتوقيت الرياض' : 'غير متوفر');
  document.getElementById('radarDiagnostics').innerHTML='<b class="radar-mode">'+mode+'</b><span>🕒 وقت الرصد: '+escHtml(scanLabel)+'</span><span>مرشحون '+Number(diag.candidates||0)+'</span><span>اجتازوا '+Number(diag.passed||0)+'</span><span>مستبعدون '+Number(diag.filtered||0)+'</span><span>أخطاء '+Number(diag.errors||0)+'</span>';
  const candidateEl=document.getElementById('radarCandidates');
  const candidates=Array.isArray(diag.filtered_examples)?diag.filtered_examples:[];
  if(candidateEl && candidates.length){
    candidateEl.hidden=false;
    candidateEl.innerHTML='<div class="candidate-title"><b>🔎 تحليل المرشحين</b><small>هذه الأسهم اجتازت مرحلة البحث الأولي ولم تدخل الإشارة النهائية. سبب الاستبعاد ظاهر لكل سهم.</small></div>'+
      '<div class="candidate-grid">'+candidates.slice(0,12).map(x=>{
        const sym=String(x.symbol||'—');
        return '<article class="candidate-card"><div><b>'+escHtml(sym)+'</b><small>'+escHtml(x.data_source||'مصدر الرصد')+'</small></div><p>'+escHtml(x.reason||'تم استبعاده في مرحلة لاحقة')+'</p><button onclick="openSymbol(\''+escHtml(sym)+'\')">⏳ تحليل</button></article>';
      }).join('')+'</div>';
  }else if(candidateEl){
    candidateEl.hidden=true;
    candidateEl.innerHTML='';
  }
  renderRadar();
  renderDashboard({radar:{enabled:d.enabled,opportunities:terminalState.radar.length,top_move_pct:Math.max(...terminalState.radar.map(x=>Number(x.change_pct)||-Infinity)),top_volume:Math.max(...terminalState.radar.map(x=>Number(x.volume)||-Infinity))}});
 }catch(e){
   const msg=String(e?.message||'');
   if(msg.includes('الموافقة على الشروط')){
    openTerms('trial');
    return;
   }
   if(!/تعذر تحديث البيانات|مهلة الاتصال/.test(msg)){
    document.getElementById('radarGrid').innerHTML='<div class="fatal">'+escHtml(msg)+'</div>';
   }else{
    console.warn('SAS PRO radar temporary connection issue:',msg);
   }
 }finally{
   terminalState.radarScanning=false;
 }
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
 const sh=x.shariah||{};
 const shLabel=sh.status_ar||'غير واضح / يحتاج تحقق';
 const shClass=sh.status==='halal'||sh.status==='compliant'?'sh-ok':(sh.status==='haram'||sh.status==='non_compliant'?'sh-bad':'sh-unknown');
 const gates=x.radar_checks||{};
 const catalyst=Number(x.catalyst_score||0);
 const catalystLabel=catalyst>=75?'🔥 محفز قوي':catalyst>=55?'⚡ محفز متوسط':catalyst>0?'📰 محفز ضعيف':'📰 دون محفز حديث';
 const gate=(ok,label)=>'<i class="'+(ok?'gate-ok':'gate-warn')+'">'+(ok?'✓ ':'⚠️ ')+label+'</i>';
 const recommendation=(Number(rr)<1||warning) ? '🔴 لا تدخل الآن: الربح المتوقع لا يعوض المخاطرة.' : (Number(change)>10 ? '🟡 لا تطارد السهم: ارتفع بسرعة، انتظر هدوء الحركة وتأكيد جديد.' : (Number(score)>=70 ? '🟢 فرصة جيدة للمراقبة: انتظر تأكيد الاختراق والسيولة قبل الدخول.' : '🟡 مراقبة فقط: الإشارة غير قوية بما يكفي للدخول الآن.'));
 return '<article class="stock-card"><div class="stock-head"><div><b>'+s+'</b><small>'+(section==='large'?'سهم كبير / متوسط':'سهم صغير')+(x.created_at?' • '+formatTime(x.created_at):'')+'</small></div><span class="'+(Number(change)>=0?'up':'down')+'">'+pct(change)+'</span></div>'+
 '<strong>$'+money(price)+'</strong>'+
 '<div class="stock-levels"><span>دخول <b>$'+money(price)+'</b></span><span>وقف <b>$'+money(stop)+'</b></span><span>هدف 1 <b>$'+money(target)+'</b></span></div>'+
 '<div class="stock-meta"><span>RVOL '+(Number.isFinite(rvol)&&rvol>0?rvol.toFixed(2):'—')+'×</span><span>حجم '+(Number.isFinite(volume)&&volume>0?volume.toLocaleString('en-US'):'—')+'</span><span>R:R '+(rr!=null?Number(rr).toFixed(2):'—')+(warning?' ⚠️':'')+'</span></div>'+
 '<div class="stock-gates">'+gate(gates.sas_core!==false,'SAS Core')+gate(gates.liquidity!==false,'السيولة')+gate(gates.target!==false,'الهدف')+gate(gates.live_levels!==false,'المستويات')+'</div>'+
 ' <div class="stock-shariah '+shClass+'">🕌 <b>الشرعية:</b> '+escHtml(shLabel)+'</div>'+
 '<div class="stock-recommendation">📌 <b>القراءة:</b> '+escHtml(recommendation)+'</div>'+
 '<div class="stock-summary">'+(Number.isFinite(score)?'<span>⭐ قوة '+score.toFixed(0)+'/100</span>':'')+'<span>'+catalystLabel+(catalyst>0?' '+catalyst+'/100':'')+'</span>'+(x.live_price_source?'<span>📡 '+escHtml(x.live_price_source)+'</span>':'')+'</div>'+
 '<div class="stock-ai">'+escHtml(ai.key_takeaway||ai.headline_summary||cls.reason||'تحليل AI يظهر عند فتح التحليل الكامل.')+'</div>'+
 '<div class="stock-actions"><button onclick="event.stopPropagation();openSymbol(\''+raw+'\',true)">📈 الشارت والتوصية</button><button onclick="event.stopPropagation();toggleWatch(\''+raw+'\')">'+(terminalState.watch.includes(raw)?'★ محفوظ':'☆ حفظ')+'</button></div></article>';
}
function toggleWatch(symbol){symbol=symbol.toUpperCase();terminalState.watch=terminalState.watch.includes(symbol)?terminalState.watch.filter(x=>x!==symbol):[...terminalState.watch,symbol];localStorage.setItem('saspro_watchlist',JSON.stringify(terminalState.watch));renderWatchlist();renderRadar();}
async function renderWatchlist(){
 const el=document.getElementById('watchGrid'); if(!terminalState.watch.length){el.innerHTML='<div class="empty-state">أضف الأسهم من الرادار أو صفحة التحليل.</div>';return;}
 el.innerHTML='<div class="loading">جاري تحديث المحفوظة...</div>';
 const rows=await Promise.all(terminalState.watch.slice(0,20).map(async s=>{try{return await api('/api/stocks/'+encodeURIComponent(s)+'/quote');}catch(e){return {symbol:s};}}));
 el.innerHTML=rows.map(x=>stockCard(x)).join('');
}
function openSymbol(symbol,focusChart=false){document.getElementById('symbolSearch').value=symbol;switchTerminalTab('search');analyzeSymbol().then(()=>{if(focusChart){const detail=document.getElementById('symbolResult');const chart=detail?.querySelector('.chart-title');if(chart){chart.scrollIntoView({behavior:'smooth',block:'start'});chart.classList.add('chart-focus');setTimeout(()=>chart.classList.remove('chart-focus'),1800);}}});}
async function searchShariah(){
 const input=document.getElementById('symbolSearch');
 const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,'');
 const el=document.getElementById('shariahSearchResult');
 if(!el)return;
 if(!symbol){
  el.hidden=false;
  el.innerHTML='<div class="shariah-search-card sh-unknown"><b>🕌 بحث الشرعية</b><p>اكتب رمز السهم أولًا، مثل <b>AAPL</b> أو <b>HTZ</b>.</p></div>';
  return;
 }
 el.hidden=false;
 el.innerHTML='<div class="shariah-search-card sh-unknown"><b>🕌 بحث الشرعية — '+escHtml(symbol)+'</b><p>⏳ جاري التحقق من النتيجة المنشورة...</p></div>';
 try{
  const result=await api('/api/stocks/'+encodeURIComponent(symbol)+'/shariah',{timeoutMs:12000});
  const status=result?.status;
  const label=result?.status_ar||'غير واضح / يحتاج تحقق';
  const cls=status==='compliant'||status==='halal'?'sh-ok':(status==='non_compliant'||status==='haram'?'sh-bad':'sh-unknown');
  const icon=cls==='sh-ok'?'🟢':(cls==='sh-bad'?'🔴':'🟡');
  const sources=Array.isArray(result?.sources)?result.sources:[];
  const sourceLines=sources.length?sources.map(src=>{
   if(!src||typeof src!=='object')return '';
   const name=escHtml(src.source||'مصدر');
   const statusText=escHtml(src.status_ar||'غير واضح / يحتاج تحقق');
   let meaning='';
   if(src.source==='مصرف الراجحي') meaning='المعيار الشرعي المستخدم للتحقق، وليس حكمًا مباشرًا على السهم.';
   else if(src.source==='بنك البلاد') meaning='مرجع شرعي منهجي، وليس حكمًا مباشرًا على السهم.';
   else if(src.source==='يقين') meaning=src.verified?'نتيجة منشورة مباشرة للسهم.':'لم تظهر نتيجة مباشرة موثقة.';
   else if(src.source==='فلترنا') meaning='مرجع فلترة؛ لا نعتمد حكمًا للسهم إذا لم تظهر نتيجة مباشرة.';
   else meaning=src.verified?'نتيجة مباشرة موثقة.':'لا توجد نتيجة مباشرة موثقة.';
   return '<div class="shariah-source-row"><span><b>'+name+'</b><small>'+escHtml(meaning)+'</small></span><b>'+statusText+'</b></div>';
  }).filter(Boolean).join(''):'<div class="shariah-source-row"><span>المصادر</span><b>لا توجد نتيجة موثقة كافية</b></div>';
  el.innerHTML='<div class="shariah-search-card '+cls+'">'+
   '<div class="shariah-search-head"><div><span>🕌 بحث الشرعية</span><h3>$'+escHtml(symbol)+'</h3></div><strong>'+icon+' '+escHtml(label)+'</strong></div>'+
   '<p class="shariah-search-reason">'+escHtml(result?.reason||result?.message||'تم التحقق دون تأليف نتيجة.')+'</p>'+
   (result?.updated_at?'<small>📅 آخر تحديث منشور: '+escHtml(result.updated_at)+'</small>':'')+
   '<div class="shariah-source-list">'+sourceLines+'</div>'+
   '<div class="shariah-search-note">⛔ النتيجة معلوماتية للتحقق فقط، وشرعية السهم مسؤوليتك. لا يتم اعتماد نتيجة غير موثقة.</div>'+
   '</div>';
 }catch(e){
  el.innerHTML='<div class="shariah-search-card sh-unknown"><b>🕌 بحث الشرعية</b><p>⚠️ تعذر التحقق الآن؛ لم يتم تأليف أي نتيجة.</p></div>';
 }
}

async function analyzeSymbol(){
 const input=document.getElementById('symbolSearch');
 const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,'');
 if(!symbol)return;
 const el=document.getElementById('symbolResult');
 el.innerHTML='<div class="loading">⏳ يجري تحليل '+escHtml(symbol)+'...</div>';
 const [qR,chartR,newsR,analysisR,miniR,shariahR]=await Promise.allSettled([
   api('/api/stocks/'+encodeURIComponent(symbol)+'/quote'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/chart'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/news'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/analyze',{method:'POST',timeoutMs:30000}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/mini-analysis',{timeoutMs:30000}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/shariah',{timeoutMs:12000})
 ]);
 const qRaw=qR.status==='fulfilled'?qR.value:{symbol,price:null,change_pct:null,source:'غير متاح'};
 const miniPayload=miniR.status==='fulfilled'&&miniR.value&&typeof miniR.value==='object'?miniR.value:null;
 const miniQuote=miniPayload?.quote&&typeof miniPayload.quote==='object'?miniPayload.quote:{};
 const q=(Number(qRaw?.price)>0||Number.isFinite(Number(qRaw?.change_pct)))
   ? qRaw
   : Object.assign({symbol},miniQuote,{source:miniQuote.source||'SAS PRO OHLCV'});
 const chart=chartR.status==='fulfilled'&&chartR.value&&typeof chartR.value==='object'?chartR.value:{candles:[],available:false};
 const news=newsR.status==='fulfilled'&&Array.isArray(newsR.value)?newsR.value:[];
 const analysis=analysisR.status==='fulfilled'?analysisR.value:null;
 const shariah=shariahR.status==='fulfilled'?shariahR.value:(analysis?.sas_pro?.targets?.shariah||null);
 if(!analysis){
   const errors=[qR,chartR,newsR,analysisR].filter(x=>x.status==='rejected').map(x=>x.reason?.message).filter(Boolean);
   el.innerHTML='<div class="fatal">⚠️ تعذر إكمال التحليل الكامل<br><small>لكن تم إبقاء البيانات التي نجح تحميلها.</small>'+(errors.length?'<br><small>'+escHtml(errors[0])+'</small>':'')+'</div>';
   if(Number(q.price)>0||news.length||chart.candles?.length) renderPartialAnalysis(el,symbol,q,chart,news,miniR.status==='fulfilled'?miniR.value:null);
   return;
 }
 const tech=analysis.sas_pro?.targets||{};
 const sh=shariah||tech.shariah||{};
 const ai=analysis.analysis||{};
 const fcc=analysis.sas_pro?.targets?.fcc_review||ai.fcc_review||{};
 const fccHtml=fcc.available ? '<div class="ai-box"><b>⏳ مراجعة الذكاء الاصطناعي للسهم</b><p><b>التقييم:</b> '+escHtml(fcc.review_level||'محايد')+'</p>'+((fcc.strengths||[]).length?'<p><b>💪 نقاط القوة:</b><br>'+fcc.strengths.slice(0,4).map(x=>'• '+escHtml(x)).join('<br>')+'</p>':'')+((fcc.contradictions||[]).length?'<p><b>⚠️ نقاط تحتاج انتباه:</b><br>'+fcc.contradictions.slice(0,4).map(x=>'• '+escHtml(x)).join('<br>')+'</p>':'')+(fcc.note?'<p><b>📌 الخلاصة:</b> '+escHtml(fcc.note)+'</p>':'')+'<small>مراجعة مساعدة لفهم البيانات فقط، ولا تغيّر مستويات SAS PRO.</small></div>' : '';
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
 const recommendation=(partial||!Number.isFinite(entry)||!Number.isFinite(stop)||!Number.isFinite(target1)||entry<=stop||target1<=entry) ? '⚪ الحالة: البيانات غير مكتملة؛ راقب فقط حتى تتأكد الأسعار والمستويات.' : ((Number(rr)<1||Boolean(tech.risk_reward_warning)) ? '🔴 الحالة: المخاطرة مرتفعة مقارنة بالهدف؛ لا تدخل الآن.' : (Number(q.change_pct)>10 ? '🟡 الحالة: السهم ارتفع بسرعة؛ تجنب المطاردة وانتظر إعادة اختبار.' : (Number(tech.score||0)>=70 ? '🟢 الحالة: إعداد فني جيد للمراقبة؛ انتظر تأكيد الاختراق والسيولة.' : '🟡 الحالة: مراقبة فقط؛ التأكيد الفني غير كافٍ بعد.')));
 const summary=ai.key_takeaway||ai.headline_summary||'لا توجد خلاصة موثقة متاحة حاليًا.';
 const mini=analysis.mini_analysis||{};
 const recommendationHtml='<section class="recommendation-box"><b>📌 القراءة الفنية</b><strong>'+escHtml(recommendation)+'</strong><small>قراءة آلية وليست توصية شراء أو بيع.</small></section>';
 const shSources=Array.isArray(sh.sources)?sh.sources:[];
 const shHtml='<section class="shariah-box '+(sh.status==='halal'||sh.status==='compliant'?'sh-ok':(sh.status==='haram'||sh.status==='non_compliant'?'sh-bad':'sh-unknown'))+'"><div><b>🕌 التحقق الشرعي</b><strong>'+escHtml(sh.status_ar||'غير واضح / يحتاج تحقق')+'</strong></div><p>'+escHtml(sh.message||'لم تتوفر نتيجة موثقة؛ لم يتم التأليف.')+'</p><div class="shariah-help"><b>كيف تقرأ النتيجة؟</b><span>يقين = نتيجة السهم المنشورة مباشرة.</span><span>الراجحي وبنك البلاد = مراجع للمعايير الشرعية، وليس حكمًا مباشرًا على السهم.</span><span>فلترنا = مرجع فلترة؛ إذا لم تظهر نتيجة مباشرة فلا نعتمد حكمًا.</span></div>'+(sh.ai?.summary?'<p>🧠 '+escHtml(sh.ai.summary)+'</p>':'')+'<em>⛔ هذه معلومات للتحقق فقط وليست فتوى، وقرار الشرعية مسؤوليتك.</em></section>';
const miniAnalysisHtml=
   '<section class="mini-analysis">'+
     '<div class="mini-analysis-head"><div><span class="eyebrow">SAS PRO QUICK ANALYSIS</span><b>⏳ تحليل مختصر</b></div></div>'+
     '<div class="mini-analysis-grid">'+
       '<div><small>📊 الاتجاه</small><b>'+escHtml(mini.direction||'غير واضح')+'</b></div>'+
       '<div><small>🚀 الزخم</small><b>'+escHtml(mini.momentum||'—')+'</b></div>'+
       '<div><small>💧 السيولة</small><b>'+escHtml(mini.liquidity||'—')+'</b></div>'+
       '<div><small>📈 الإشارة</small><b>'+escHtml(mini.signal||'محايدة')+'</b></div>'+
     '</div>'+
     '<div class="mini-levels">'+
       '<span>🎯 الهدف <b>&#36;'+money(mini.target)+'</b></span>'+
       '<span>🛑 الوقف <b>&#36;'+money(mini.stop)+'</b></span>'+
     '</div>'+
     '<p class="mini-takeaway">'+escHtml(mini.takeaway||summary)+'</p>'+
     
   '</section>';
 el.innerHTML=
   '<div class="detail-head"><div><span class="eyebrow">SAS PRO STOCK</span><h2>'+escHtml(symbol)+'</h2></div><button onclick="toggleWatch(\''+escHtml(symbol)+'\')">'+(terminalState.watch.includes(symbol)?'★ محفوظ':'☆ حفظ')+'</button></div>'+
   shHtml+
   recommendationHtml+
   '<div class="quote-line"><strong>&#36;'+money(q.price)+'</strong><span class="'+(Number(q.change_pct)>=0?'up':'down')+'">'+pct(q.change_pct)+'</span><span>'+escHtml(q.source||'')+'</span></div>'+
   notice+
   miniAnalysisHtml+
   '<div class="chart-title"><b>📈 شارت السهم</b><div class="chart-title-actions"><small>شموع وحجم تداول • بيانات SAS PRO</small><a class="gocharting-link" href="https://gocharting.com/stock/'+encodeURIComponent(symbol)+'" target="_blank" rel="noopener noreferrer">↗ فتح GoCharting</a></div></div><div class="chart-box">'+(chart.available===false?'<div class="chart-unavailable">📊 البيانات الفنية التاريخية غير متاحة حاليًا<br><small>لم يتم اختلاق هدف أو وقف أو إشارة. سيتم إظهارها عند توفر بيانات الشموع والحجم.</small></div>':'<canvas id="stockCanvas" height="230"></canvas>')+'</div>'+
   '<div class="level-grid"><div><small>🟦 الدخول</small><b>&#36;'+money(entry)+'</b></div><div><small>🛑 الوقف</small><b>&#36;'+money(stop)+'</b></div><div><small>🎯 الهدف 1</small><b>&#36;'+money(target1)+'</b></div><div><small>⚖️ R:R</small><b>'+rr+'</b></div></div>'+
   '<div class="ai-box"><b>'+(aiAvailable?'⏳ زبدة تحليل AI':'📐 الخلاصة الفنية')+'</b><p>'+escHtml(summary)+'</p>'+(aiAvailable&&ai.provider?'<small>المزود: '+escHtml(ai.provider)+'</small>':'')+'</div>'+fccHtml+
   '<div class="news-list">'+renderNewsItems(newsFromAnalysis,5,Array.isArray(ai.news_summaries)?ai.news_summaries:[])+'</div>'+
   '<div class="terminal-disclaimer">🛡️ AI يفسّر الأدلة فقط ولا يغيّر قرار الرادار أو المستويات.</div>';
 const chartCandles=Array.isArray(chart.candles)?chart.candles:[];
 requestAnimationFrame(()=>drawChart(chartCandles));
 if(!chartCandles.length){
   const box=el.querySelector('.chart-box');
   if(box)box.insertAdjacentHTML('beforeend','<div class="chart-status">لا توجد شموع تاريخية مستلمة من الخادم لهذا السهم حاليًا.</div>');
 }
}
async function openPrivateAnalysis(symbol){
 symbol=String(symbol||'').trim().toUpperCase();
 if(!symbol)return;
 try{
   const d=await api('/api/stocks/'+encodeURIComponent(symbol)+'/private-link',{timeoutMs:10000});
   const url=String(d?.url||'');
   if(!/^https:\/\/t\.me\//i.test(url))throw new Error('رابط التحليل الخاص غير صالح.');
   refreshTelegramWebApp();
   if(tg?.openTelegramLink){
     try{ tg.openTelegramLink(url); return; }catch(e){}
   }
   if(tg?.openLink){
     try{ tg.openLink(url,{try_instant_view:false}); return; }catch(e){}
   }
   const w=window.open(url,'_blank','noopener,noreferrer');
   if(!w)window.location.href=url;
 }catch(e){
   const msg=String(e?.message||'');
   if(msg.includes('الموافقة على الشروط')){
    openTerms('trial');
    return;
   }
   alert(msg||'تعذر فتح التحليل الخاص. تحقق من صلاحية SAS PRO ثم أعد المحاولة.');
 }
}
if(!window.__sasPrivateAnalysisBound){
 window.__sasPrivateAnalysisBound=true;
 document.addEventListener('click',function(ev){
   const btn=ev.target&&ev.target.closest?ev.target.closest('[data-private-analysis]'):null;
   if(!btn)return;
   ev.preventDefault();
   ev.stopPropagation();
   openPrivateAnalysis(btn.getAttribute('data-private-analysis')||'');
 });
}
function renderPartialAnalysis(el,symbol,q,chart,news,miniData){
 const fallbackQuote=miniData?.quote&&typeof miniData.quote==='object'?miniData.quote:{};
 const displayQuote=(Number(q?.price)>0||Number.isFinite(Number(q?.change_pct)))?q:fallbackQuote;
 const price=Number(displayQuote?.price);
 const change=Number(displayQuote?.change_pct);
 const priceText=Number.isFinite(price)&&price>0?money(price):'—';
 const changeText=Number.isFinite(change)?pct(change):'—';
 el.innerHTML='<div class="detail-head"><div><span class="eyebrow">SAS PRO STOCK</span><h2>'+escHtml(symbol)+'</h2></div></div>'+
 '<div class="quote-line"><strong>&#36;'+priceText+'</strong><span class="'+(change>=0?'up':'down')+'">'+changeText+'</span><span>'+escHtml(q?.source||'')+'</span></div>'+
 '<div class="mini-analysis">'+
   '<div class="mini-analysis-head"><div><span class="eyebrow">SAS PRO QUICK ANALYSIS</span><b>⏳ التحليل الفني المختصر</b></div></div>'+
   '<div class="mini-analysis-grid">'+
   '<div><small>📊 الاتجاه</small><b>'+escHtml(miniData?.mini_analysis?.direction||'غير واضح')+'</b></div>'+
   '<div><small>🚀 الزخم</small><b>'+escHtml(miniData?.mini_analysis?.momentum||'—')+'</b></div>'+
   '<div><small>💧 السيولة</small><b>'+escHtml(miniData?.mini_analysis?.liquidity||'—')+'</b></div>'+
   '<div><small>📈 الإشارة</small><b>'+escHtml(miniData?.mini_analysis?.signal||'محايدة')+'</b></div>'+
   '</div>'+
   '<div class="mini-levels"><span>🎯 الهدف <b>&#36;'+money(miniData?.mini_analysis?.target)+'</b></span><span>🛑 الوقف <b>&#36;'+money(miniData?.mini_analysis?.stop)+'</b></span></div>'+
   '<p class="mini-takeaway">'+escHtml(miniData?.mini_analysis?.takeaway||'تعذر تحميل تحليل SAS المختصر حاليًا.')+'</p>'+
   
 '</div>'+
 '<div class="chart-title"><b>📈 شارت السهم</b><div class="chart-title-actions"><small>شموع وحجم تداول • بيانات SAS PRO</small><a class="gocharting-link" href="https://gocharting.com/stock/'+encodeURIComponent(symbol)+'" target="_blank" rel="noopener noreferrer">↗ فتح GoCharting</a></div></div><div class="chart-box"><canvas id="stockCanvas" height="260"></canvas></div>'+
 '<div class="news-list">'+renderNewsItems(news,5)+'</div>';
 const partialCandles=Array.isArray(chart?.candles)?chart.candles:[];
 requestAnimationFrame(()=>drawChart(partialCandles));
 if(!partialCandles.length){
   const box=el.querySelector('.chart-box');
   if(box)box.insertAdjacentHTML('beforeend','<div class="chart-status">لم تصل بيانات شموع تاريخية من الخادم؛ السعر المعروض لا يكفي لرسم شارت.</div>');
 }
}
function drawChart(candles,attempt=0){
 const canvas=document.getElementById('stockCanvas');if(!canvas)return;
 const actualWidth=canvas.getBoundingClientRect().width;
 if(actualWidth<2){if(attempt<10)requestAnimationFrame(()=>drawChart(candles,attempt+1));return;}
 const dpr=Math.max(1,window.devicePixelRatio||1),w=actualWidth,h=260;
 canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
 const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
 const bg=ctx.createLinearGradient(0,0,0,h);bg.addColorStop(0,'#071a2a');bg.addColorStop(1,'#020b14');ctx.fillStyle=bg;ctx.fillRect(0,0,w,h);
 if(!Array.isArray(candles)||!candles.length){ctx.fillStyle='#a9c1cf';ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('لا توجد بيانات شموع متاحة',w/2,42);return;}
 const rows=candles.map(x=>({o:Number(x.open),h:Number(x.high),l:Number(x.low),c:Number(x.close),v:Number(x.volume)})).filter(x=>Number.isFinite(x.c)&&x.c>0);
 if(!rows.length){ctx.fillStyle='#a9c1cf';ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('لا توجد أسعار صالحة للرسم',w/2,42);return;}
 const hasOHLC=rows.some(x=>Number.isFinite(x.o)&&Number.isFinite(x.h)&&Number.isFinite(x.l));
 const highs=rows.map(x=>Number.isFinite(x.h)?x.h:x.c),lows=rows.map(x=>Number.isFinite(x.l)?x.l:x.c);
 const min=Math.min(...lows),max=Math.max(...highs),range=max-min||Math.max(max*.02,.01),pad=range*.08;
 const top=24,bottom=hasOHLC?h-48:h-22,plotH=bottom-top,y=v=>bottom-((v-(min-pad))/(range+2*pad))*plotH;
 ctx.strokeStyle='rgba(91,211,255,.13)';ctx.lineWidth=1;
 for(let g=0;g<=4;g++){const yy=top+g*plotH/4;ctx.beginPath();ctx.moveTo(0,yy);ctx.lineTo(w,yy);ctx.stroke();const price=max+pad-(range+2*pad)*g/4;ctx.fillStyle='#8ba8b8';ctx.font='10px sans-serif';ctx.textAlign='left';ctx.fillText('$'+money(price),5,Math.max(11,yy-3));}
 const step=(w-24)/rows.length,bodyW=Math.max(2,Math.min(9,step*.62));
 if(hasOHLC){rows.forEach((x,i)=>{const px=12+i*step+step/2,open=Number.isFinite(x.o)?x.o:x.c,high=Number.isFinite(x.h)?x.h:Math.max(open,x.c),low=Number.isFinite(x.l)?x.l:Math.min(open,x.c),up=x.c>=open,color=up?'#26d9a0':'#ff657a';ctx.strokeStyle=color;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(px,y(high));ctx.lineTo(px,y(low));ctx.stroke();ctx.fillStyle=color;ctx.fillRect(px-bodyW/2,Math.min(y(open),y(x.c)),bodyW,Math.max(1.5,Math.abs(y(open)-y(x.c))));});}
 else{const points=rows.map((x,i)=>[12+i*step+step/2,y(x.c)]);ctx.strokeStyle='#36d9ff';ctx.lineWidth=2;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();}
 if(hasOHLC&&rows.some(x=>Number.isFinite(x.v)&&x.v>0)){const maxV=Math.max(...rows.map(x=>Number.isFinite(x.v)?x.v:0)),base=h-8,volH=23;rows.forEach((x,i)=>{if(!Number.isFinite(x.v)||x.v<=0)return;const px=12+i*step+step/2,open=Number.isFinite(x.o)?x.o:x.c;ctx.fillStyle=x.c>=open?'rgba(38,217,160,.55)':'rgba(255,101,122,.55)';ctx.fillRect(px-bodyW/2,base-(x.v/maxV)*volH,bodyW,(x.v/maxV)*volH);});}
 ctx.fillStyle='#d9f5ff';ctx.font='bold 11px sans-serif';ctx.textAlign='right';ctx.fillText('آخر إغلاق: $'+money(rows[rows.length-1].c),w-8,14);
}
function formatTime(v){return v?new Date(v).toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'}):'—';}
function formatDateTime(v){return v?new Date(v).toLocaleString('ar-SA',{weekday:'long',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';}
function formatSessionDate(v){return v?new Date(v+'T12:00:00Z').toLocaleDateString('ar-SA',{weekday:'long',year:'numeric',month:'2-digit',day:'2-digit'}):'—';}
function renderAccount(){
 const exp=me.expires_at||me.trial_expires; document.getElementById('accountCards').innerHTML='<div><small>الحالة</small><b>🟢 فعال</b></div><div><small>الباقة</small><b>'+escHtml(me.user?.plan||'SAS PRO')+'</b></div><div><small>الانتهاء</small><b>'+escHtml(exp?fmtDate(exp):'—')+'</b></div>';
}

function renderTrialCard(days){
 const d=Number(days||3);
 document.querySelector("#trialCard p").textContent=d+" يومًا للمستخدم الجديد • تبدأ تلقائيًا بعد الموافقة على الشروط، ثم يفتح رابط القناة مباشرة.";
 document.getElementById("trialBtn").textContent=d===30?"قراءة الشروط وبدء التجربة المجانية":"قراءة الشروط وبدء التجربة";
}

function renderStatus(x){
 const active=!!x.pro;
 const source=x.access?.source||null;
 const expiry=x.expires_at||null;
 const start=x.trial_start||x.subscription_start||null;
 document.getElementById('subStatus').textContent=active?'🟢 فعال':'🔒 غير مشترك';
 document.getElementById('subStatus').className=active?'ok':'bad';
 document.getElementById('subPlan').textContent=source==='trial'?'🎁 تجربة مجانية':(active?(x.plan||x.user?.plan||'اشتراك'):'—');
 document.getElementById('subStart').textContent=start?fmtDate(start):'—';
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
 document.getElementById('trialBtn').disabled=!x.trial_available;
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

function openTerms(action){
 termAction=action;
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(agree)agree.checked=false;
 if(button){
  button.disabled=false;
  button.textContent=action==='view'?'إغلاق':'أوافق على الشروط وأتابع';
 }
 document.getElementById('termsText').textContent='جاري تحميل الشروط...';
 document.getElementById('termsModal').hidden=false;
 requestAnimationFrame(()=>{if(agree)agree.focus();});
 api('/api/subscription/plans').then(d=>{document.getElementById('termsText').textContent=d.terms_text||'';}).catch(e=>{document.getElementById('termsText').textContent=e.message;});
}
function toggleTermsButton(){
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(!button||termAction==='view')return;
 button.disabled=false;
 button.textContent=agree?.checked?'أوافق على الشروط وأتابع':'أوافق على الشروط وأتابع';
}
function closeTerms(){document.getElementById('termsModal').hidden=true;termAction=null;}
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'){const m=document.getElementById('termsModal');if(m&&!m.hidden)closeTerms();}
 if(e.key==='Enter'&&document.activeElement&&document.activeElement.id==='adminSearch'){adminSearch();}
});
async function continueTerms(){
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(termAction==='view'){closeTerms();return;}
 // زر الموافقة نفسه تفاعلي: عند الضغط عليه تُسجّل الموافقة صراحة حتى لو لم يضغط المستخدم مربع الاختيار.
 if(!agree.checked)agree.checked=true;
 const action=termAction;
 const originalText=button.textContent;
 button.disabled=true;
 button.textContent='⏳ جاري التفعيل...';
 try{
  const accepted=await api('/api/terms/accept',{method:'POST'});

  // للمستخدم العادي يجب أن تبدأ التجربة ويُعاد رابط القناة. أما المالك/المشرف
  // فقد لا تُمنح له تجربة، لذلك لا نحول موافقة الشروط الصحيحة إلى خطأ.
  if(action==='trial'){
   if(accepted.trial_started&&accepted.trial?.channel_link){
    closeTerms();
    if(tg?.openTelegramLink)tg.openTelegramLink(accepted.trial.channel_link);
    else if(tg?.openLink)tg.openLink(accepted.trial.channel_link);
    else window.open(accepted.trial.channel_link,'_blank');
    setTimeout(()=>{loadStarted=false;load();},900);
    return;
   }
   if(accepted.ok){
    closeTerms();
    setTimeout(()=>{loadStarted=false;load();},300);
    return;
   }
   throw new Error('تعذر تفعيل التجربة أو إنشاء رابط القناة. حاول مرة أخرى.');
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

const ADMIN_ACCORDION_ITEMS=[
 ['adminOverview','⌂','نظرة عامة','الحالة العامة والإحصاءات والتنبيهات'],
 ['adminRadarPreviewPanel','📡','حالة الرادار','آخر دورة، أفضل الفرص وأسباب الاستبعاد'],
 ['adminUsersPanel','👥','المشتركون','البحث وإدارة المستخدمين'],
 ['termsAdminPanel','📋','موافقات الشروط','الموافقات ونسخة الشروط'],
 ['adminSubscriptionPanel','💎','الاشتراكات','التجربة والباقات وإعدادات الاشتراك'],
 ['starsPanel','⭐','المدفوعات','Telegram Stars والعمليات المالية'],
 ['staffPanel','🛡️','المشرفون','الأدوار والصلاحيات'],
 ['deployPanel','🚀','النشر','حالة GitHub Actions والنشر إلى OVH'],
 ['plansEditorPanel','💎','الباقات والأسعار','تعديل الأسعار وظهور الباقات'],
 ['ownerCard','👑','حساب المالك','بيانات الحساب الرئيسي'],
 ['monthly-report-panel','📊','التقرير الشهري','ملخص أداء المنصة الشهري']
];

function initAdminAccordion(){
 const monthly=document.querySelector('.monthly-report-panel');
 if(monthly && !monthly.id)monthly.id='monthly-report-panel';
 const page=document.getElementById('adminPage');
 const oldNav=page?.querySelector('.admin-nav');
 if(!page||!oldNav||oldNav.dataset.accordionReady==='1')return;
 const shell=document.createElement('div');
 shell.className='admin-accordion';
 shell.setAttribute('aria-label','أقسام إدارة SAS PRO');

 // افصل اللوحات الموجودة داخل النظرة العامة أولاً، ثم أعد ترتيبها
 // داخل قائمة واحدة حتى يفتح كل قسم مباشرة أسفل عنوانه.
 const overview=document.getElementById('adminOverview');
 const movingIds=ADMIN_ACCORDION_ITEMS.slice(1).map(x=>x[0]);
 const movingPanels=[];
 for(const id of movingIds){
  const el=document.getElementById(id);
  if(el && el.parentElement!==shell)movingPanels.push(el);
 }

 for(const [id,icon,title,subtitle] of ADMIN_ACCORDION_ITEMS){
  const panel=document.getElementById(id);
  if(!panel)continue;
  const item=document.createElement('section');
  item.className='admin-accordion-item';
  item.dataset.adminAccordionId=id;

  const header=document.createElement('button');
  header.type='button';
  header.className='admin-accordion-trigger';
  header.setAttribute('aria-controls',id);
  header.setAttribute('aria-expanded',id==='adminOverview'?'true':'false');
  header.innerHTML='<span class="admin-accordion-icon">'+icon+'</span><span class="admin-accordion-copy"><b>'+title+'</b><small>'+subtitle+'</small></span><span class="admin-accordion-chevron">⌄</span>';

  const body=document.createElement('div');
  body.className='admin-accordion-body';
  body.appendChild(panel);
  if(id!=='adminOverview')panel.hidden=true;

  header.addEventListener('click',()=>{
   const isOpen=header.getAttribute('aria-expanded')==='true';
   document.querySelectorAll('.admin-accordion-item').forEach(other=>{
    const h=other.querySelector('.admin-accordion-trigger');
    const b=other.querySelector('.admin-accordion-body');
    const p=other.querySelector('.admin-panel,.admin-overview,.owner-card');
    const same=other===item;
    const open=same?!isOpen:false;
    if(h)h.setAttribute('aria-expanded',String(open));
    if(b)b.classList.toggle('open',open);
    if(p){
     // لا نكسر إخفاء الصلاحيات؛ الفتح اليدوي فقط يزيل حالة الإغلاق.
     if(open)p.hidden=false;
     else if(p.dataset.adminPermissionHidden==='1')p.hidden=true;
     else p.hidden=true;
    }
   });
   if(!isOpen){
    body.classList.add('open');
    item.scrollIntoView({behavior:'smooth',block:'start'});
   }
  });

  item.appendChild(header);
  item.appendChild(body);
  shell.appendChild(item);
 }

 oldNav.replaceWith(shell);
 shell.querySelector('[data-admin-accordion-id="adminOverview"] .admin-accordion-body')?.classList.add('open');
 shell.querySelector('[data-admin-accordion-id="adminOverview"] .admin-accordion-trigger')?.classList.add('is-open');
 syncAdminAccordionVisibility();
}

function syncAdminAccordionVisibility(){
 const shell=document.querySelector('.admin-accordion');
 if(!shell)return;
 shell.querySelectorAll('.admin-accordion-item').forEach(item=>{
  const id=item.dataset.adminAccordionId;
  const panel=document.getElementById(id);
  const trigger=item.querySelector('.admin-accordion-trigger');
  if(!panel||!trigger)return;
  const permissionHidden=panel.dataset.adminPermissionHidden==='1';
  item.hidden=permissionHidden;
  if(permissionHidden){
   trigger.setAttribute('aria-expanded','false');
   item.querySelector('.admin-accordion-body')?.classList.remove('open');
  }
 });
}

function openAdminAccordionFor(id){
 initAdminAccordion();
 const item=document.querySelector('.admin-accordion-item[data-admin-accordion-id="'+id+'"]');
 const trigger=item?.querySelector('.admin-accordion-trigger');
 if(!item||item.hidden||!trigger)return;
 if(trigger.getAttribute('aria-expanded')!=='true')trigger.click();
}

function adminSection(id,btn){
 initAdminAccordion();
 const item=document.querySelector('.admin-accordion-item[data-admin-accordion-id="'+id+'"]');
 const trigger=item?.querySelector('.admin-accordion-trigger');
 if(!item||item.hidden||!trigger)return;
 const open=trigger.getAttribute('aria-expanded')==='true';
 trigger.click();
}

let adminHealthTimer=null;
let adminHealthBusy=false;
let adminHealthLastSignature='';

function renderAdminHealth(items){
 const list=document.getElementById('adminAlertsList');
 const summary=document.getElementById('adminAlertsSummary');
 const updated=document.getElementById('adminAlertsUpdated');
 if(!list||!summary)return;
 const bad=items.filter(x=>x.level==='error').length;
 const warn=items.filter(x=>x.level==='warn').length;
 updated.textContent='آخر فحص: '+new Date().toLocaleTimeString('ar-SA');
 const info=items.filter(x=>x.level==='info').length;
 if(!bad&&!warn){
  summary.innerHTML='<div class="admin-system-ok"><b>🟢 النظام سليم</b><small>'+(
    info ? 'لا توجد أعطال. توجد معلومات تشغيلية فقط.' : 'الرادار والخدمات الأساسية تعمل بشكل طبيعي.'
  )+'</small></div>';
  list.innerHTML=items.length
    ? items.map(x=>'<div class="admin-health-row ok"><span>🟢</span><div><b>'+esc(x.title)+'</b><small>'+esc(x.message)+'</small></div></div>').join('')
    : '<div class="admin-health-row ok"><span>✓</span><div><b>الحالة العامة سليمة</b><small>سيتم إعادة الفحص تلقائيًا كل دقيقة.</small></div></div>';
  return;
 }
 summary.innerHTML='<div class="admin-system-bad '+(bad?'critical':'warning')+'"><b>'+(bad?'🔴 يوجد عطل يحتاج انتباهك':'🟠 يوجد تنبيه يحتاج المراجعة')+'</b><small>'+bad+' عطل • '+warn+' تنبيه'+(info?' • '+info+' معلومة':'')+'</small></div>';
 list.innerHTML=items.map(x=>{
   const cls=x.level==='error'?'error':(x.level==='warn'?'warn':'ok');
   const icon=x.level==='error'?'🔴':(x.level==='warn'?'🟠':'🟢');
   return '<div class="admin-health-row '+cls+'"><span>'+icon+'</span><div><b>'+esc(x.title)+'</b><small>'+esc(x.message)+'</small></div></div>';
 }).join('');
}

async function checkAdminHealth(){
 if(adminHealthBusy)return;
 adminHealthBusy=true;
 const items=[];
 try{
  const d=await api('/api/admin/health',{timeoutMs:12000});
  const components=Array.isArray(d.components)?d.components:[];
  components.forEach(x=>{
   const level=x.status==='error'?'error':(x.status==='warn'?'warn':'info');
   const detail=x.detail?(' — '+x.detail):'';
   items.push({level,title:String(x.name||'مكوّن غير معروف'),message:String(x.message||'لا توجد تفاصيل')+detail});
  });
  if(!components.length){
   items.push({level:'warn',title:'مراقبة النظام',message:'الخادم استجاب لكن لم يُرجع تفاصيل مكونات الفحص.'});
  }
  renderAdminHealth(items);
 }catch(e){
  const msg=String(e?.message||'تعذر تنفيذ فحص الحالة.');
  renderAdminHealth([{level:'error',title:'اتصال لوحة الإدارة بالخادم',message:msg+' — تحقق من اتصال Mini App بالخادم ثم أعد المحاولة.'}]);
 }finally{
  adminHealthBusy=false;
 }
}

function startAdminHealthMonitor(){
 if(adminHealthTimer)clearInterval(adminHealthTimer);
 checkAdminHealth();
 adminHealthTimer=setInterval(checkAdminHealth,60000);
}

async function runAdminRadarPreview(){
 const btn=document.getElementById('adminRadarRunBtn');
 const summary=document.getElementById('adminRadarPreviewSummary');
 const top=document.getElementById('adminRadarPreviewTop');
 const rejects=document.getElementById('adminRadarPreviewRejects');
 if(!btn||!summary||!top||!rejects)return;
 const old=btn?.textContent || '';
 if(btn){btn.disabled=true; btn.textContent='⏳ الفحص التلقائي يعمل عبر scheduler…';}
 summary.innerHTML='<span class="subscriber-loading">جاري تشغيل Smart Levels + ICT على المرشحين الفعليين…</span>';
 top.innerHTML=''; rejects.innerHTML='';
 try{
  const d=await api('/api/admin/radar/run-preview',{method:'POST'});
  const s=d.scanner||{};
  summary.innerHTML='<b>✅ اكتمل الفحص</b><span>المرشحون: '+Number(s.candidates||0).toLocaleString('en-US')+' • المختصر: '+Number(s.shortlist||0).toLocaleString('en-US')+' • المؤكد: '+Number(s.confirmed||0)+' • مراقبة: '+Number(s.watch||0)+' • مرفوض: '+Number(s.filtered||0)+' • أخطاء: '+Number(s.errors||0)+'</span><small>'+esc(d.message||'')+'</small>';
  const rows=Array.isArray(d.top5)?d.top5:[];
  let topHtml='<div class="section-head"><b>🔥 أفضل 5</b><span>ترتيب الفحص الجديد</span></div>';
  if(rows.length){
   topHtml+=rows.map((x,i)=>{
    return '<article class="subscriber-status-row"><div class="terms-admin-avatar">'+(i+1)+'</div><div class="terms-admin-main"><b>'+esc(x.symbol||'—')+'</b><small>السعر: '+esc(x.price??'—')+' • التغير: '+esc(x.change_pct??'—')+'% • RVOL: '+esc(x.rvol??'—')+'×</small><span>Smart Score: '+esc(x.smart_levels_score??'—')+' • '+esc(x.smart_levels_status||'—')+' • R:R '+esc(x.risk_reward??'—')+'</span><small>'+esc((x.gate_reasons||[]).slice(0,4).join(' • '))+'</small></div></article>';
   }).join('');
  }else{
   topHtml+='<div class="empty-state">لا توجد فرصة مؤكدة حاليًا وفق البوابة الجديدة.</div>';
  }
  top.innerHTML=topHtml;
  const rs=Array.isArray(d.rejections)?d.rejections:[];
  rejects.innerHTML='<div class="section-head"><b>🧪 أبرز أسباب الاستبعاد</b><span>أول 20 حالة</span></div>'+(rs.length?rs.map(x=>'<article class="subscriber-status-row"><div class="terms-admin-avatar">🔴</div><div class="terms-admin-main"><b>'+esc(x.symbol||'—')+'</b><small>'+esc(x.reason||'غير محدد')+'</small></div></article>').join(''):'<div class="empty-state">لا توجد حالات استبعاد مسجلة في هذه الجولة.</div>');
 }catch(e){
  summary.innerHTML='<div class="fatal">'+esc(e.message||'فشل تشغيل الفحص.')+'</div>';
 }finally{if(btn){btn.disabled=false;btn.textContent=old;}}
}

async function adminRefresh(){
 initAdminAccordion();
 const p=me?.admin_permissions||[];
 const permissionRules={
  adminRadarPreviewPanel:p.includes('radar'),
  adminUsersPanel:p.includes('users'),
  termsAdminPanel:p.includes('users'),
  subscriberStatusPanel:p.includes('users'),
  'monthly-report-panel':p.includes('users'),
  adminSubscriptionPanel:p.includes('subscriptions')||p.includes('settings'),
  starsPanel:p.includes('payments'),
  staffPanel:p.includes('admins'),
  deployPanel:p.includes('settings'),
  plansEditorPanel:p.includes('settings')
 };
 const monthlyPanel=document.getElementById('monthly-report-panel');
 for(const [id,allowed] of Object.entries(permissionRules)){
  const panel=document.getElementById(id);
  if(!panel)continue;
  panel.dataset.adminPermissionHidden=allowed?'0':'1';
  if(!allowed)panel.hidden=true;
 }
 const radarPanel=document.getElementById('adminRadarPreviewPanel');
 const radarNav=document.querySelector('[data-admin-target="adminRadarPreviewPanel"]');
 if(radarPanel){radarPanel.hidden=!p.includes('radar');radarPanel.dataset.adminPermissionHidden=(!p.includes('radar'))?'1':'0';}
 if(radarNav)radarNav.hidden=!p.includes('radar');
 const tasks=[];
 if(p.includes('users')){tasks.push(loadAdminStats(),adminSearch(),loadAdminMonthlyReport(),loadAdminTerms());}
 if(p.includes('settings')){document.getElementById('planEditor').closest('.admin-panel').hidden=false;document.getElementById('plansEditorPanel').hidden=false;document.getElementById('plansEditorPanel').dataset.adminPermissionHidden='0';document.getElementById('deployPanel').hidden=false;document.getElementById('deployPanel').dataset.adminPermissionHidden='0';tasks.push(loadAdminPlans(),loadSubscriptionConfig(),loadDeployStatus());}
 else {document.getElementById('planEditor').closest('.admin-panel').hidden=true;document.getElementById('plansEditorPanel').hidden=true;document.getElementById('plansEditorPanel').dataset.adminPermissionHidden='1';document.getElementById('deployPanel').hidden=true;document.getElementById('deployPanel').dataset.adminPermissionHidden='1';}
 if(p.includes('payments')){document.getElementById('starsPanel').hidden=false;document.getElementById('starsPanel').dataset.adminPermissionHidden='0';tasks.push(loadStarsWallet());}else{document.getElementById('starsPanel').hidden=true;document.getElementById('starsPanel').dataset.adminPermissionHidden='1';}
 await Promise.all(tasks);
 syncAdminAccordionVisibility();
 startAdminHealthMonitor();
}
const STAFF_ROLE_PERMISSIONS={
 moderator:['users','channel'],
 radar_manager:['radar'],
 subscription_manager:['subscriptions','payments'],
 admin:['users','subscriptions','channel','radar','payments','admins','settings','audit'],
 viewer:[]
};
const STAFF_ROLE_LABELS={
 moderator:'مشرف عام',
 radar_manager:'مشرف الرادار',
 subscription_manager:'مشرف الاشتراكات',
 admin:'مدير كامل',
 viewer:'مشاهد فقط',
 owner:'المالك'
};
const STAFF_ROLE_HINTS={
 moderator:'المشرف العام: إدارة المستخدمين والقناة.',
 radar_manager:'مشرف الرادار: إدارة ومتابعة الرصد.',
 subscription_manager:'مشرف الاشتراكات: إدارة الاشتراكات والمدفوعات.',
 admin:'المدير الكامل: جميع الصلاحيات الإدارية.',
 viewer:'مشاهد فقط: بدون صلاحيات تعديل.'
};
function updateStaffRoleHint(){
 const role=document.getElementById('staffRole')?.value;
 const hint=document.getElementById('staffRoleHint');
 if(hint)hint.textContent=STAFF_ROLE_HINTS[role]||'';
}
async function loadStaff(){
 try{
  const d=await api('/api/admin/staff');
  const list=Array.isArray(d)?d:[];
  const count=document.getElementById('staffCount');
  if(count)count.textContent=list.filter(x=>x.role!=='owner').length+' مشرف';
  const el=document.getElementById('staffList');
  if(!el)return;
  el.innerHTML=list.map(x=>{
   const isOwner=x.role==='owner';
   const roleLabel=STAFF_ROLE_LABELS[x.role]||x.role||'مشرف';
   const perms=(x.permissions||[]).length;
   const status=x.enabled?'🟢 فعال':'🔴 معطل';
   const actions=isOwner
    ? '<span class="staff-owner-tag">حساب المالك</span>'
    : '<div class="staff-row-actions">'+
      (x.enabled
       ? '<button class="staff-disable" onclick="staffToggle('+x.telegram_id+',false)">تعطيل</button>'
       : '<button class="staff-enable" onclick="staffToggle('+x.telegram_id+',true)">تفعيل</button>')+
      '<button class="staff-delete" onclick="staffDelete('+x.telegram_id+')">حذف</button>'+
      '</div>';
   return '<article class="staff-simple-row">'+
    '<div class="staff-avatar">'+(isOwner?'👑':'🛡️')+'</div>'+
    '<div class="staff-row-main"><b>'+esc(roleLabel)+'</b><small>Telegram ID: '+esc(x.telegram_id)+'</small><span class="'+(x.enabled?'staff-status-on':'staff-status-off')+'">'+status+' • '+(isOwner?'كل الصلاحيات':perms+' صلاحيات')+'</span></div>'+
    actions+
   '</article>';
  }).join('')||'<div class="empty-state">لا يوجد مشرفون مضافون حاليًا.</div>';
 }catch(e){
  const el=document.getElementById('staffList');
  if(el)el.innerHTML='<div class="fatal">'+esc(e.message)+'</div>';
 }
}
async function addStaff(){
 const input=document.getElementById('staffId');
 const id=Number(input?.value);
 const role=document.getElementById('staffRole')?.value||'moderator';
 if(!Number.isInteger(id)||id<=0){alert('أدخل Telegram ID صحيحًا.');return;}
 const permissions=STAFF_ROLE_PERMISSIONS[role]||[];
 const button=document.querySelector('.staff-save-btn');
 const oldText=button?.textContent;
 if(button){button.disabled=true;button.textContent='جاري الحفظ…';}
 try{
  await api('/api/admin/staff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({telegram_id:id,role,permissions})});
  if(input)input.value='';
  await loadStaff();
  alert('تم حفظ المشرف: '+(STAFF_ROLE_LABELS[role]||role));
 }catch(e){alert(e.message);}
 finally{if(button){button.disabled=false;button.textContent=oldText||'حفظ المشرف';}}
}
async function staffToggle(id,enabled){
 try{await api('/api/admin/staff/'+id+'/'+(enabled?'enable':'disable'),{method:'POST'});await loadStaff();}
 catch(e){alert(e.message);}
}
async function staffDelete(id){
 if(!confirm('حذف هذا المشرف نهائيًا؟'))return;
 try{await api('/api/admin/staff/'+id,{method:'DELETE'});await loadStaff();}
 catch(e){alert(e.message);}
}
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

async function loadAdminTerms(){
 try{
  const d=await api('/api/admin/terms-status');
  const rows=Array.isArray(d.users)?d.users:[];
  const accepted=Number(d.accepted||0), pending=Number(d.pending||0), total=Number(d.total||rows.length);
  const summary=document.getElementById('termsAdminSummary');
  const list=document.getElementById('termsAdminList');
  if(summary)summary.innerHTML='<b>'+accepted.toLocaleString('en-US')+'</b><span>موافقون • الإجمالي: '+total.toLocaleString('en-US')+' • النسخة الحالية: '+esc(d.terms_version||'—')+'</span>';
  if(list)list.innerHTML=rows.length?rows.map(u=>{
   const name=[u.first_name,u.last_name].filter(Boolean).join(' ')||'بدون اسم';
   const date=u.accepted_at?new Date(u.accepted_at).toLocaleString('ar-SA'):'—';
   const ver=u.terms_version||'—';
   return '<article class="terms-admin-row"><div class="terms-admin-avatar">✅</div><div class="terms-admin-main"><b>'+esc(name)+'</b><small>'+(u.username?'@'+esc(u.username)+' • ':'')+'Telegram ID: '+esc(u.telegram_id)+'</small><span class="terms-ok">موافق على الشروط</span><small>النسخة: '+esc(ver)+' • التاريخ: '+esc(date)+'</small></div></article>';
  }).join(''):'<div class="empty-state">لا توجد بيانات مستخدمين حتى الآن.</div>';
 }catch(e){
  const el=document.getElementById('termsAdminList'); if(el)el.innerHTML='<div class="fatal">تعذر تحميل موافقات الشروط: '+esc(e.message)+'</div>';
 }
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
function adminStatCard(key,label,icon,value,clickable=true){
 const action=clickable ? ' onclick='+String.fromCharCode(39)+'openSubscriberStatus("'+key+'")'+String.fromCharCode(39) : '';
 return `<button type="button" class="admin-stat-card ${clickable?'is-clickable':''}"${action}><small>${label}</small><strong>${Number(value||0).toLocaleString('en-US')}</strong><span>${icon}${clickable?' عرض التفاصيل ↗':''}</span></button>`;
}
async function loadAdminStats(){
 const d=await api('/api/admin/overview');
 document.getElementById('adminStats').innerHTML=
  adminStatCard('active','🟢 المفعلين','👥',d.active,true)+
  adminStatCard('expired','🔴 المنتهية','⏳',d.expired,true)+
  '<div class="admin-stat-card"><small>🎁 التجارب</small><strong>'+Number(d.trial_users||0).toLocaleString('en-US')+'</strong><span>المستخدمون الذين بدأوا تجربة</span></div>'+
  '<div class="admin-stat-card"><small>👥 الجدد</small><strong>'+Number(d.new_users||0).toLocaleString('en-US')+'</strong><span>آخر 30 يومًا</span></div>'+
  '<div class="admin-stat-card"><small>💳 المدفوعات</small><strong>'+Number(d.payments||0).toLocaleString('en-US')+'</strong><span>عمليات مسجلة</span></div>'+
  '<div class="admin-stat-card"><small>⭐ Stars</small><strong>'+Number(d.stars||0).toLocaleString('en-US')+'</strong><span>إجمالي مسجل</span></div>';
 const o=d.owner||{};
 const full=[o.first_name,o.last_name].filter(Boolean).join(' ')||'مالك SAS PRO';
 document.getElementById('ownerName').textContent=full;
 document.getElementById('ownerUsername').textContent=o.username?'@'+o.username:'بدون Username';
 document.getElementById('ownerId').textContent='Telegram ID: '+(o.telegram_id||'—');
}
async function openSubscriberStatus(state){
 const panel=document.getElementById('subscriberStatusPanel');
 const title=document.getElementById('subscriberStatusTitle');
 const subtitle=document.getElementById('subscriberStatusSubtitle');
 const summary=document.getElementById('subscriberStatusSummary');
 const list=document.getElementById('subscriberStatusList');
 if(!panel||!list)return;
 const isActive=state==='active';
 title.textContent=isActive?'👥 المشتركين المفعلين':'⏳ المشتركين المنتهية صلاحيتهم';
 subtitle.textContent='بيانات حقيقية مباشرة من قاعدة بيانات SAS PRO';
 summary.innerHTML='<span class="subscriber-loading">جاري قراءة البيانات…</span>';
 list.innerHTML='<div class="loading">جاري تحميل القائمة...</div>';
 panel.hidden=false;
 openAdminAccordionFor('subscriberStatusPanel');
 panel.scrollIntoView({behavior:'smooth',block:'start'});
 try{
  const d=await api('/api/admin/subscribers?state='+encodeURIComponent(state));
  const rows=Array.isArray(d.users)?d.users:[];
  summary.innerHTML='<b>'+Number(d.count||rows.length).toLocaleString('en-US')+'</b><span>'+(isActive?'مستخدم لديه وصول فعال الآن':'مستخدم غير فعال أو منتهي')+'</span>';
  list.innerHTML=rows.length?rows.map(renderSubscriberRow).join(''):'<div class="empty-state">'+(isActive?'لا يوجد مشتركون مفعلون حاليًا.':'لا توجد اشتراكات منتهية حاليًا.')+'</div>';
 }catch(e){
  list.innerHTML='<div class="fatal">تعذر تحميل بيانات المشتركين: '+esc(e.message)+'</div>';
 }
}
function renderSubscriberRow(u){
 const name=[u.first_name,u.last_name].filter(Boolean).join(' ')||'بدون اسم';
 const username=u.username?'@'+u.username:'بدون Username';
 const expires=u.expires_at?new Date(u.expires_at).toLocaleString('ar-SA'):'بدون انتهاء';
 const plan=u.plan||'—';
 return '<article class="subscriber-real-row">'+
  '<div class="subscriber-real-avatar">'+(u.source==='subscription'?'💎':u.source==='trial'?'🎁':u.source==='free'?'♾️':'⏳')+'</div>'+
  '<div class="subscriber-real-main"><b>'+esc(name)+'</b><small>'+esc(username)+' • Telegram ID: '+esc(u.telegram_id)+'</small><span>'+esc(u.status_label)+' • الباقة: '+esc(plan)+'</span><small>الانتهاء: '+esc(expires)+'</small></div>'+
  '<button class="subscriber-real-action" onclick="openSubscriberActions('+Number(u.telegram_id)+')">إدارة</button>'+
 '</article>';
}
function closeSubscriberStatus(){
 const panel=document.getElementById('subscriberStatusPanel');
 if(panel)panel.hidden=true;
}
function openSubscriberActions(id){
 const input=document.getElementById('adminSearch');
 if(input){input.value=String(id);input.scrollIntoView({behavior:'smooth',block:'center'});}
 closeSubscriberStatus();
 setTimeout(()=>adminSearch(),250);
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
