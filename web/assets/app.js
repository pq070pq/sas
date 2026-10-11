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

function showAccountLoadError(message){
 const name=document.getElementById('userName');
 const error=document.getElementById('accountLoadError');
 const text=document.getElementById('accountLoadErrorText');
 const grid=document.getElementById('subscriptionStatusGrid');
 if(name){name.textContent='تعذر التحقق';name.classList.remove('account-loading');}
 if(text)text.textContent=String(message||'تعذر تحميل بيانات الحساب.').slice(0,240);
 if(error)error.hidden=false;
 if(grid)grid.setAttribute('aria-busy','false');
 const trial=document.getElementById('trialBtn');if(trial)trial.disabled=true;
}
async function retryAccountLoad(){
 if(loadStarted)return;
 const name=document.getElementById('userName');
 const error=document.getElementById('accountLoadError');
 if(name){name.innerHTML='<i aria-hidden="true"></i> جارٍ التحقق من حسابك';name.classList.add('account-loading');}
 if(error)error.hidden=true;
 const grid=document.getElementById('subscriptionStatusGrid');if(grid)grid.setAttribute('aria-busy','true');
 const trial=document.getElementById('trialBtn');if(trial)trial.disabled=true;
 await load();
}

function showSASWelcome(){
 const overlay=document.getElementById('sasWelcomeOverlay');
 if(!overlay||window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
 overlay.hidden=false;
 overlay.classList.remove('is-leaving');
 // Confetti/firework particles are decorative only; the greeting dismisses automatically.
 const field=overlay.querySelector('.sas-fireworks');
 if(field){
  field.replaceChildren();
  const colors=['#ff4d6d','#ffd166','#4de4f0','#a78bfa','#ffffff'];
  for(let i=0;i<44;i++){
   const p=document.createElement('i');
   p.className='sas-firework-particle';
   p.style.setProperty('--x',(Math.random()*100)+'%');
   p.style.setProperty('--y',(Math.random()*75)+'%');
   p.style.setProperty('--delay',(Math.random()*.8)+'s');
   p.style.setProperty('--duration',(1.4+Math.random()*1.2)+'s');
   p.style.setProperty('--turn',(Math.random()*720-360)+'deg');
   p.style.setProperty('--particle-color',colors[i%colors.length]);
   field.appendChild(p);
  }
 }
 requestAnimationFrame(()=>overlay.classList.add('is-visible'));
 window.setTimeout(()=>{
  overlay.classList.add('is-leaving');
  window.setTimeout(()=>{overlay.hidden=true;overlay.classList.remove('is-visible','is-leaving');},500);
 },2600);
}

async function load(){
 if(loadStarted)return;
 loadStarted=true;
 const watchdog=setTimeout(()=>{
  if(!loadStarted)return;
  showAccountLoadError('تأخر رد الخادم أثناء التحقق. تحقق من الاتصال ثم أعد المحاولة.');
 },12000);
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
   await adminRefresh();showSASWelcome();return;
  }
  if(me.pro){
   document.getElementById('subscriptionPage').hidden=true;
   document.getElementById('terminalPage').hidden=false;
   await initTerminal();
   showSASWelcome();
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
  const msg=e?.message||'تعذر التحقق من Telegram.';
  showAccountLoadError(msg);
  loadStarted=false;
 }
}

const terminalState={ticker:[],radar:[],perpAssets:{},watch:JSON.parse(localStorage.getItem('saspro_watchlist')||'[]'),timer:null,tab:'dashboard',refreshing:false,radarScanning:false};
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
 document.querySelectorAll('.terminal-tabs button').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
 document.querySelectorAll('.sas-mobile-dock button').forEach(x=>x.classList.toggle('is-active',x.getAttribute('onclick')?.includes("'"+tab+"'")));
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
 const results=await Promise.allSettled([
  api('/api/market/radar-status'),
  api('/api/dashboard/home'),
  api('/api/market/ticker'),
  api('/api/market/movers')
 ]);
 const status=results[0]?.status==='fulfilled'?results[0].value:null;
 const home=results[1]?.status==='fulfilled'?results[1].value:null;
 const ticker=results[2]?.status==='fulfilled'?results[2].value:null;
 const movers=results[3]?.status==='fulfilled'?results[3].value:null;
 if(movers) renderPerpEquitiesMovers(movers);
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
 const statusText=document.getElementById('radarStatusText')?.textContent||'';
 const strip=document.querySelector('.sas-market-strip');
 const headline=document.getElementById('sasMarketHeadline');
 const subline=document.getElementById('sasMarketSubline');
 const sessionLabelEl=document.getElementById('sasSessionLabel');
 const marketClockEl=document.getElementById('sasMarketClock');
 const clockZone=window.__sasClockZone||'Asia/Riyadh';
 const clockLocaleTime=(zone)=>new Date().toLocaleTimeString('en-GB',{timeZone:zone,hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
 if(marketClockEl)marketClockEl.textContent=clockLocaleTime(clockZone);
 if(headline)headline.textContent=session==='regular'?'السوق الأمريكي مفتوح':premarket?'السوق قبل الافتتاح':session==='afterhours'?'التداول بعد الإغلاق':session==='night'?'التداول الليلي':nightPending?'التداول الليلي يفتح قريبًا':'السوق الأمريكي مغلق';
 if(subline)subline.textContent=statusText.replace(/^[🟢🟡🟠🟣🌙🔴 ]+/,'').slice(0,105)||'توقيت الرياض • حالة مباشرة من النظام';
 if(sessionLabelEl)sessionLabelEl.textContent=sessionLabel|| (preopen?'مغلق':'جارٍ التحقق');
 const preEl=document.getElementById('sasPremarketTime'),regularEl=document.getElementById('sasRegularTime'),afterEl=document.getElementById('sasAfterHoursTime');
 if(preEl)preEl.textContent=nextPremarket;
 if(regularEl)regularEl.textContent=regularOpen+' – '+regularClose;
 if(afterEl)afterEl.textContent='حتى '+afterClose;
 if(strip){strip.classList.remove('is-open','is-pre','is-closed');strip.classList.add(session==='regular'?'is-open':premarket?'is-pre':'is-closed');}
 const dock=document.querySelector('.sas-mobile-dock');
 if(dock){dock.querySelectorAll('button').forEach(b=>b.classList.remove('is-active'));const active=dock.querySelector('button[onclick*="'+(terminalState.tab||'dashboard')+'"]');if(active)active.classList.add('is-active');}
 const metricDate=r.last_signal_at?new Date(r.last_signal_at):null;
 const metricDateValid=metricDate&&Number.isFinite(metricDate.getTime());
 const metricDateLabel=metricDateValid?new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(metricDate):'لا توجد إشارة مسجلة';
 const metricTimeLabel=metricDateValid?new Intl.DateTimeFormat('ar-SA-u-ca-gregory',{hour:'2-digit',minute:'2-digit',hour12:true}).format(metricDate):'بانتظار أول إشارة';
 const metricItems=[
  {id:'opportunities',icon:'<svg viewBox="0 0 24 24" fill="none"><path d="M3 17l6-6 4 3 8-9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 5h6v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',label:historical?'فرص آخر جلسة':'فرص اليوم',value:Number(r.opportunities||terminalState.radar.length||0).toLocaleString('ar-SA'),detail:historical?'عدد الفرص التي رصدها النظام في آخر جلسة تداول مسجلة.':'عدد الفرص التي رصدها النظام خلال جلسة اليوم.'},
  {id:'move',icon:'<svg viewBox="0 0 24 24" fill="none"><path d="M4 16l6-6 4 4 6-8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 6h6v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',label:'أعلى ارتفاع مسجل',value:pct(r.top_move_pct),detail:'أكبر نسبة ارتفاع مسجلة ضمن نتائج الرادار للفترة المعروضة؛ ليست ضمانًا لاستمرار الحركة.'},
  {id:'volume',icon:'<svg viewBox="0 0 24 24" fill="none"><path d="M4 19V9m5 10V5m5 14v-7m5 7V3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',label:'أعلى حجم تداول',value:r.top_volume?Number(r.top_volume).toLocaleString('en-US'):'—',detail:'أكبر حجم تداول مسجل في النتائج المتاحة. الحجم هو عدد الأسهم المتداولة، وليس قيمة التداول بالدولار.'},
  {id:'signal',icon:'<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v5l3 2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',label:'وقت آخر إشارة',value:metricDateValid?'<span class="metric-date" dir="rtl"><bdi dir="rtl">'+metricDateLabel+'</bdi></span><span class="metric-time" dir="ltr"><bdi dir="ltr">'+metricTimeLabel+'</bdi></span>':'<span class="metric-date">لا توجد إشارة</span><span class="metric-time">بانتظار البيانات</span>',detail:metricDateValid?'تاريخ ووقت آخر إشارة سجلها الرادار حسب التوقيت المحلي للجهاز.':'لم يسجل النظام وقت إشارة صالحًا حتى الآن.'}
 ];
 document.getElementById('dashboardMetrics').innerHTML=metricItems.map(m=>'<button type="button" class="sas-metric-card" data-metric="'+m.id+'" aria-expanded="false" onclick="toggleSasMetric(\''+m.id+'\')"><span class="sas-metric-top"><span class="sas-metric-icon">'+m.icon+'</span><span class="sas-metric-label">'+m.label+'</span><span class="sas-metric-chevron">⌄</span></span><strong class="sas-metric-value">'+m.value+'</strong><span class="sas-metric-hint">اضغط للتوضيح</span></button>').join('')+'<div id="sasMetricDetail" class="sas-metric-detail" hidden aria-live="polite"></div>';
 window.sasMetricDetails=Object.fromEntries(metricItems.map(m=>[m.id,{label:m.label,detail:m.detail}]));
 const movers=document.getElementById('liveMovers');
 if(movers){
  const ranked=[...terminalState.radar].map(x=>({...x,_move:Number(x.change_pct??x.change??x.change_percent??x.day_change_pct)})).filter(x=>Number.isFinite(x._move)).sort((a,b)=>b._move-a._move).slice(0,5);
  const maxMove=Math.max(1,...ranked.map(x=>Math.abs(x._move)));
  movers.innerHTML=ranked.length?ranked.map(x=>'<div class="sas-mover-row"><span class="sas-mover-symbol">'+escHtml(x.symbol||x.ticker||'—')+'</span><span class="sas-mover-track"><i style="width:'+Math.max(4,Math.min(100,Math.abs(x._move)/maxMove*100))+'%"></i></span><strong class="sas-mover-value" style="color:'+(x._move>=0?'#00f5a0':'#ff6078')+'">'+(x._move>0?'+':'')+x._move.toFixed(2)+'%</strong></div>').join(''):'<div class="empty-state">لا توجد بيانات حركة قابلة للعرض ضمن نتائج الرادار الحالية.</div>';
 }
 const count=document.getElementById('sasRadarCount');if(count)count.textContent=Number(terminalState.radar.length||0).toLocaleString('ar-SA')+' فرصة';
 const el=document.getElementById('dashboardRadar');
 if(terminalState.radar.length) renderCards(el,terminalState.radar.slice(0,6));
 else el.innerHTML='<div class="empty-state">لا توجد إشارات محفوظة من آخر جلسة.</div>';
}

function toggleSasMetric(id){
 const panel=document.getElementById('sasMetricDetail');
 if(!panel||!window.sasMetricDetails||!window.sasMetricDetails[id])return;
 const wasOpen=panel.dataset.active===id&&!panel.hidden;
 document.querySelectorAll('.sas-metric-card').forEach(b=>{const active=b.dataset.metric===id&&!wasOpen;b.classList.toggle('is-active',active);b.setAttribute('aria-expanded',String(active));});
 if(wasOpen){panel.hidden=true;panel.dataset.active='';panel.innerHTML='';return;}
 const item=window.sasMetricDetails[id];panel.dataset.active=id;panel.innerHTML='<span class="sas-metric-detail-icon">ⓘ</span><div><strong>'+item.label+'</strong><p>'+item.detail+'</p></div><button type="button" aria-label="إغلاق التوضيح" onclick="toggleSasMetric(\''+id+'\')">×</button>';panel.hidden=false;
}

function renderPerpEquitiesMovers(data){
 const sourceLink=document.querySelector('.sas-movers-source');
 if(sourceLink){const adminCanSeeSource=Boolean(me?.admin);sourceLink.hidden=!adminCanSeeSource;sourceLink.setAttribute('aria-hidden',String(!adminCanSeeSource));sourceLink.classList.toggle('admin-source-visible',adminCanSeeSource);}
 const updated=document.getElementById('perpMoversUpdated'),state=document.getElementById('perpMoversState');
 const gainers=document.getElementById('perpMoversGainers'),losers=document.getElementById('perpMoversLosers');
 const gainersCount=document.getElementById('perpMoversGainersCount'),losersCount=document.getElementById('perpMoversLosersCount');
 if(!gainers||!losers||!data)return;
 const assets=Array.isArray(data.assets)?data.assets:[];
 terminalState.perpAssets=Object.fromEntries(assets.map(x=>[String(x?.symbol||'').toUpperCase(),x]));
 const fmtUsd=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4}):'—';
 const fmtPct=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?(Number(v)>0?'+':'')+(Number(v)*100).toFixed(2)+'%':'—';
 if(!data.available){
  if(gainersCount)gainersCount.textContent='0 سهم';if(losersCount)losersCount.textContent='0 سهم';
  const empty=escHtml(data.message_ar||'لا توجد بيانات أسعار ضمن النطاق المحدد حاليًا.');
  gainers.innerHTML='<div class="sas-perp-movers-empty">'+empty+'</div>';losers.innerHTML='<div class="sas-perp-movers-empty">'+empty+'</div>';
  if(updated)updated.textContent='بانتظار تحديث الأسعار';
  if(state)state.textContent=data.stale?'آخر بيانات محفوظة • تحديث متأخر':'نطاق السعر $0.50–$15.00';
  return;
 }
 const makeRows=(items,direction,emptyLabel)=>items.length?items.map((item,index)=>{
  const value=Number(item.change_pct),change=(value>0?'+':'')+value.toFixed(2)+'%';
  const symbol=escHtml(String(item.symbol||'—')),name=escHtml(String(item.name||'اسم الشركة غير متوفر'));
  const price=fmtUsd(item.price),change7d=fmtPct(item.change7d);
  const volume=fmtUsd(item.qualityFilteredVolume24hUsd??item.volume24hUsd),oi=fmtUsd(item.openInterestUsd);
  const marketCount=escHtml(String(item.liveMarkets??item.venueCount??'—'));
  const venues=Array.isArray(item.venues)?item.venues:[];
  const venueRows=venues.length?venues.map(v=>'<div class="sas-perp-detail-venue"><span>'+escHtml(String(v.venue||v.venueName||'منصة'))+'</span><span>'+fmtUsd(v.price??v.mark)+'</span><span>حجم '+fmtUsd(v.volume24hUsd)+'</span></div>').join(''):'<div class="sas-perp-detail-muted">تفاصيل المنصات غير متاحة</div>';
  return '<details class="sas-perp-mover-card '+direction+'"><summary class="sas-perp-mover"><span class="sas-perp-mover-rank">'+(index+1)+'</span><span class="sas-perp-mover-main"><strong class="sas-perp-mover-symbol">'+symbol+'</strong><small>'+name+'</small></span><span class="sas-perp-mover-price">'+price+'</span><b>'+change+'</b><span class="sas-perp-mover-open">⌄</span></summary><div class="sas-perp-mover-details"><div class="sas-perp-detail-grid"><div><small>السعر المرجعي</small><b>'+price+'</b></div><div><small>التغير 24 ساعة</small><b>'+change+'</b></div><div><small>التغير 7 أيام</small><b>'+change7d+'</b></div><div><small>حجم التداول 24 ساعة</small><b>'+volume+'</b></div><div><small>القيمة المفتوحة</small><b>'+oi+'</b></div><div><small>الأسواق النشطة</small><b>'+marketCount+'</b></div></div><div class="sas-perp-detail-venues"><b>المنصات المتاحة</b>'+venueRows+'</div></div></details>';
 }).join(''):'<div class="sas-perp-movers-empty">'+emptyLabel+'</div>';
 const gainersList=Array.isArray(data.gainers)?data.gainers:[],losersList=Array.isArray(data.losers)?data.losers:[];
 gainers.innerHTML=makeRows(gainersList,'is-up','لا توجد أسهم رابحة ضمن نطاق السعر');
 losers.innerHTML=makeRows(losersList,'is-down','لا توجد أسهم خاسرة ضمن نطاق السعر');
 if(gainersCount)gainersCount.textContent=gainersList.length.toLocaleString('ar-SA-u-nu-latn')+' سهم';
 if(losersCount)losersCount.textContent=losersList.length.toLocaleString('ar-SA-u-nu-latn')+' سهم';
 if(updated&&data.updated_at){const date=new Date(data.updated_at);updated.textContent='آخر تحديث للبيانات: '+(Number.isFinite(date.getTime())?new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{timeZone:'Asia/Riyadh',hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit',hour12:true}).format(date)+' بتوقيت الرياض':'—');}
 else if(updated)updated.textContent='وقت التحديث غير متاح';
 if(state)state.textContent=data.stale?'آخر بيانات محفوظة • تحديث متأخر':'تحديث تلقائي كل 5 دقائق • السعر المرجعي $0.50–$15.00';
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
  const labelKey=String(x.label||'').toLowerCase();
  const iconSlot=labelKey==='btc'?'icon-bitcoin':labelKey==='gold'?'icon-gold':null;
  const iconFallback=labelKey==='btc'?'₿':labelKey==='gold'?'🪙':'';
  const iconUrl=iconSlot?terminalState.siteMedia?.[iconSlot]?.url:null;
  const iconHtml=iconSlot?'<span class="macro-card-icon" aria-hidden="true">'+(iconUrl?'<img src="'+esc(iconUrl)+'" alt="">':iconFallback)+'</span>':'';
  return '<div class="macro-card '+(valid?'':'macro-unavailable')+'"'+(iconSlot?' data-macro-label="'+labelKey+'"':'')+'>'+iconHtml+'<span>'+escHtml(x.label)+'</span><b>'+ (valid?money(x.price):'غير متاح') +'</b><em class="'+(Number(x.change_pct)>=0?'up':'down')+'">'+pct(x.change_pct)+'</em><small class="macro-source">'+escHtml(x.source||'غير متوفر')+'</small>'+(x.diagnostic?'<small class="macro-diagnostic">'+escHtml(x.diagnostic)+'</small>':'')+'</div>';
 }).join('');
 document.getElementById('macroGrid').innerHTML=cards || '<div class="empty-state">لا تتوفر بيانات السوق حاليًا.</div>';
 const ticker=document.getElementById('marketTicker');
 if(ticker){
   const tickerItems=rows.map(x=>{
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
   }).join('');
   // Duplicate the ticker run to keep the ribbon continuously filled while scrolling.
   ticker.innerHTML=tickerItems
     ? '<div class="ticker-run">'+tickerItems+'</div><div class="ticker-run" aria-hidden="true">'+tickerItems+'</div>'
     : '<div class="empty-state">لا تتوفر أسعار السوق حاليًا.</div>';
   ticker.classList.toggle('ticker-empty',!tickerItems);
 }
}
async function runRadar(show=true){
 if(terminalState.radarScanning)return;
 terminalState.radarScanning=true;
 document.querySelectorAll('.sas-radar-showcase,.sas-radar-visual,.terminal-page').forEach(el=>el.classList.add('sas-radar-scanning'));
 const button=document.getElementById('radarScanBtn');
 const showcase=document.querySelector('.sas-radar-showcase');
 const copyText=showcase?.querySelector('.sas-radar-copy p');
 const statusText=showcase?.querySelector('.sas-radar-status span');
 const scanButtons=[...document.querySelectorAll('.sas-radar-scan-btn')];
 const radarVisual=showcase?.querySelector('.sas-radar-visual');
 const grid=document.getElementById('radarGrid');
 const dashboardGrid=document.getElementById('dashboardRadar');
 const startedAt=Date.now();
 let elapsedTimer=null;
 let progress=null;
 const initialCopy=copyText?.textContent||'يمسح النتائج الفعلية ويعرض الفرص المكتشفة أسفل الرادار.';
 try{
  if(show)switchTerminalTab('radar');
  const existing=terminalState.radar.length;
  if(button){button.disabled=true;button.textContent='⏳ جارٍ الفحص…';}
  scanButtons.forEach(b=>{b.disabled=true;b.dataset.originalText=b.textContent;b.textContent='⏳ جارٍ المسح…';});
  if(radarVisual)radarVisual.disabled=true;
  if(copyText)copyText.textContent='سيتم المسح الآن… نبحث عن الأسهم النشطة ونراجع الحركة والسيولة والشروط الفنية قبل عرض النتائج.';
  if(statusText)statusText.textContent='المسح جارٍ';
  progress=document.createElement('div');
  progress.id='radarScanProgress';
  progress.className='radar-scan-progress';
  progress.setAttribute('role','status');
  progress.setAttribute('aria-live','polite');
  progress.innerHTML='<b>⟳ جارٍ مسح الأسهم الآن</b><div class="radar-scan-progress-track"><i></i></div><small>نراجع الحركة والسيولة والشروط الفنية. لن تظهر إلا الأسهم التي اجتازت شروط الرادار.</small><span class="radar-scan-stage">نبحث عن الأسهم النشطة…</span><span class="radar-scan-elapsed">⏱️ 0 ث</span>';
  if(show){
   const diagnostics=document.getElementById('radarDiagnostics');
   if(diagnostics)diagnostics.prepend(progress);
   if(!existing&&grid)grid.innerHTML='<div class="empty-state">نراجع الأسهم الآن… ستظهر هنا فقط النتائج التي تجتاز شروط الرادار.</div>';
  }else if(showcase){
   const actions=showcase.querySelector('.sas-radar-actions');
   if(actions)actions.before(progress);
   else showcase.append(progress);
  }
  const stages=['نبحث عن الأسهم النشطة…','نراجع الحركة والسيولة…','نتحقق من الشروط الفنية…','نرتب النتائج التي اجتازت الفحص…'];
  elapsedTimer=setInterval(()=>{
   const elapsed=Math.floor((Date.now()-startedAt)/1000);
   const label=progress?.querySelector('.radar-scan-elapsed');
   const stage=progress?.querySelector('.radar-scan-stage');
   if(label)label.textContent='⏱️ '+elapsed+' ث · نكمل مراجعة الفرص';
   if(stage)stage.textContent=stages[Math.floor(elapsed/5)%stages.length];
  },1000);
  // Start a real fresh scan, retaining previous cards until the new results return.
  const d=await api('/api/radar/scan?fresh=1',{timeoutMs:180000,retries:0});
  if(d?.error){
    const reason=String(d.error.message||d.error.type||'خطأ غير معروف');
    if(show&&grid)grid.innerHTML='<div class="fatal"><b>🔴 تعذر إكمال مسح الأسهم</b><br><small>'+escHtml(reason)+'</small><br><small>تم الاحتفاظ بآخر نتائج الرصد إن وُجدت.</small></div>';
    if(copyText)copyText.textContent='تعذر إكمال المسح. يمكنك إعادة المحاولة؛ احتفظنا بالنتائج السابقة.';
    if(statusText)statusText.textContent='تعذر المسح';
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
  const diagnosticsEl=document.getElementById('radarDiagnostics');
  if(diagnosticsEl)diagnosticsEl.innerHTML='<b class="radar-mode">'+mode+'</b><span>🕒 وقت الرصد: '+escHtml(scanLabel)+'</span><span>مرشحون '+Number(diag.candidates||0)+'</span><span>اجتازوا '+Number(diag.passed||0)+'</span><span>مستبعدون '+Number(diag.filtered||0)+'</span><span>أخطاء '+Number(diag.errors||0)+'</span>';
  const candidateEl=document.getElementById('radarCandidates');
  const candidates=Array.isArray(diag.filtered_examples)?diag.filtered_examples:[];
  if(candidateEl && candidates.length){
    candidateEl.hidden=false;
    candidateEl.innerHTML='<div class="candidate-title"><b>🔎 تحليل المرشحين</b><small>هذه الأسهم اجتازت مرحلة البحث الأولي ولم تدخل الإشارة النهائية. سبب الاستبعاد ظاهر لكل سهم.</small></div>'+
      '<div class="candidate-grid">'+candidates.slice(0,12).map(x=>{
        const sym=String(x.symbol||'—');
        return '<article class="candidate-card"><div><b>'+escHtml(sym)+'</b><small>'+escHtml(x.data_source||'مصدر الرصد')+'</small></div><p>'+escHtml(x.reason||'تم استبعاده في مرحلة لاحقة')+'</p><button type="button" data-symbol="'+escHtml(sym)+'" onclick="openSymbol(this.dataset.symbol)">⏳ تحليل</button></article>';
      }).join('')+'</div>';
  }else if(candidateEl){
    candidateEl.hidden=true;
    candidateEl.innerHTML='';
  }
  renderRadar();
  renderDashboard({radar:{enabled:d.enabled,opportunities:terminalState.radar.length,top_move_pct:Math.max(...terminalState.radar.map(x=>Number(x.change_pct)||-Infinity)),top_volume:Math.max(...terminalState.radar.map(x=>Number(x.volume)||-Infinity))}});
  const found=terminalState.radar.length;
  if(copyText)copyText.textContent=found
   ? 'اكتمل المسح بنجاح — تم العثور على '+found.toLocaleString('ar-SA')+' فرصة مطابقة للشروط، وتظهر الأسهم أدناه.'
   : 'اكتمل المسح دون فرص مستوفية للشروط حاليًا. لا نعرض أسهمًا غير مؤكدة؛ يمكنك إعادة المسح لاحقًا.';
  if(statusText)statusText.textContent=found?'اكتمل المسح':'اكتمل المسح • لا توجد فرص';
  if(!show&&dashboardGrid){
   dashboardGrid.setAttribute('aria-live','polite');
   dashboardGrid.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
  }
 }catch(e){
   const msg=String(e?.message||'');
   if(msg.includes('الموافقة على الشروط')){
    openTerms('trial');
    return;
   }
   if(show&&grid)grid.innerHTML='<div class="fatal"><b>تعذر إكمال مسح الأسهم</b><br>'+escHtml(msg||'حدث خطأ غير معروف')+'<br><small>يمكنك إعادة المحاولة؛ لم تُحذف نتائج الرصد السابقة من قاعدة البيانات.</small></div>';
   if(copyText)copyText.textContent='حدث خطأ أثناء المسح. احتفظنا بالنتائج السابقة؛ حاول مرة أخرى.';
   if(statusText)statusText.textContent='حدث خطأ في المسح';
 }finally{
   if(elapsedTimer)clearInterval(elapsedTimer);
   progress?.remove();
   if(button){button.disabled=false;button.textContent='🔎 مسح الأسهم';}
   scanButtons.forEach(b=>{b.disabled=false;b.textContent=b.dataset.originalText||'⟳ ابدأ مسحًا جديدًا';delete b.dataset.originalText;});
   if(radarVisual)radarVisual.disabled=false;
   terminalState.radarScanning=false;
   document.querySelectorAll('.sas-radar-showcase,.sas-radar-visual,.terminal-page').forEach(el=>el.classList.remove('sas-radar-scanning'));
 }
}
function renderRadar(){renderCards(document.getElementById('radarGrid'),terminalState.radar);}
function renderCards(el,rows){
 if(!rows.length){
  el.innerHTML='<div class="radar-empty-card">'+
   '<div class="radar-empty-orbit" aria-hidden="true"><span></span><i></i><b></b></div>'+
   '<div class="radar-empty-content">'+
    '<span class="radar-empty-kicker">SAS PRO · SMART RADAR</span>'+
    '<h3>لا توجد فرص مكتملة حاليًا</h3>'+
    '<p>الرادار لا يعرض إلا الأسهم التي اجتازت شروط الفحص. إذا كان السوق مغلقًا، فسيعرض آخر الإشارات المحفوظة عند توفرها.</p>'+
    '<div class="radar-empty-status"><span class="radar-empty-dot"></span><span>بانتظار فرصة تستوفي الشروط</span></div>'+
    '<div class="radar-empty-actions"><button type="button" class="radar-empty-primary" onclick="runRadar()">⟳ ابدأ مسحًا جديدًا</button><button type="button" class="radar-empty-secondary" onclick="toggleRadarEmptyHelp(this)" aria-expanded="false">كيف يعمل الرادار؟ <span>⌄</span></button></div>'+
    '<div class="radar-empty-help" hidden>يفحص الرادار حركة السعر والسيولة والزخم والشروط الفنية. عدم ظهور فرص لا يعني وجود خطأ؛ بل يعني أن الأسهم المتاحة لم تحقق شروط الرصد في هذه الدورة.</div>'+
   '</div></div>';
  return;
 }
 el.innerHTML=rows.map(stockCard).join('');
}
function toggleRadarEmptyHelp(button){
 const help=button.closest('.radar-empty-content')?.querySelector('.radar-empty-help');
 if(!help)return;
 const open=help.hidden;
 help.hidden=!open;
 button.setAttribute('aria-expanded',String(open));
 const label=button.querySelector('span');
 if(label)label.textContent=open?'⌃':'⌄';
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
 const perpFromResult=x.perpequities_context&&typeof x.perpequities_context==='object'?{
  symbol:x.perpequities_context.symbol,
  price:x.perpequities_context.reference_price,
  change24h:x.perpequities_context.change_24h_fraction,
  qualityFilteredVolume24hUsd:x.perpequities_context.volume_24h_usd,
  openInterestUsd:x.perpequities_context.open_interest_usd,
  url:x.perpequities_context.source_url||'https://perpequities.com/ar'
 }:null;
 const perp=terminalState.perpAssets[raw.toUpperCase()]||perpFromResult;
 const perpPrice=Number(perp?.price);
 const perpChange=perp?.change24h==null?null:Number(perp.change24h);
 const perpVolume=Number(perp?.qualityFilteredVolume24hUsd??perp?.volume24hUsd);
 const perpOi=Number(perp?.openInterestUsd);
 const perpContext=perp&&Number.isFinite(perpPrice)&&perpPrice>0?[
  '<div class="sas-perp-context"><div class="sas-perp-context-head"><b>↗ PerpEquities</b><span>سعر عقد دائم • ليس سعر السهم الفوري</span></div><div class="sas-perp-context-metrics">',
  '<span>العقد <b dir="ltr">$'+money(perpPrice)+'</b></span>',
  '<span>24 ساعة <b class="'+(perpChange===null?'':(perpChange>=0?'up':'down'))+'" dir="ltr">'+(perpChange===null?'—':pct(perpChange*100))+'</b></span>',
  '<span>حجم العقود <b>$'+(Number.isFinite(perpVolume)&&perpVolume>0?escHtml(perpVolume.toLocaleString('en-US',{notation:'compact',maximumFractionDigits:1})):'—')+'</b></span>',
  '<span>الفائدة المفتوحة <b>$'+(Number.isFinite(perpOi)&&perpOi>0?escHtml(perpOi.toLocaleString('en-US',{notation:'compact',maximumFractionDigits:1})):'—')+'</b></span>',
  '</div>'+(me?.admin ? '<a href="'+escHtml(safeNewsUrl(perp.url)||'https://perpequities.com/ar')+'" target="_blank" rel="noopener noreferrer">تفاصيل العقود والمنصات ↗</a>' : '')+'</div>'
 ].join(''):'';
 return '<article class="stock-card"><div class="stock-head"><div><b>'+s+'</b><small>'+(section==='large'?'سهم كبير / متوسط':'سهم صغير')+(x.created_at?' • '+formatTime(x.created_at):'')+'</small></div><span class="'+(Number(change)>=0?'up':'down')+'">'+pct(change)+'</span></div>'+
 '<strong>$'+money(price)+'</strong>'+
 '<div class="stock-levels"><span>دخول <b>$'+money(price)+'</b></span><span>وقف <b>$'+money(stop)+'</b></span><span>هدف 1 <b>$'+money(target)+'</b></span></div>'+
 '<div class="stock-meta"><span>RVOL '+(Number.isFinite(rvol)&&rvol>0?rvol.toFixed(2):'—')+'×</span><span>حجم '+(Number.isFinite(volume)&&volume>0?volume.toLocaleString('en-US'):'—')+'</span><span>R:R '+(rr!=null?Number(rr).toFixed(2):'—')+(warning?' ⚠️':'')+'</span></div>'+
 '<div class="stock-gates">'+gate(gates.sas_core!==false,'SAS Core')+gate(gates.liquidity!==false,'السيولة')+gate(gates.target!==false,'الهدف')+gate(gates.live_levels!==false,'المستويات')+'</div>'+
 ' <button type="button" class="stock-shariah stock-shariah-action '+shClass+'" onclick="event.stopPropagation();openShariah(\''+raw+'\')"><span class="stock-shariah-icon-label"><span class="stock-shariah-icon" aria-hidden="true">🕌</span><b>الشرعية</b></span><strong>'+(shClass==='sh-unknown'?'اضغط للتحقق':escHtml(shLabel))+'</strong><span class="shariah-action-arrow">‹</span></button>'+

 '<div class="stock-summary">'+(Number.isFinite(score)?'<span>⭐ قوة '+score.toFixed(0)+'/100</span>':'')+'<span>'+catalystLabel+(catalyst>0?' '+catalyst+'/100':'')+'</span>'+(x.live_price_source?'<span>📡 '+escHtml(x.live_price_source)+'</span>':'')+'</div>'+
 perpContext+
 '<div class="stock-ai">'+escHtml(ai.key_takeaway||ai.headline_summary||cls.reason||'تحليل AI يظهر عند فتح التحليل الكامل.')+'</div>'+
 '<div class="stock-actions"><button class="stock-chart-action" onclick="event.stopPropagation();openSymbol(\''+raw+'\',true)">🔍 تحليل السهم</button><button onclick="event.stopPropagation();toggleWatch(\''+raw+'\')">'+(terminalState.watch.includes(raw)?'★ محفوظ':'☆ حفظ')+'</button></div></article>';
}
function toggleWatch(symbol){symbol=symbol.toUpperCase();terminalState.watch=terminalState.watch.includes(symbol)?terminalState.watch.filter(x=>x!==symbol):[...terminalState.watch,symbol];localStorage.setItem('saspro_watchlist',JSON.stringify(terminalState.watch));renderWatchlist();renderRadar();}
async function renderWatchlist(){
 const el=document.getElementById('watchGrid'); if(!terminalState.watch.length){el.innerHTML='<div class="empty-state">أضف الأسهم من الرادار أو صفحة التحليل.</div>';return;}
 el.innerHTML='<div class="loading">جاري تحديث المحفوظة...</div>';
 const rows=await Promise.all(terminalState.watch.slice(0,20).map(async s=>{try{return await api('/api/stocks/'+encodeURIComponent(s)+'/quote');}catch(e){return {symbol:s};}}));
 el.innerHTML=rows.map(x=>stockCard(x)).join('');
}
function openSymbol(symbol,focusChart=false){document.getElementById('symbolSearch').value=symbol;switchTerminalTab('search');analyzeSymbol().then(()=>{if(focusChart){const detail=document.getElementById('symbolResult');const chart=detail?.querySelector('.chart-title')||detail?.querySelector('canvas')||detail?.querySelector('.quote-line');if(chart){chart.scrollIntoView({behavior:'smooth',block:'start'});chart.classList.add('chart-focus');setTimeout(()=>chart.classList.remove('chart-focus'),1800);}}});}
function openShariah(symbol){const input=document.getElementById('symbolSearch');if(input)input.value=String(symbol||'').toUpperCase();switchTerminalTab('search');const result=document.getElementById('shariahSearchResult');if(result)result.hidden=false;searchShariah();setTimeout(()=>{const target=document.getElementById('shariahSearchResult');target?.scrollIntoView({behavior:'smooth',block:'center'});},120);}
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

let stockAnalysisLoaderTimer=null;
let stockAnalysisStartedAt=0;
function stopStockAnalysisLoader(){
 if(stockAnalysisLoaderTimer){clearInterval(stockAnalysisLoaderTimer);stockAnalysisLoaderTimer=null;}
}
function startStockAnalysisLoader(el,symbol){
 stopStockAnalysisLoader();
 stockAnalysisStartedAt=Date.now();
 const stages=[
  {title:'نجهّز بيانات السعر والسيولة',detail:'نجمع أحدث البيانات المتاحة للسهم.'},
  {title:'نفحص الرسم والحركة الفنية',detail:'نراجع الشموع والاتجاه والمستويات.'},
  {title:'نرتّب الأخبار ذات الصلة',detail:'نجهّز الأخبار والملخصات المتاحة.'},
  {title:'نراجع النتائج قبل العرض',detail:'نتأكد من وضوح البيانات وعدم تخمين القيم.'}
 ];
 el.innerHTML='<section class="stock-analysis-loader" role="status" aria-live="polite" aria-label="جاري تحليل السهم">'+
  '<div class="sal-loader-top"><div class="sal-orbit" aria-hidden="true"><span>📡</span></div><div class="sal-loader-title"><span class="eyebrow">SAS PRO • SMART ANALYSIS</span><b>يجري تحليل <strong>'+escHtml(symbol)+'</strong></b><small>لحظات ونرتّب لك الصورة بشكل واضح</small></div></div>'+
  '<div class="sal-progress-head"><span id="salStageTitle">'+stages[0].title+'</span><b id="salProgressPct">08%</b></div>'+
  '<div class="sal-progress-track"><span id="salProgressFill"></span></div>'+
  '<div class="sal-loader-meta"><span id="salStageDetail">'+stages[0].detail+'</span><span class="sal-timer">⏱ <b id="salElapsed">00:00</b></span></div>'+
  '<div class="sal-steps"><span class="sal-step active"><i>1</i> السعر</span><span class="sal-step"><i>2</i> الفني</span><span class="sal-step"><i>3</i> الأخبار</span><span class="sal-step"><i>4</i> النتيجة</span></div>'+
  '<div class="sal-loader-tip"><span>💡</span><span id="salTip">نستخدم البيانات المتاحة فقط؛ لن نخمن سعرًا أو هدفًا غير موثّق.</span></div>'+
  '</section>';
 let tick=0;
 stockAnalysisLoaderTimer=setInterval(()=>{
  tick++;
  const elapsed=Math.floor((Date.now()-stockAnalysisStartedAt)/1000);
  const pct=Math.min(92,8+Math.floor(84*(1-Math.exp(-tick/10))));
  const stageIndex=Math.min(stages.length-1,Math.floor(tick/4));
  const elapsedNode=el.querySelector('#salElapsed');
  const pctNode=el.querySelector('#salProgressPct');
  const fill=el.querySelector('#salProgressFill');
  const title=el.querySelector('#salStageTitle');
  const detail=el.querySelector('#salStageDetail');
  if(!elapsedNode||!pctNode||!fill||!title||!detail){stopStockAnalysisLoader();return;}
  elapsedNode.textContent=String(Math.floor(elapsed/60)).padStart(2,'0')+':'+String(elapsed%60).padStart(2,'0');
  pctNode.textContent=String(pct).padStart(2,'0')+'%';
  fill.style.width=pct+'%';
  title.textContent=stages[stageIndex].title;
  detail.textContent=elapsed>=18?'ما زلنا ننتظر اكتمال البيانات من المصادر…':stages[stageIndex].detail;
  el.querySelectorAll('.sal-step').forEach((node,index)=>node.classList.toggle('active',index<=stageIndex));
  const tip=el.querySelector('#salTip');
  if(tip&&elapsed>=12)tip.textContent='قد يختلف وقت التحليل حسب استجابة مصادر البيانات، ولا يعني الانتظار وجود فرصة تداول.';
 },1000);
}

function mountPerpEquitiesAnalysis(root,data,symbol){
 const host=root?.querySelector('#perpEquitiesAnalysis');if(!host)return;
 const panel=document.createElement('section');panel.className='perp-analysis-panel';
 const heading=document.createElement('div');heading.className='perp-analysis-heading';
 const title=document.createElement('div');title.innerHTML='<span class="eyebrow">PERPEQUITIES · LIVE CONTEXT</span><b>📊 شارت وتحليل السهم من المصدر</b>';heading.appendChild(title);
 if(me?.admin){const link=document.createElement('a');link.className='perp-analysis-open';link.href=safeNewsUrl(data?.chart_url||data?.source_url)||'https://perpequities.com/ar';link.target='_blank';link.rel='noopener noreferrer';link.textContent='فتح الشارت والتحليل الكامل ↗';heading.appendChild(link);}panel.appendChild(heading);
 if(!data?.available){const msg=document.createElement('p');msg.className='perp-analysis-empty';msg.textContent=data?.message_ar||'بيانات المصدر غير متاحة الآن.';panel.appendChild(msg);host.appendChild(panel);return;}
 const money=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v))?'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:4}):'—';
 const pct=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v))?(Number(v)>=0?'+':'')+(Number(v)*100).toFixed(2)+'%':'—';
 const metrics=[['السعر المرجعي للعقد',money(data.reference_price)],['التغير 24 ساعة',pct(data.change_24h_fraction)],['التغير 7 أيام',pct(data.change_7d_fraction)],['حجم العقود 24 ساعة',money(data.volume_24h_usd)],['العقود المفتوحة',money(data.open_interest_usd)],['الأسواق الحية',String(data.fresh_live_markets??data.live_markets??'—')]];
 const grid=document.createElement('div');grid.className='perp-analysis-metrics';metrics.forEach(([label,value])=>{const card=document.createElement('div');card.className='perp-analysis-metric';const small=document.createElement('small');small.textContent=label;const strong=document.createElement('b');strong.textContent=value;card.append(small,strong);grid.appendChild(card);});panel.appendChild(grid);
 const venues=document.createElement('div');venues.className='perp-analysis-venues';const names=Array.isArray(data.venues)?data.venues.slice(0,5).map(v=>v.venue).filter(Boolean).join(' · '):'';const venueLine=document.createElement('small');venueLine.textContent='المنصات: '+(names||'غير متاحة');venues.appendChild(venueLine);const updateLine=document.createElement('small');const time=data.market_data_at?new Date(data.market_data_at):null;updateLine.textContent='آخر بيانات المصدر: '+(time&&Number.isFinite(time.getTime())?new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{timeZone:'Asia/Riyadh',hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit',hour12:true}).format(time)+' بتوقيت الرياض':'غير متاح');venues.appendChild(updateLine);panel.appendChild(venues);
 const embedTitle=document.createElement('div');embedTitle.className='perp-embed-title';embedTitle.innerHTML='<b>الشارت التفاعلي الأصلي</b><span>1D · 7D · 30D · 90D · All</span>';panel.appendChild(embedTitle);
 const frame=document.createElement('div');frame.className='perp-embed-frame';const iframe=document.createElement('iframe');iframe.title='شارت وتحليل '+symbol+' من PerpEquities';iframe.src=link.href;iframe.loading='lazy';iframe.referrerPolicy='strict-origin-when-cross-origin';iframe.allow='fullscreen';frame.appendChild(iframe);panel.appendChild(frame);
 const note=document.createElement('p');note.className='perp-analysis-disclaimer';note.textContent='بيانات العقود الدائمة قد تختلف عن سعر السهم الفوري. مصدر البيانات: PerpEquities. البيانات للمعلومات فقط وليست توصية تداول.';panel.appendChild(note);host.appendChild(panel);
}
async function analyzeSymbol(){
 const input=document.getElementById('symbolSearch');
 const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,'');
 if(!symbol)return;
 const el=document.getElementById('symbolResult');
 startStockAnalysisLoader(el,symbol);
 const [qR,chartR,newsR,analysisR,miniR,shariahR,perpR]=await Promise.allSettled([
   api('/api/stocks/'+encodeURIComponent(symbol)+'/quote'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/chart'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/news'),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/analyze',{method:'POST',timeoutMs:30000}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/mini-analysis',{timeoutMs:30000}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/shariah',{timeoutMs:12000}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/perpequities',{timeoutMs:10000})
 ]);
 stopStockAnalysisLoader();
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
 const perp=perpR.status==='fulfilled'&&perpR.value&&typeof perpR.value==='object'?perpR.value:{symbol,available:false,source_url:'https://perpequities.com/ar',message_ar:'تعذر تحميل بيانات PerpEquities.'};
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
   '<div class="chart-title"><b>📈 شارت السهم</b><div class="chart-title-actions"><small>شموع وحجم تداول • بيانات SAS PRO</small>'+(me?.admin ? '<a class="gocharting-link" href="'+escHtml(safeNewsUrl(perp.chart_url||perp.source_url)||'https://perpequities.com/ar')+'" target="_blank" rel="noopener noreferrer">↗ فتح تحليل PerpEquities</a>' : '')+'</div></div><div class="chart-box">'+(chart.available===false?'<div class="chart-unavailable">📊 البيانات الفنية التاريخية غير متاحة حاليًا<br><small>لم يتم اختلاق هدف أو وقف أو إشارة. سيتم إظهارها عند توفر بيانات الشموع والحجم.</small></div>':'<canvas id="stockCanvas" height="230"></canvas>')+'</div><div id="perpEquitiesAnalysis"></div>'+
   '<div class="level-grid"><div><small>🟦 الدخول</small><b>&#36;'+money(entry)+'</b></div><div><small>🛑 الوقف</small><b>&#36;'+money(stop)+'</b></div><div><small>🎯 الهدف 1</small><b>&#36;'+money(target1)+'</b></div><div><small>⚖️ R:R</small><b>'+rr+'</b></div></div>'+
   '<div class="ai-box"><b>'+(aiAvailable?'⏳ زبدة تحليل AI':'📐 الخلاصة الفنية')+'</b><p>'+escHtml(summary)+'</p>'+(aiAvailable&&ai.provider?'<small>المزود: '+escHtml(ai.provider)+'</small>':'')+'</div>'+fccHtml+
   '<div class="news-list">'+renderNewsItems(newsFromAnalysis,5,Array.isArray(ai.news_summaries)?ai.news_summaries:[])+'</div>'+
   '<div class="terminal-disclaimer">🛡️ AI يفسّر الأدلة فقط ولا يغيّر قرار الرادار أو المستويات.</div>';
 mountPerpEquitiesAnalysis(el,perp,symbol);
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
 const expiry=x.expires_at||x.subscription_expires||x.trial_expires||null;
 const start=source==='trial'?(x.trial_start||null):(x.subscription_start||x.trial_start||null);
 const byId=id=>document.getElementById(id);
 byId('userName').textContent=x.user?.first_name||x.user?.username||'تم التحقق من الحساب';
 byId('userName').classList.remove('account-loading');
 byId('accountLoadError').hidden=true;
 byId('subscriptionStatusGrid').setAttribute('aria-busy','false');
 byId('subStatus').textContent=active?'🟢 اشتراك فعّال':'🔒 غير مشترك';
 byId('subStatus').className=active?'ok':'bad';
 byId('subPlan').textContent=source==='trial'?'🎁 تجربة مجانية':(active?(x.plan||x.user?.plan||'اشتراك فعّال'):'لا توجد باقة فعّالة');
 byId('subStart').textContent=start?fmtDate(start):(source==='trial'?'لم يبدأ التسجيل':'—');
 byId('subEnd').textContent=expiry?fmtDate(expiry):(source==='free'?'مفتوح':'لا يوجد موعد');
 byId('subDays').textContent=expiry?fmtDays(new Date(),expiry):(source==='free'?'مفتوح':'—');
 if(source==='trial'&&expiry){
  byId('trialState').textContent='🎁 فعّالة حتى '+fmtDate(expiry);
 }else if(source==='free'){
  byId('trialState').textContent='صلاحية مجانية مفتوحة';
 }else{
  byId('trialState').textContent=x.trial_available?'متاحة مرة واحدة':(x.trial_expires?'منتهية/مستخدمة':'غير متاحة');
 }
 byId('channelBtn').hidden=!active;
 byId('trialBtn').disabled=!x.trial_available;
}
 
function renderPlans(p){
 const names={monthly:'شهري','3month':'3 أشهر','6month':'6 أشهر',yearly:'سنة'};
 const icons={monthly:'🟢','3month':'🔷','6month':'💎',yearly:'👑'};
 document.getElementById('plans').innerHTML=Object.entries(p).filter(([k,x])=>x.visible!==false).map(([k,x])=>
  '<article class="plan sas-plan-card"><div class="plan-icon">'+(icons[k]||'💠')+'</div><b>'+names[k]+'</b><strong>'+Number(x.sar).toLocaleString('ar-SA')+' ريال سعودي</strong><span>'+Number(x.days).toLocaleString('ar-SA')+' يوم</span>'+
  '<small class="plan-payment-note">دفع آمن عبر Telegram Stars</small>'+
  '<button '+(x.stars>0?'':'disabled')+' onclick="buyPlan(\''+k+'\')">اشترك الآن ←</button></article>'
 ).join('');
}

function openTerms(action){
 termAction=action;
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(agree)agree.checked=false;
 if(button){
  button.disabled=action!=='view';
  button.textContent=action==='view'?'إغلاق':'أوافق على الشروط وأتابع';
 }
 const row=document.getElementById('termsAgreeRow');
 if(row)row.hidden=action==='view';
 document.getElementById('termsText').textContent='جاري تحميل الشروط...';
 document.getElementById('termsModal').hidden=false;
 requestAnimationFrame(()=>{if(agree&&action!=='view')agree.focus();});
 api('/api/subscription/plans').then(d=>{document.getElementById('termsText').textContent=d.terms_text||'';}).catch(e=>{document.getElementById('termsText').textContent=e.message;});
}
function toggleTermsButton(){
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(!button||termAction==='view')return;
 button.disabled=!Boolean(agree?.checked);
 button.textContent='أوافق على الشروط وأتابع';
}
function closeTerms(){document.getElementById('termsModal').hidden=true;termAction=null;}
async function continueTerms(){
 const agree=document.getElementById('termsAgree');
 const button=document.getElementById('termsContinue');
 if(termAction==='view'){closeTerms();return;}
 // لا نسجل الموافقة ولا نفعّل التجربة قبل تحديد مربع الموافقة صراحة.
 if(!agree?.checked){
  const row=document.getElementById('termsAgreeRow');
  if(row){row.style.borderColor='rgba(255,83,104,.75)';row.scrollIntoView({behavior:'smooth',block:'nearest'});}
  return;
 }
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

const SITE_MEDIA_MANAGED_ITEMS=[
 {slot:'icon-radar',label:'أيقونة زر الرادار',fallback:'📡',target:'.radar-icon'},
 {slot:'icon-channel',label:'أيقونة زر القناة',fallback:'✈️',target:'.channel-icon'},
 {slot:'icon-home',label:'أيقونة تبويب الرئيسية',fallback:'⌂',target:'.terminal-tabs button[data-tab="dashboard"]'},
 {slot:'icon-tab-radar',label:'أيقونة تبويب الرادار',fallback:'📡',target:'.terminal-tabs button[data-tab="radar"]'},
 {slot:'icon-search',label:'أيقونة تبويب البحث',fallback:'🔎',target:'.terminal-tabs button[data-tab="search"]'},
 {slot:'icon-watch',label:'أيقونة تبويب المحفوظات',fallback:'★',target:'.terminal-tabs button[data-tab="watch"]'},
 {slot:'icon-account',label:'أيقونة تبويب الحساب',fallback:'👤',target:'.terminal-tabs button[data-tab="account"]'},





 {slot:'icon-live-movers',label:'أيقونة بطاقة أعلى الأسهم حركة',fallback:'📈',target:'.sas-insight-grid .sas-insight-panel .sas-insight-title > span'},
 {slot:'icon-perp-movers',label:'أيقونة بطاقة الأكثر تحركًا خلال 24 ساعة',fallback:'↕️',target:'.sas-perp-movers-panel .sas-insight-title > span'},
 {slot:'icon-bitcoin',label:'أيقونة البتكوين — مؤشرات السوق',fallback:'₿',target:'.macro-card[data-macro-label="btc"] .macro-card-icon'},
 {slot:'icon-gold',label:'أيقونة الذهب — مؤشرات السوق',fallback:'🪙',target:'.macro-card[data-macro-label="gold"] .macro-card-icon'},
 {slot:'icon-market-indicators',label:'أيقونة عنوان مؤشرات السوق',fallback:'📊',target:'.section-title .market-indicators-icon'},
 {slot:'icon-shariah',label:'أيقونة الشرعية في بطاقة السهم',fallback:'🕌',target:'.stock-shariah-icon'},
 {slot:'icon-disclaimer-alert',label:'أيقونة التنبيه القانوني أسفل الصفحة',fallback:'⛔',target:'.sas-disclaimer-bar .disclaimer-badge .disclaimer-icon'}
];
const SITE_MEDIA_ICON_TARGETS=Object.fromEntries(SITE_MEDIA_MANAGED_ITEMS.map(item=>[item.slot,item.target]));
function renderMediaAdminCards(items={}){
 const grid=document.getElementById('mediaIconsAdminGrid');
 if(!grid)return;
 grid.innerHTML=SITE_MEDIA_MANAGED_ITEMS.map(item=>{
  const current=items[item.slot]?.url;
  const slot=esc(item.slot),label=esc(item.label),fallback=esc(item.fallback);
  return '<article class="media-admin-card" data-media-card="'+slot+'">'+
   '<div class="media-admin-preview"><img id="mediaPreview-'+slot+'" alt="معاينة '+label+'" '+(current?'':'hidden')+(current?' src="'+esc(current)+'"':'')+'><span id="mediaEmpty-'+slot+'" '+(current?'hidden':'')+'>'+fallback+'</span></div>'+
   '<b>'+label+'</b><small>تتغير مباشرة في الواجهة</small>'+
   '<label class="media-upload-btn">رفع أيقونة<input type="file" accept="image/webp,image/png,image/jpeg" onchange="uploadSiteMedia(\''+slot+'\',this)"></label>'+
   '<button type="button" class="media-delete-btn" onclick="deleteSiteMedia(\''+slot+'\')">🗑️ حذف والعودة للأصل</button>'+
  '</article>';
 }).join('');
}
async function loadSiteMedia(){
 const data=await api('/api/site-media');
 const items=data?.items||{};
 terminalState.siteMedia=items;
 const hero=document.querySelector('.terminal-hero-banner > img');
 if(hero){
  if(!hero.dataset.mediaOriginalSrc)hero.dataset.mediaOriginalSrc=hero.getAttribute('src')||'/assets/saspro-banner.webp';
  hero.src=items.hero?.url||hero.dataset.mediaOriginalSrc;
 }
 for(const [slot,selector] of Object.entries(SITE_MEDIA_ICON_TARGETS)){
  const el=document.querySelector(selector);
  if(!el)continue;
  if(!el.dataset.mediaOriginalHTML)el.dataset.mediaOriginalHTML=el.innerHTML;
  const item=items[slot];
  if(!item?.url){
   el.innerHTML=el.dataset.mediaOriginalHTML;
   el.classList.remove('has-managed-icon');
   continue;
  }
  if(el.matches('button')){
   if(!el.dataset.mediaOriginalLabel)el.dataset.mediaOriginalLabel=el.textContent.trim();
   const label=el.dataset.mediaOriginalLabel.replace(/^[^\p{L}\p{N}]+/u,'').trim();
   el.innerHTML='<img class="sas-managed-tab-icon" src="'+esc(item.url)+'" alt="" aria-hidden="true"><span>'+esc(label)+'</span>';
  }else{
   el.innerHTML='<img class="sas-managed-icon-image" src="'+esc(item.url)+'" alt="" aria-hidden="true">';
  }
  el.classList.add('has-managed-icon');
 }
 return items;
}
async function loadAdminMedia(){
 const status=document.getElementById('mediaUploadStatus');
 try{
  const items=await loadSiteMedia();
  const hero=items.hero?.url;
  const heroPreview=document.getElementById('mediaPreview-hero');
  const heroEmpty=document.getElementById('mediaEmpty-hero');
  if(heroPreview){heroPreview.hidden=!hero;if(hero)heroPreview.src=hero;}
  if(heroEmpty)heroEmpty.hidden=!!hero;
  renderMediaAdminCards(items);
  if(status)status.textContent='تم تحديث معاينات الصور والأيقونات ('+SITE_MEDIA_MANAGED_ITEMS.length+' موضعًا).';
 }catch(e){if(status)status.textContent='تعذر تحميل الصور: '+(e.message||'خطأ غير معروف');}
}
async function deleteSiteMedia(slot){
 const label=slot==='hero'?'صورة الواجهة الرئيسية':(SITE_MEDIA_MANAGED_ITEMS.find(item=>item.slot===slot)?.label||'الأيقونة');
 if(!confirm('هل تريد حذف '+label+' والعودة إلى الشكل الافتراضي؟'))return;
 const status=document.getElementById('mediaUploadStatus');
 const buttons=document.querySelectorAll('#mediaPanel .media-delete-btn, #mediaPanel input[type="file"]');
 buttons.forEach(el=>el.disabled=true);
 if(status)status.textContent='جاري حذف '+label+'...';
 try{
  await api('/api/admin/site-media/'+encodeURIComponent(slot),{method:'DELETE',timeoutMs:15000});
  if(status)status.textContent='تم حذف '+label+' والعودة إلى الرمز الافتراضي.';
  await loadAdminMedia();
  // Restore defaults in place; do not reload the Telegram Mini App or lose the admin session.
 }catch(e){
  if(status)status.textContent='تعذر حذف الصورة: '+(e.message||'خطأ غير معروف');
  alert('تعذر حذف الصورة: '+(e.message||'حدث خطأ'));
 }finally{
  buttons.forEach(el=>el.disabled=false);
 }
}
async function uploadSiteMedia(slot,input){
 const file=input?.files?.[0];
 if(!file)return;
 const status=document.getElementById('mediaUploadStatus');
 if(file.size>5*1024*1024){alert('الحد الأقصى لحجم الصورة 5 ميغابايت.');input.value='';return;}
 if(!['image/webp','image/png','image/jpeg'].includes(file.type)){alert('اختر صورة بصيغة WebP أو PNG أو JPEG.');input.value='';return;}
 if(status)status.textContent='جاري رفع '+file.name+' وحفظها...';
 const cards=document.querySelectorAll('#mediaPanel input[type="file"]');
 cards.forEach(el=>el.disabled=true);
 try{
  await api('/api/admin/site-media/'+encodeURIComponent(slot),{
   method:'POST',
   headers:{'Content-Type':file.type},
   body:file,
   timeoutMs:30000
  });
  await loadAdminMedia();
  if(status)status.textContent='تم حفظ الصورة وتطبيقها بنجاح. ستبقى بعد إعادة النشر.';
 }catch(e){
  if(status)status.textContent='لم يتم حفظ الصورة: '+(e.message||'حدث خطأ');
  alert('تعذر رفع الصورة: '+(e.message||'حدث خطأ'));
 }finally{
  cards.forEach(el=>el.disabled=false);
  input.value='';
 }
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
 ['mediaPanel','🖼️','الصور والأيقونات','استبدال صورة الواجهة ورفع الأيقونات المصغرة'],
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
  giftExtendPanel:p.includes('subscriptions'),
  starsPanel:p.includes('payments'),
  staffPanel:p.includes('admins'),
  deployPanel:p.includes('settings'),
  mediaPanel:p.includes('settings'),
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
 if(p.includes('subscriptions'))tasks.push(loadGiftCandidates());
 if(p.includes('settings')){document.getElementById('planEditor').closest('.admin-panel').hidden=false;document.getElementById('plansEditorPanel').hidden=false;document.getElementById('plansEditorPanel').dataset.adminPermissionHidden='0';document.getElementById('deployPanel').hidden=false;document.getElementById('deployPanel').dataset.adminPermissionHidden='0';document.getElementById('mediaPanel').hidden=false;document.getElementById('mediaPanel').dataset.adminPermissionHidden='0';tasks.push(loadAdminPlans(),loadSubscriptionConfig(),loadDeployStatus(),loadAdminMedia());}
 else {document.getElementById('planEditor').closest('.admin-panel').hidden=true;document.getElementById('plansEditorPanel').hidden=true;document.getElementById('plansEditorPanel').dataset.adminPermissionHidden='1';document.getElementById('deployPanel').hidden=true;document.getElementById('deployPanel').dataset.adminPermissionHidden='1';document.getElementById('mediaPanel').hidden=true;document.getElementById('mediaPanel').dataset.adminPermissionHidden='1';}
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
 '<div class="user-actions"><button onclick="adminGrant('+u.telegram_id+',30)">30 يوم</button><button onclick="adminGrant('+u.telegram_id+',90)">3 أشهر</button><button onclick="adminGrant('+u.telegram_id+',365)">سنة</button><button onclick="adminFreeExtend('+u.telegram_id+',3)">مجاني +3</button><button onclick="adminFreeExtend('+u.telegram_id+',7)">مجاني +7</button><button onclick="adminFreeExtend('+u.telegram_id+',30)">مجاني +30</button><button onclick="adminGrant('+u.telegram_id+',\'forever\')">دائم</button><button class="danger" onclick="adminRevoke('+u.telegram_id+')">إلغاء</button><button class="admin-test-reset" onclick="adminResetForTest('+u.telegram_id+')">↻ إعادة فتح الاشتراك للاختبار</button></div></div>';
 }).join('')||'<p>لا توجد نتائج.</p>';}catch(e){document.getElementById('adminUsers').textContent=e.message;}}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
async function adminFreeExtend(id,d){try{await api('/api/admin/free-extend/'+id+'?days='+d,{method:'POST'});await adminRefresh();alert('تم تمديد المجاني للمستخدم.');}catch(e){alert(e.message);}}
async function adminGrant(id,d){try{await api('/api/admin/grant/'+id+'?days='+encodeURIComponent(d),{method:'POST'});await adminRefresh();}catch(e){alert(e.message);}}
let giftMode='single';
async function loadGiftCandidates(){
 const select=document.getElementById('giftExtendTelegramId');
 if(!select)return;
 const previous=select.value;
 select.innerHTML='<option value="">⏳ جاري تحميل المشتركين...</option>';
 try{
  const rows=await api('/api/admin/gift-candidates');
  if(!Array.isArray(rows)||!rows.length){select.innerHTML='<option value="">لا يوجد مشتركون فعّالون حاليًا</option>';return;}
  select.innerHTML='<option value="">— اختر المشترك —</option>'+rows.map(u=>{
   const name=esc(u.name||u.username||u.telegram_id);
   const username=u.username?' (@'+esc(u.username)+')':'';
   return '<option value="'+Number(u.telegram_id)+'">'+name+username+' · '+esc(u.kind)+' · ينتهي '+esc(fmtDate(u.expires_at))+' · ID '+Number(u.telegram_id)+'</option>';
  }).join('');
  if(previous&&rows.some(u=>String(u.telegram_id)===previous))select.value=previous;
 }catch(e){select.innerHTML='<option value="">تعذر تحميل المشتركين — حدّث اللوحة</option>';console.error('Gift candidates load failed',e);}
}
function setGiftMode(mode){
 giftMode=mode==='all'?'all':'single';
 const single=giftMode==='single';
 const target=document.getElementById('giftSingleTarget');
 const singleBtn=document.getElementById('giftModeSingle');
 const allBtn=document.getElementById('giftModeAll');
 const action=document.getElementById('giftExtendAllBtn');
 if(target)target.hidden=!single;
 if(single)loadGiftCandidates();
 if(singleBtn){singleBtn.classList.toggle('active',single);singleBtn.setAttribute('aria-pressed',String(single));}
 if(allBtn){allBtn.classList.toggle('active',!single);allBtn.setAttribute('aria-pressed',String(!single));}
 if(action)action.textContent=single?'🎁 إرسال الهدية وتمديد الاشتراك':'🎁 إرسال الهدية وتمديد اشتراكات الجميع';
 const result=document.getElementById('giftExtendResult');if(result)result.hidden=true;
}
async function adminGiftExtendAll(){
 const input=document.getElementById('giftExtendDays');
 const targetInput=document.getElementById('giftExtendTelegramId');
 const result=document.getElementById('giftExtendResult');
 const button=document.getElementById('giftExtendAllBtn');
 const days=Number(input?.value);
 const telegramId=Number(targetInput?.value);
 if(!Number.isInteger(days)||days<1||days>365){alert('أدخل مدة هدية من يوم إلى 365 يومًا.');return;}
 if(giftMode==='single'&&(!Number.isSafeInteger(telegramId)||telegramId<1)){alert('اختر المشترك الذي تريد إهداءه.');return;}
 const scope=giftMode==='single'?'للمستخدم '+telegramId:'لجميع المشتركين الفعّالين';
 if(!confirm('تأكيد إرسال هدية '+days+' يومًا '+scope+'؟'))return;
 button.disabled=true;button.textContent='⏳ جارٍ تنفيذ الهدية…';result.hidden=true;
 try{
  const payload={days};if(giftMode==='single')payload.telegram_id=telegramId;
  const d=await api('/api/admin/gift-extend-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  result.hidden=false;
  result.innerHTML='<b>🎉 اكتملت الهدية</b><br>الاشتراكات الممددة: '+Number(d.extended||0)+'<br>رسائل تيليجرام المرسلة: '+Number(d.notifications_sent||0)+'<br>تعذر إرسالها: '+Number(d.notifications_failed||0)+(d.skipped?'<br>تم تجاوزها: '+Number(d.skipped):'');
  button.textContent='✅ تم تنفيذ الهدية';
 }catch(e){alert(e.message||'تعذر تنفيذ الهدية.');button.textContent=giftMode==='single'?'🎁 إرسال الهدية وتمديد الاشتراك':'🎁 إرسال الهدية وتمديد اشتراكات الجميع';}
 finally{button.disabled=false;}
}
async function adminRevoke(id){if(!confirm('إلغاء وصول هذا المستخدم؟'))return;try{await api('/api/admin/revoke/'+id,{method:'POST'});await adminRefresh();}catch(e){alert(e.message);}}
async function adminResetForTest(id){
 if(!confirm('سيتم إلغاء الوصول الحالي لهذا المستخدم فقط، وإعادة تهيئة حالته ليظهر له الاشتراك من جديد عند فتح SAS PRO. هل تريد المتابعة؟'))return;
 try{
  await api('/api/admin/users/'+id+'/reset-for-test',{method:'POST'});
  await adminRefresh();
  alert('تمت إعادة تهيئة المستخدم للاختبار. اطلب منه إغلاق SAS PRO وفتحه من جديد.');
 }catch(e){alert(e.message||'تعذرت إعادة التهيئة.');}
}
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

// تحميل العلامة البصرية من مساحة التخزين الدائمة؛ لا يتدخل في بيانات الرادار.\nloadSiteMedia().catch(()=>{});\n

/* Interactive countdowns use the US exchange timezone, so DST is handled automatically. */
(function initSasMarketCountdown(){
 const zone='America/New_York';
 let selected='pre';
 const fmtParts=(date)=>{const p=new Intl.DateTimeFormat('en-US',{timeZone:zone,year:'numeric',month:'numeric',day:'numeric',weekday:'short',hour:'numeric',minute:'numeric',second:'numeric',hourCycle:'h23'}).formatToParts(date);const o={};for(const x of p)if(x.type!=='literal')o[x.type]=x.value;return o;};
 function zonedTimeToUtc(y,m,d,h,min){
   const wanted=Date.UTC(y,m-1,d,h,min,0);let guess=wanted;
   for(let i=0;i<4;i++){const p=fmtParts(new Date(guess));const actual=Date.UTC(Number(p.year),Number(p.month)-1,Number(p.day),Number(p.hour),Number(p.minute),Number(p.second));const diff=wanted-actual;guess+=diff;if(Math.abs(diff)<1000)break;}
   return guess;
 }
 function nextOpening(now,hour,minute){
   const p=fmtParts(now);const base=Date.UTC(Number(p.year),Number(p.month)-1,Number(p.day));
   for(let n=0;n<12;n++){const d=new Date(base+n*86400000);const dow=d.getUTCDay();if(dow===0||dow===6)continue;const target=zonedTimeToUtc(d.getUTCFullYear(),d.getUTCMonth()+1,d.getUTCDate(),hour,minute);if(target>now.getTime())return target;}
   return now.getTime()+86400000;
 }
 function dur(ms){let s=Math.max(0,Math.floor(ms/1000));const days=Math.floor(s/86400);s%=86400;const hh=Math.floor(s/3600);s%=3600;const mm=Math.floor(s/60),ss=s%60;const pad=n=>String(n).padStart(2,'0');const clock=pad(hh)+':'+pad(mm)+':'+pad(ss);return (days?'<span class="sas-countdown-days">'+days+' '+(days===1?'يوم':'أيام')+'</span>':'')+'<span class="sas-countdown-clock" dir="ltr">'+clock+'</span>';}
 function arDate(ms){return new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{timeZone:'Asia/Riyadh',weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(new Date(ms));}
 function arTime(ms){return new Intl.DateTimeFormat('ar-SA-u-ca-gregory',{timeZone:'Asia/Riyadh',hour:'numeric',minute:'2-digit',hour12:true}).format(new Date(ms));}
 function openingDateTime(ms){return '<span class="sas-opening-date" dir="rtl">'+arDate(ms)+'</span><span class="sas-opening-time"><bdi dir="ltr">'+arTime(ms)+'</bdi> • بتوقيت الرياض</span>';}
 function marketPhase(now){
   const p=fmtParts(now),day=String(p.weekday||'').toLowerCase();
   const mins=Number(p.hour)*60+Number(p.minute);
   if(day==='sat'||day==='sun')return 'closed';
   if(mins>=240&&mins<570)return 'pre';
   if(mins>=570&&mins<960)return 'open';
   if(mins>=960&&mins<1200)return 'after';
   return 'closed';
 }
 function paint(){
   const now=new Date(), pre=nextOpening(now,4,0), regular=nextOpening(now,9,30);
   const pe=document.getElementById('sasPreCountdown'),re=document.getElementById('sasRegularCountdown');
   if(!pe||!re)return;
   pe.innerHTML=dur(pre-now.getTime());re.innerHTML=dur(regular-now.getTime());
   const pd=document.getElementById('sasPreCountdownLabel'),rd=document.getElementById('sasRegularCountdownLabel');
   if(pd)pd.textContent=pre-now.getTime()<1000?'بدأ وقت ما قبل الافتتاح':'متبقي على بداية ما قبل الافتتاح';
   if(rd)rd.textContent=regular-now.getTime()<1000?'بدأت الجلسة الرسمية':'متبقي على افتتاح الجلسة الرسمية';
   const pw=document.getElementById('sasPreCountdownWhen'),rw=document.getElementById('sasRegularCountdownWhen');
   if(pw)pw.innerHTML=openingDateTime(pre);
   if(rw)rw.innerHTML=openingDateTime(regular);
   const date=document.getElementById('sasCountdownDate');if(date)date.innerHTML='<span class="sas-opening-date" dir="rtl">موعد الجلسة القادمة: '+arDate(pre)+'</span><span class="sas-opening-time">جميع الأوقات بتوقيت الرياض</span>';
   const phase=marketPhase(now),strip=document.querySelector('.sas-market-strip'),headline=document.getElementById('sasMarketHeadline');
   if(strip){strip.classList.remove('is-open','is-pre','is-closed','is-after');strip.classList.add(phase==='open'?'is-open':phase==='pre'?'is-pre':phase==='after'?'is-after':'is-closed');strip.dataset.marketState=phase;}
   if(headline)headline.textContent=phase==='open'?'السوق الأمريكي مفتوح':phase==='pre'?'السوق الأمريكي قبل الافتتاح':phase==='after'?'التداول بعد الإغلاق':'السوق الأمريكي مغلق';
   const sub=document.getElementById('sasMarketSubline');
   if(sub)sub.textContent=phase==='closed'?'السوق مغلق حاليًا • موعد ما قبل الافتتاح 04:00 نيويورك':phase==='pre'?'فترة ما قبل الافتتاح • بتوقيت نيويورك':'التوقيت حسب بورصة نيويورك • جميع المواعيد بتوقيت الرياض';
   const session=document.getElementById('sasSessionLabel');
   if(session)session.textContent=phase==='open'?'الجلسة الرسمية مفتوحة':phase==='pre'?'ما قبل الافتتاح':phase==='after'?'بعد الإغلاق':'مغلق — لا يوجد تداول';
   document.querySelectorAll('[data-countdown]').forEach(b=>{const active=b.dataset.countdown===selected;b.classList.toggle('is-selected',active);b.setAttribute('aria-pressed',String(active));});
 }
 document.querySelectorAll('[data-countdown]').forEach(b=>b.addEventListener('click',()=>{selected=b.dataset.countdown;paint();}));
 paint();setInterval(paint,1000);
})();


/* Editable gift notification template managed by SAS ADMIN. */
function renderGiftMessagePreview(){
 const days=Number(document.getElementById('giftExtendDays')?.value||7);
 const node=document.getElementById('giftMessagePreviewDays');
 if(node)node.textContent=Number.isInteger(days)&&days>0?String(days):'7';
}
document.addEventListener('input',event=>{if(event.target?.id==='giftExtendDays')renderGiftMessagePreview();});
