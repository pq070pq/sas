/* SAS PRO deployment trigger */
/* SAS PRO final deploy trigger */
/* SAS PRO news ribbon and admin news panel: 20261015-news-admin-4 */
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
  const externalSignal=fetchOptions.signal;
  const abortFromCaller=()=>controller.abort();
  if(externalSignal?.aborted)controller.abort();
  else externalSignal?.addEventListener('abort',abortFromCaller,{once:true});
  const timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{
   if(controller.signal.aborted)throw new DOMException('Aborted','AbortError');
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
    const apiError=new Error(msg);apiError.status=r.status;throw apiError;
   }
   return await r.json();
  }catch(e){
   lastError=e;
   if(attempt<retries){
    await sleep(350*(attempt+1));
    continue;
   }
  }finally{clearTimeout(timeout);externalSignal?.removeEventListener('abort',abortFromCaller);}
 }
 if(lastError?.name==='AbortError')throw new Error('تعذر تحديث البيانات في الوقت المحدد. سيُعاد الاتصال تلقائيًا عند المحاولة التالية.');
 throw lastError||new Error('تعذر تنفيذ العملية');
}
const enDigits=v=>String(v??'').replace(/[٠-٩]/g,d=>String(d.charCodeAt(0)-0x0660));
const normalizeEnglishDigits=root=>{if(!root)return;const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const nodes=[];while(w.nextNode())nodes.push(w.currentNode);nodes.forEach(n=>{const x=enDigits(n.nodeValue);if(x!==n.nodeValue)n.nodeValue=x;});};
const fmtDate=v=>v?enDigits(new Date(v).toLocaleDateString('ar-SA')):'—';
const fmtDays=(a,b)=>{if(!a||!b)return'—';const n=Math.ceil((new Date(b)-new Date(a))/86400000);return n>0?n+' يوم':'منتهي';};
let me=null,plans=null,termAction=null;
let activeSymbolSearchController=null;
try{localStorage.removeItem('saspro_watchlist');}catch(e){}
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

let sasWelcomeTimer=null;
let sasChannelInviteTimer=null;
function showSASWelcome(){
 if(localStorage.getItem('saspro_onboarding_pending')!=='1')return;
 if(me && !me.pro)return;
 const overlay=document.getElementById('sasWelcomeOverlay');
 const field=overlay?.querySelector('.sas-fireworks');
 if(!overlay||!field||localStorage.getItem('saspro_channel_onboarding_done')==='1')return;
 localStorage.removeItem('saspro_onboarding_pending');
 if(sasWelcomeTimer)window.clearTimeout(sasWelcomeTimer);
 if(sasChannelInviteTimer)window.clearTimeout(sasChannelInviteTimer);
 overlay.hidden=false;
 overlay.classList.remove('is-leaving','is-join-stage','is-visible');
 const greeting=document.getElementById('sasWelcomeGreeting');
 const join=document.getElementById('sasWelcomeJoin');
 if(greeting)greeting.hidden=false;
 if(join)join.hidden=true;
 field.replaceChildren();
 const colors=['#ff405d','#ffd166','#4de4f0','#b69cff','#ffffff','#ff9f43'];
 const launch=(x,y)=>{
  const burst=document.createElement('span');burst.className='sas-firework-burst';burst.style.left=x+'%';burst.style.top=y+'%';
  const count=42+Math.floor(Math.random()*20);
  for(let i=0;i<count;i++){
   const p=document.createElement('i'),angle=Math.PI*2*i/count+Math.random()*.12,distance=55+Math.random()*105;
   p.style.setProperty('--dx',Math.cos(angle)*distance+'px');p.style.setProperty('--dy',Math.sin(angle)*distance+'px');
   p.style.setProperty('--particle-color',colors[Math.floor(Math.random()*colors.length)]);
   p.style.setProperty('--particle-size',2+Math.random()*3+'px');p.style.setProperty('--particle-delay',Math.random()*.12+'s');burst.appendChild(p);
  }
  field.appendChild(burst);window.setTimeout(()=>burst.remove(),1800);
 };
 let bursts=0;
 const fireTimer=window.setInterval(()=>{launch(12+Math.random()*76,14+Math.random()*54);if(++bursts>=7)window.clearInterval(fireTimer);},300);
 const onTap=e=>{const r=overlay.getBoundingClientRect();launch(Math.max(4,Math.min(96,(e.clientX-r.left)/r.width*100)),Math.max(5,Math.min(90,(e.clientY-r.top)/r.height*100)));};
 overlay.addEventListener('pointerdown',onTap);
 requestAnimationFrame(()=>overlay.classList.add('is-visible'));
 // Welcome lasts 5 seconds; the channel invitation appears 10 seconds after it disappears.
 sasWelcomeTimer=window.setTimeout(()=>finishSASWelcome(onTap,fireTimer),5000);
}
function finishSASWelcome(onTap=null,fireTimer=null){
 const overlay=document.getElementById('sasWelcomeOverlay');
 const field=overlay?.querySelector('.sas-fireworks');
 const greeting=document.getElementById('sasWelcomeGreeting');
 if(fireTimer)window.clearInterval(fireTimer);
 if(onTap&&overlay)overlay.removeEventListener('pointerdown',onTap);
 if(field)field.replaceChildren();
 if(greeting)greeting.hidden=true;
 if(overlay){overlay.classList.add('is-leaving');window.setTimeout(()=>{overlay.hidden=true;overlay.classList.remove('is-visible','is-leaving');},350);}
 if(sasChannelInviteTimer)window.clearTimeout(sasChannelInviteTimer);
 sasChannelInviteTimer=window.setTimeout(showSASChannelInvite,10000);
}
function skipSASWelcome(){
 if(sasWelcomeTimer)window.clearTimeout(sasWelcomeTimer);
 const overlay=document.getElementById('sasWelcomeOverlay');
 const field=overlay?.querySelector('.sas-fireworks');
 finishSASWelcome(null,null);
 if(field)field.replaceChildren();
}
function showSASChannelInvite(){
 if(localStorage.getItem('saspro_channel_onboarding_done')==='1')return;
 const overlay=document.getElementById('sasWelcomeOverlay');
 const greeting=document.getElementById('sasWelcomeGreeting');
 const join=document.getElementById('sasWelcomeJoin');
 if(!overlay||!join)return;
 if(greeting)greeting.hidden=true;
 join.hidden=false;
 overlay.hidden=false;
 overlay.classList.remove('is-leaving');
 overlay.classList.add('is-join-stage');
 requestAnimationFrame(()=>overlay.classList.add('is-visible'));
}
function dismissWelcome(){
 localStorage.setItem('saspro_channel_onboarding_done','1');
 if(sasWelcomeTimer)window.clearTimeout(sasWelcomeTimer);
 if(sasChannelInviteTimer)window.clearTimeout(sasChannelInviteTimer);
 const overlay=document.getElementById('sasWelcomeOverlay');
 overlay?.classList.add('is-leaving');
 window.setTimeout(()=>{if(overlay){overlay.hidden=true;overlay.classList.remove('is-visible','is-leaving','is-join-stage');}},350);
}

async function joinFromWelcome(){
 const button=document.getElementById('sasWelcomeJoinBtn');
 if(button){button.disabled=true;button.textContent='⏳ جارٍ تجهيز رابط الانضمام…';}
 try{
  if(!me?.terms_accepted){dismissWelcome();openTerms('trial');return;}
  if(me?.pro){
   const d=await api('/api/channel/access');
   localStorage.setItem('saspro_channel_onboarding_done','1');
   if(tg?.openTelegramLink)tg.openTelegramLink(d.url);else if(tg?.openLink)tg.openLink(d.url);else window.open(d.url,'_blank');
   dismissWelcome();return;
  }
  dismissWelcome();openTerms('trial');
 }catch(e){alert(e.message||'تعذر تجهيز رابط القناة.');}
 finally{if(button){button.disabled=false;button.textContent='🚀 الانضمام للقناة';}}
}

window.addEventListener('error',event=>{
 const name=document.getElementById('userName');
 const grid=document.getElementById('subscriptionStatusGrid');
 if(name&&grid?.getAttribute('aria-busy')==='true'){
  showAccountLoadError('توقف تحميل الواجهة بسبب خطأ JavaScript: '+String(event.message||'خطأ غير معروف').slice(0,160));
 }
});
window.addEventListener('unhandledrejection',event=>{
 const grid=document.getElementById('subscriptionStatusGrid');
 if(grid?.getAttribute('aria-busy')==='true'){
  showAccountLoadError('تعذر إكمال التحقق: '+String(event.reason?.message||event.reason||'خطأ غير معروف').slice(0,160));
 }
});
async function load(){
 if(loadStarted)return;
 loadStarted=true;
 const watchdog=setTimeout(()=>{
  if(!loadStarted)return;
  loadStarted=false;
  showAccountLoadError('التحقق تأخر وما اكتمل. اضغط «إعادة المحاولة»، أو أغلق SAS PRO وافتحه من تيليجرام من جديد.');
 },8000);
 try{
  refreshTelegramWebApp();
  if(!tg)throw new Error('تعذر الوصول إلى Telegram WebApp. افتح SAS PRO من داخل Telegram.');
  const initData=await waitForTelegramInitData(3500);
  if(!initData)throw new Error('لم تصل بيانات Telegram الموثقة إلى التطبيق. أغلق Mini App وافتحه من زر SAS PRO داخل Telegram ثم أعد المحاولة.');
  me=await api('/api/me',{timeoutMs:6500,retries:0});
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
  const cfg=await api('/api/subscription/config',{timeoutMs:6500,retries:0});
  plans=await api('/api/subscription/plans',{timeoutMs:6500,retries:0});
  renderTrialCard(cfg.trial_days);
  document.getElementById('paidPlansSection').hidden=!cfg.paid_plans_visible;
  renderPlans(plans.plans||{});
  // الشروط أولًا؛ بعد الموافقة يبدأ الترحيب ثم تظهر دعوة القناة.
  if(!me.terms_accepted){
   openTerms('trial');
   return;
  }
  showSASWelcome();
 }catch(e){
  clearTimeout(watchdog);
  const msg=e?.message||'تعذر التحقق من Telegram.';
  showAccountLoadError(msg);
  loadStarted=false;
 }
}

function readLocalJson(key,fallback){
 try{const raw=localStorage.getItem(key);return raw?JSON.parse(raw):fallback;}
 catch(error){console.warn('SAS PRO local cache ignored:',key,error?.message||error);return fallback;}
}
const savedRadarSnapshot=readLocalJson('saspro_radar_snapshot',[]);
const terminalState={ticker:[],radar:Array.isArray(savedRadarSnapshot)?savedRadarSnapshot:[],preliminaryRadar:[],radarAutoScanAttempted:false,perpAssets:{},watch:readLocalJson('saspro_watchlist',[]),timer:null,moversTimer:null,newsTimer:null,newsItems:[],newsFilter:'all',moversRefreshing:false,tab:'dashboard',refreshing:false,radarScanning:false};
let radarSnapshotLastChecked=0;
let radarSnapshotLoadPromise=null;
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
   // API market-news responses include translation fields on each news item.
   // Prefer those fields before the optional separate translation array.
   const translatedHeadline=String(n.translated_headline||translated?.translated_headline||'').trim();
   const headline=escHtml(translatedHeadline||originalHeadline);
   const originalTitle=translatedHeadline&&translatedHeadline!==originalHeadline?'<small class="news-original-title">العنوان الأصلي: '+escHtml(originalHeadline)+'</small>':'';
   const source=escHtml(n.source||translated?.source||'مصدر غير محدد');
   const aiSummary=String(n.translated_summary||translated?.summary||n.ai_summary||'').trim();
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
async function loadRadarSnapshot(){
 if(terminalState.radar.length||Date.now()-radarSnapshotLastChecked<15000)return;
 if(radarSnapshotLoadPromise)return radarSnapshotLoadPromise;
 radarSnapshotLastChecked=Date.now();
 radarSnapshotLoadPromise=(async()=>{
  try{
   // A normal tab open reads the saved snapshot only; it must not trigger a fresh multi-minute scan.
   const d=await api('/api/radar/scan',{timeoutMs:12000,retries:0});
   if(Array.isArray(d.stocks)){
    // An empty successful snapshot is meaningful: clear old opportunities instead of
    // keeping yesterday's cards alive in memory/localStorage.
    terminalState.radar=d.stocks;
    try{localStorage.setItem('saspro_radar_snapshot',JSON.stringify(terminalState.radar));}catch(e){}
   }
  }catch(e){
   console.warn('SAS PRO saved radar snapshot unavailable:',e?.message||e);
  }finally{
   renderRadar();
   radarSnapshotLoadPromise=null;
  }
 })();
 return radarSnapshotLoadPromise;
}
function switchTerminalTab(tab){
 if(tab!=='search'&&activeSymbolSearchController){activeSymbolSearchController.abort();activeSymbolSearchController=null;}
 terminalState.tab=tab;
 document.querySelectorAll('.terminal-tab').forEach(x=>x.classList.toggle('active',x.id==='tab-'+tab));
 document.querySelectorAll('.terminal-tabs button').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
 document.querySelectorAll('.sas-mobile-dock button').forEach(x=>x.classList.toggle('is-active',x.getAttribute('onclick')?.includes("'"+tab+"'")));
 if(tab==='radar'){renderRadar();void loadRadarSnapshot();}

}
async function initTerminal(){
 const backBtn=document.getElementById('adminBackBtn');
 if(backBtn)backBtn.hidden=!Boolean(me?.admin);
 document.getElementById('terminalGreeting').textContent='مرحبًا '+(me.user?.first_name||me.user?.username||'في SAS PRO');
 document.getElementById('accountName').textContent=me.user?.first_name||me.user?.username||'مستخدم SAS PRO';
 document.getElementById('accountPlan').textContent=(me.user?.plan||'SAS PRO')+' • وصول فعّال';
 renderAccount();
 if(terminalState.timer)clearInterval(terminalState.timer);
 if(terminalState.moversTimer)clearInterval(terminalState.moversTimer);
 await Promise.allSettled([refreshTerminal(),refreshMoversOnly()]);
 terminalState.timer=setInterval(refreshTerminal,20000);
 // Refresh winning/losing equity prices every five minutes, independently of the dashboard's faster refresh.
 terminalState.moversTimer=setInterval(refreshMoversOnly,300000);
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
 if(terminalState.tickerTimer){clearInterval(terminalState.tickerTimer);terminalState.tickerTimer=null;}
 if(terminalState.moversTimer){clearInterval(terminalState.moversTimer);terminalState.moversTimer=null;}
 document.getElementById('terminalPage').hidden=true;
 document.getElementById('adminPage').hidden=false;
 adminRefresh();
 window.scrollTo({top:0,behavior:'smooth'});
}

async function refreshMoversOnly(){
 if(terminalState.moversRefreshing||document.visibilityState!=='visible')return;
 terminalState.moversRefreshing=true;
 try{
  const movers=await api('/api/market/movers',{timeoutMs:30000,retries:0});
  renderPerpEquitiesMovers(movers);
 }catch(e){
  console.warn('SAS PRO movers refresh failed:',e?.message||e);
 }finally{
  terminalState.moversRefreshing=false;
 }
}
async function refreshTerminal(){
 if(terminalState.refreshing)return;
 terminalState.refreshing=true;
 try{
  // Update each panel as soon as its own request completes. Do not let a slow
  // movers/status endpoint keep already-received prices stuck on "loading".
  const requests=[
   api('/api/market/radar-status').then(status=>{renderMarketStrip(status);return status;}),
   api('/api/dashboard/home').then(home=>{renderDashboard(home);return home;}),
   api('/api/market/ticker').then(ticker=>{
    if(!Array.isArray(ticker))return ticker;
    // Paint real/cached market prices immediately; fetch missing gold separately.
    terminalState.ticker=ticker;
    renderMacro();
    const gold=ticker.find(x=>String(x?.symbol||'').toUpperCase()==='XAU/USD' || String(x?.label||'').toUpperCase()==='GOLD');
    if(!gold || !(Number(gold.price)>0)){
     fetchPublicGoldFallback().then(directGold=>{
      if(!directGold)return;
      const current=Array.isArray(terminalState.ticker)?terminalState.ticker:[];
      const idx=current.findIndex(x=>String(x?.symbol||'').toUpperCase()==='XAU/USD' || String(x?.label||'').toUpperCase()==='GOLD');
      if(idx>=0)current[idx]=directGold;else current.push(directGold);
      terminalState.ticker=current;
      renderMacro();
     }).catch(()=>{});
    }
    return ticker;
   }),
  ];
  // Start the radar scan independently too; it must not wait for ticker/movers.
  if(terminalState.tab==='radar')void runRadar(false);
  else if(!terminalState.radar.length&&!terminalState.preliminaryRadar.length&&!terminalState.radarAutoScanAttempted){terminalState.radarAutoScanAttempted=true;void runRadar(false);}
  const results=await Promise.allSettled(requests);
  const errors=results.filter(x=>x.status==='rejected').map(x=>x.reason?.message||'خطأ غير معروف');
  if(errors.length)console.warn('SAS PRO panel refresh partial failures:',errors);

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
  night:'التداول الليلي ☾',
  night_pending:'☾ التداول الليلي يفتح قريبًا'
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
 const scheduleNight=document.getElementById('sasScheduleNight');
 const scheduleNightET=document.getElementById('sasScheduleNightET');
 const schedulePre=document.getElementById('sasSchedulePre');
 const scheduleRegular=document.getElementById('sasScheduleRegular');
 const scheduleAfter=document.getElementById('sasScheduleAfter');
 const scheduleStatus=document.getElementById('sasScheduleStatus');
 if(schedulePre)schedulePre.textContent=nextPremarket+' – '+regularOpen;
 if(scheduleRegular)scheduleRegular.textContent=regularOpen+' – '+regularClose;
 if(scheduleAfter)scheduleAfter.textContent=regularClose+' – '+afterClose;
  // Exchange time is authoritative; convert New York session hours to Riyadh with DST handled automatically.
  const exchangeNow=new Date();
  const nyParts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(exchangeNow).filter(p=>p.type!=='literal').map(p=>[p.type,p.value]));
  const nyZoneName=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',timeZoneName:'short'}).formatToParts(exchangeNow).find(p=>p.type==='timeZoneName')?.value||'ET';
  const nyOffsetText=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',timeZoneName:'longOffset'}).formatToParts(exchangeNow).find(p=>p.type==='timeZoneName')?.value||'GMT-04:00';
  const offsetMatch=nyOffsetText.match(/GMT([+-])(\\d{2}):(\\d{2})/);
  const nyOffsetMinutes=offsetMatch?(offsetMatch[1]==='-'?-1:1)*(Number(offsetMatch[2])*60+Number(offsetMatch[3])):-240;
  const riyadhFromET=(hour,minute)=>{
    const utcMs=Date.UTC(Number(nyParts.year),Number(nyParts.month)-1,Number(nyParts.day),hour,minute)-nyOffsetMinutes*60000;
    return new Intl.DateTimeFormat('ar-SA',{timeZone:'Asia/Riyadh',hour:'numeric',minute:'2-digit',hour12:true}).format(new Date(utcMs));
  };
  const preRiyadh=riyadhFromET(4,0)+' – '+riyadhFromET(9,30);
  const regularRiyadh=riyadhFromET(9,30)+' – '+riyadhFromET(16,0);
  const afterRiyadh=riyadhFromET(16,0)+' – '+riyadhFromET(20,0);
  if(schedulePre)schedulePre.textContent=preRiyadh;
  if(scheduleRegular)scheduleRegular.textContent=regularRiyadh;
  if(scheduleAfter)scheduleAfter.textContent=afterRiyadh;
  const preET=document.getElementById('sasSchedulePreET');
  const regularET=document.getElementById('sasScheduleRegularET');
  const afterET=document.getElementById('sasScheduleAfterET');
  const nightStartRiyadh=riyadhFromET(20,0);
  const nightEndRiyadh=riyadhFromET(4,0);
  if(scheduleNight) scheduleNight.textContent=nightStartRiyadh+' – '+nightEndRiyadh;
  const et12=(hour,minute)=>{const suffix=hour<12?'ص':'م';const h12=hour%12||12;return String(h12)+':'+String(minute).padStart(2,'0')+' '+suffix;};
  if(scheduleNightET) scheduleNightET.textContent=et12(20,0)+' – '+et12(4,0)+' بتوقيت نيويورك ('+nyZoneName+')';
  if(preET)preET.textContent=et12(4,0)+' – '+et12(9,30)+' بتوقيت نيويورك ('+nyZoneName+')';
  if(regularET)regularET.textContent=et12(9,30)+' – '+et12(16,0)+' بتوقيت نيويورك ('+nyZoneName+')';
  if(afterET)afterET.textContent=et12(16,0)+' – '+et12(20,0)+' بتوقيت نيويورك ('+nyZoneName+')';
 if(scheduleStatus)scheduleStatus.textContent=({
  premarket:'🟡 السوق في فترة ما قبل الافتتاح — التداول الممتد قبل الجلسة الرسمية.',
  regular:'🟢 الجلسة الرسمية مفتوحة الآن — التداول جارٍ.',
  afterhours:'🟠 انتهت الجلسة الرسمية — التداول الممتد بعد الإغلاق.',
  night:'☾ التداول الليلي جارٍ عبر منصات/وسطاء داعمين؛ التوفر والأسعار يختلفان حسب السهم ومزود البيانات.',
  night_pending:'☾ التداول الليلي لم يبدأ بعد.',
  overnight:'🟣 مراقبة SAS PRO مستمرة على مدار الساعة؛ فحص الأسهم يتبع الجلسات المتاحة وموثوقية بيانات السوق.',
  weekend:'🟣 مراقبة النظام مستمرة في عطلة نهاية الأسبوع؛ لا توجد جلسة أسهم أمريكية عادية، وتُعرض البيانات المتاحة فقط.',
  holiday:'🟣 مراقبة النظام مستمرة في عطلة السوق؛ فحص الأسهم يعتمد على التقويم وتوفر بيانات موثوقة.'
 }[session]||'يتم التحقق من حالة الجلسة الحالية.');
 document.getElementById('radarStatusText').textContent=nightPending
  ? '☾ التداول الليلي يفتح قريبًا'+nightCountdown+' — الرصد لا ينشر إشارة إلا بعد تحقق السعر والسيولة والزخم.'
  : premarket
   ? '🟡 Pre-Market — الرصد المبكر يعمل حتى '+regularOpen+' بتوقيت الرياض، مع تأكيد السعر والسيولة والزخم.'
   : session==='regular'
    ? '🟢 السوق الأمريكي مفتوح — الجلسة الرسمية حتى '+regularClose+' بتوقيت الرياض.'
    : session==='afterhours'
     ? '🟠 After-Hours — التداول الممتد حتى '+afterClose+' بتوقيت الرياض.'
     : session==='night'
      ? '☾ فترة التداول الليلي — الرصد يعمل، لكن التداول الفعلي وتحديث السعر يعتمدان على الوسيط ومزود البيانات.'
      : preopen
       ? '🟣 مراقبة SAS PRO مستمرة؛ يبدأ فحص الأسهم في فترة ما قبل الافتتاح الساعة '+nextPremarket+' بتوقيت الرياض.'
       : enabled
     ? (extended
       ? '🟡 الرصد الآلي يعمل في '+sessionLabel+' — لا تُنشر الإشارة إلا بعد تأكيد السعر والسيولة والزخم.'
       : '🟢 الرصد الآلي يعمل — يبحث عن الأسهم التي تستوفي بوابة SAS PRO.')
     : (historical ? '🟣 مراقبة SAS PRO مستمرة؛ المعروض آخر بيانات رصد محفوظة من الجلسة السابقة، وسيُستأنف فحص الأسهم حسب الجلسة وتوفر البيانات.'
       : '🟣 مراقبة SAS PRO مستمرة على مدار الساعة؛ فحص الأسهم وإرسال الفرص يتبعان الجلسة المتاحة وشروط تأكيد السعر والسيولة والزخم.');
 const statusText=document.getElementById('radarStatusText')?.textContent||'';
 const radarStatusNode=document.getElementById('radarStatusText');
 if(radarStatusNode && !document.getElementById('radarPreliminaryNotice')){
  const radarNotice=document.createElement('div');
  radarNotice.id='radarPreliminaryNotice';
  radarNotice.textContent='ℹ️ الرصد الأولي قيد المتابعة؛ إذا حقق السهم شروط التأكيد والتحقق، ستُرسل الفرصة إلى القناة وتظهر في بطاقات الفرص.';
  radarNotice.style.cssText='position:relative;z-index:1;margin-top:8px;padding:9px 12px;border:1px solid rgba(70,190,210,.28);border-radius:10px;background:rgba(10,70,85,.18);color:#b8dce5;font-size:12px;line-height:1.7;text-align:right;';
  radarStatusNode.insertAdjacentElement('afterend',radarNotice);
 }
 const sasAmbienceStyleId='sas-session-ambience-style';
 if(!document.getElementById(sasAmbienceStyleId)){
  const style=document.createElement('style');
  style.id=sasAmbienceStyleId;
  style.textContent=`
   .sas-market-strip{position:relative;isolation:isolate;overflow:hidden;transition:background .6s ease,border-color .6s ease,box-shadow .6s ease}
   .sas-market-strip::after{content:"";position:absolute;inset:-45%;z-index:0;pointer-events:none;opacity:.34;filter:blur(22px);transform:translate3d(-12%,0,0);background:radial-gradient(ellipse at 25% 45%,rgba(65,210,165,.26),transparent 48%),radial-gradient(ellipse at 78% 55%,rgba(54,156,255,.19),transparent 44%);animation:sasRadarAmbientFlow 16s ease-in-out infinite alternate}
   .sas-market-strip>*{position:relative;z-index:1}
   .sas-market-strip.sas-session-regular::after{opacity:.48;background:radial-gradient(ellipse at 25% 45%,rgba(44,230,142,.30),transparent 48%),radial-gradient(ellipse at 78% 55%,rgba(42,160,255,.22),transparent 44%);animation-duration:9s}
   .sas-market-strip.sas-session-pre::after{opacity:.40;background:radial-gradient(ellipse at 25% 45%,rgba(255,184,72,.28),transparent 48%),radial-gradient(ellipse at 78% 55%,rgba(65,173,255,.18),transparent 44%);animation-duration:12s}
   .sas-market-strip.sas-session-after::after{opacity:.40;background:radial-gradient(ellipse at 25% 45%,rgba(255,145,67,.25),transparent 48%),radial-gradient(ellipse at 78% 55%,rgba(145,93,255,.18),transparent 44%);animation-duration:13s}
   .sas-market-strip.sas-session-night::after{opacity:.34;background:radial-gradient(ellipse at 25% 45%,rgba(155,114,255,.25),transparent 48%),radial-gradient(ellipse at 78% 55%,rgba(56,149,255,.18),transparent 44%);animation-duration:18s}
   .sas-market-strip.sas-session-closed::after{opacity:.18;background:radial-gradient(ellipse at 25% 45%,rgba(111,142,168,.20),transparent 48%),radial-gradient(ellipse at 78% 55%,rgba(75,93,126,.13),transparent 44%);animation-duration:22s}
   @keyframes sasRadarAmbientFlow{0%{transform:translate3d(-12%,-4%,0) scale(.96)}100%{transform:translate3d(12%,5%,0) scale(1.08)}}
   #app{position:relative;isolation:isolate;min-height:100vh}
   #app::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.24;filter:blur(2px);background:radial-gradient(ellipse at 12% 18%,rgba(70,150,220,.15),transparent 42%),radial-gradient(ellipse at 86% 78%,rgba(100,110,190,.12),transparent 45%);transition:opacity .8s ease,background 1s ease;animation:sasPageAtmosphere 24s ease-in-out infinite alternate}
   #app.sas-page-regular::before{opacity:.36;background:radial-gradient(ellipse at 15% 20%,rgba(35,220,140,.22),transparent 43%),radial-gradient(ellipse at 82% 72%,rgba(40,150,255,.18),transparent 46%)}
   #app.sas-page-pre::before{opacity:.32;background:radial-gradient(ellipse at 15% 20%,rgba(255,185,65,.22),transparent 43%),radial-gradient(ellipse at 82% 72%,rgba(45,150,255,.16),transparent 46%)}
   #app.sas-page-after::before{opacity:.32;background:radial-gradient(ellipse at 15% 20%,rgba(255,135,65,.20),transparent 43%),radial-gradient(ellipse at 82% 72%,rgba(150,90,255,.17),transparent 46%)}
   #app.sas-page-night::before{opacity:.28;background:radial-gradient(ellipse at 15% 20%,rgba(150,105,255,.20),transparent 43%),radial-gradient(ellipse at 82% 72%,rgba(45,140,255,.17),transparent 46%)}
   #app.sas-page-closed::before{opacity:.18;background:radial-gradient(ellipse at 15% 20%,rgba(95,130,160,.17),transparent 43%),radial-gradient(ellipse at 82% 72%,rgba(75,90,135,.13),transparent 46%)}
   @keyframes sasPageAtmosphere{0%{background-position:0% 0%;transform:scale(1)}100%{background-position:100% 100%;transform:scale(1.035)}}
   @media(prefers-reduced-motion:reduce){.sas-market-strip::after,#app::before{animation:none!important;transform:none!important}}
  `;
  document.head.appendChild(style);
 }
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
 const sasApp=document.getElementById('app');
 if(sasApp){sasApp.classList.remove('sas-page-regular','sas-page-pre','sas-page-after','sas-page-night','sas-page-closed');sasApp.classList.add(session==='regular'?'sas-page-regular':premarket?'sas-page-pre':session==='afterhours'?'sas-page-after':session==='night'?'sas-page-night':'sas-page-closed');sasApp.dataset.session=session||'unknown';}
 if(strip){strip.classList.remove('is-open','is-pre','is-closed','sas-session-regular','sas-session-pre','sas-session-after','sas-session-night','sas-session-closed');strip.classList.add(session==='regular'||session==='afterhours'||session==='night'?'is-open':premarket?'is-pre':'is-closed');strip.classList.add(session==='regular'?'sas-session-regular':premarket?'sas-session-pre':session==='afterhours'?'sas-session-after':session==='night'?'sas-session-night':'sas-session-closed');strip.dataset.marketState=session==='regular'?'regular':premarket?'premarket':session==='afterhours'?'afterhours':session==='night'?'night':'closed';}
 const dock=document.querySelector('.sas-mobile-dock');
 if(dock){dock.querySelectorAll('button').forEach(b=>b.classList.remove('is-active'));const active=dock.querySelector('button[onclick*="'+(terminalState.tab||'dashboard')+'"]');if(active)active.classList.add('is-active');}
 const metricDate=r.last_signal_at?new Date(r.last_signal_at):null;
 const metricDateValid=metricDate&&Number.isFinite(metricDate.getTime());
 const metricDateLabel=metricDateValid?new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(metricDate):'لا توجد إشارة مسجلة';
 const metricTimeLabel=metricDateValid?new Intl.DateTimeFormat('ar-SA-u-ca-gregory',{hour:'2-digit',minute:'2-digit',hour12:true}).format(metricDate):'بانتظار أول إشارة';
 const metricItems=[
  {id:'opportunities',icon:'<svg viewBox="0 0 24 24" fill="none"><path d="M3 17l6-6 4 3 8-9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 5h6v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',label:historical?'فرص آخر جلسة':'فرص اليوم',value:Number(r.opportunities??terminalState.radar.length??0).toLocaleString('ar-SA'),detail:historical?'عدد الفرص المؤكدة التي بقيت نشطة في آخر جلسة مسجلة.':(Number.isFinite(Number(r.scanCandidates))?'الفرص المؤكدة النشطة فقط. آخر مسح: '+Number(r.scanCandidates).toLocaleString('ar-SA')+' مرشح، اجتاز الفلترة الأولية '+Number(r.scanPassed||0).toLocaleString('ar-SA')+'؛ لا تُحسب المرشحات فرصًا مؤكدة.':'عدد الفرص المؤكدة النشطة فقط، وليس جميع الأسهم المرشحة للتحليل.')},
  {id:'move',icon:'<svg viewBox="0 0 24 24" fill="none"><path d="M4 16l6-6 4 4 6-8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 6h6v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',label:'أعلى ارتفاع مسجل',value:pct(r.top_move_pct),detail:'أكبر نسبة ارتفاع ضمن الفرص المؤكدة النشطة فقط؛ قد تبقى فارغة إذا لم تجتز أي فرصة شروط الاعتماد.'},
  {id:'volume',icon:'<svg viewBox="0 0 24 24" fill="none"><path d="M4 19V9m5 10V5m5 14v-7m5 7V3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',label:'أعلى حجم تداول',value:r.top_volume?Number(r.top_volume).toLocaleString('en-US'):'—',detail:'أعلى حجم بين الفرص المؤكدة النشطة فقط. الحجم عدد الأسهم المتداولة وليس قيمة التداول بالدولار؛ وقد لا تتوفر قيمة عند عدم وجود فرص مؤكدة.'},
  {id:'signal',icon:'<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v5l3 2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',label:'وقت آخر إشارة',value:metricDateValid?'<span class="metric-date" dir="rtl"><bdi dir="rtl">'+metricDateLabel+'</bdi></span><span class="metric-time" dir="ltr"><bdi dir="ltr">'+metricTimeLabel+'</bdi></span>':'<span class="metric-date">لا توجد إشارة</span><span class="metric-time">بانتظار البيانات</span>',detail:metricDateValid?'تاريخ ووقت آخر إشارة سجلها الرادار حسب التوقيت المحلي للجهاز.':'لم يسجل النظام وقت إشارة صالحًا حتى الآن.'}
 ];
 document.getElementById('dashboardMetrics').innerHTML=metricItems.map(m=>'<button type="button" class="sas-metric-card" data-metric="'+m.id+'" aria-expanded="false" onclick="toggleSasMetric(\''+m.id+'\')"><span class="sas-metric-top"><span class="sas-metric-icon">'+m.icon+'</span><span class="sas-metric-label">'+m.label+'</span><span class="sas-metric-chevron">⌄</span></span><strong class="sas-metric-value">'+m.value+'</strong><span class="sas-metric-hint">اضغط للتوضيح</span></button>').join('')+'<div id="sasMetricDetail" class="sas-metric-detail" hidden aria-live="polite"></div>';
 window.sasMetricDetails=Object.fromEntries(metricItems.map(m=>[m.id,{label:m.label,detail:m.detail}]));
 renderRadarMovementSignals();
 const count=document.getElementById('sasRadarCount');if(count)count.textContent=Number(terminalState.radar.length||0).toLocaleString('ar-SA')+' فرصة';

 const el=document.getElementById('dashboardRadar');
 if(terminalState.radar.length) renderCards(el,terminalState.radar.slice(0,6));
 else if(Array.isArray(terminalState.preliminaryRadar)&&terminalState.preliminaryRadar.length) renderPreliminaryDashboardCards(el,terminalState.preliminaryRadar);
 else el.innerHTML='<div class="radar-empty-card dashboard-saved-empty">'+
  '<div class="radar-empty-orbit" aria-hidden="true"><span></span><i></i><b></b></div>'+
  '<div class="radar-empty-content">'+
   '<span class="radar-empty-kicker">SAS PRO · RADAR WATCH</span>'+
   '<h3>نترقّب الإشارة القادمة</h3>'+
   '<p>لا توجد إشارات محفوظة من آخر جلسة. شغّل الرادار للبحث عن الأسهم التي تحقق شروط الزخم والسيولة والتأكيد الفني، وستظهر الفرص هنا عند رصدها.</p>'+
   '<div class="dashboard-empty-tags"><span>⚡ الزخم</span><span>◉ السيولة</span><span>✓ تأكيد الشروط</span></div>'+
   '<div class="radar-empty-status"><span class="radar-empty-dot"></span><span>جاهز لفحص فرص جديدة</span></div>'+
   '<div class="radar-empty-actions"><button type="button" class="radar-empty-primary" onclick="runRadar()">⟳ ابدأ مسحًا جديدًا</button><button type="button" class="radar-empty-secondary" onclick="switchTerminalTab(\'radar\')">افتح صفحة الرادار ↗</button><button type="button" class="radar-empty-secondary dashboard-empty-help-btn" onclick="toggleRadarEmptyHelp(this)" aria-expanded="false">كيف يعمل؟ <span>⌄</span></button></div>'+
   '<div class="radar-empty-help" hidden>لا يعرض SAS PRO سهمًا لمجرد ارتفاعه؛ بل ينتظر تحقق شروط الرصد المعتمدة. إذا لم تظهر فرص، فهذا يعني أن النتائج الحالية لم تجتز الفلاتر بعد.</div>'+
  '</div></div>';
}


function renderPreliminaryDashboardCards(el,stocks){
 if(!el)return;
 const rows=(Array.isArray(stocks)?stocks:[]).filter(x=>x&&String(x.symbol||'').trim()).slice(0,3);
 if(!rows.length)return;
 const cards=rows.map(x=>{
  const sym=String(x.symbol||'—');
  const price=Number(x.live_price??x.price);
  const change=Number(x.live_change_pct??x.change_pct);
  const priceText=Number.isFinite(price)&&price>0?'$'+price.toLocaleString('en-US',{maximumFractionDigits:4}):'السعر غير متاح';
  const changeText=Number.isFinite(change)?(change>=0?'+':'')+change.toFixed(2)+'%':'التغير غير متاح';
  const cls=x.classification||{};
  const gate=cls.smart_levels_ict||cls.smart_levels||{};
  const reason=x.gate_reason||x.preliminary_reason||x.reason||gate.reason||(Array.isArray(gate.reasons)?gate.reasons.join('، '):'')||'مرشح أولي؛ لم يكتمل التأكيد النهائي بعد';
  return '<article class="candidate-card dashboard-preliminary-card"><div><b>'+escHtml(sym)+'</b><small>🟡 مرشح أولي — قيد التحقق</small></div><p>'+escHtml(priceText)+' · '+escHtml(changeText)+'</p><p class="candidate-reason">'+escHtml(String(reason))+'</p><button type="button" data-symbol="'+escHtml(sym)+'" onclick="openSymbol(this.dataset.symbol)">عرض التحليل</button></article>';
 }).join('');
 el.innerHTML='<div class="candidate-title dashboard-preliminary-title"><b>🔎 بطاقات الرصد الأولي</b><small>مرشحون فعليون من نتيجة المسح، وليسوا فرصًا مؤكدة. لا تُرسل القناة إلا الإشارات التي تجتاز شروط النشر.</small></div><div class="candidate-grid dashboard-preliminary-grid">'+cards+'</div>';
}

function renderRadarMovementSignals(){
 const el=document.getElementById('liveMovers');
 if(!el)return;
 const stocks=(Array.isArray(terminalState.radar)?terminalState.radar:[])
  .filter(x=>x&&String(x.symbol||x.ticker||'').trim())
  .map(x=>({...x,_move:Number(x.live_change_pct??x.change_pct??x.change_percent??x.day_change_pct??x.change)}))
  .filter(x=>{
   const exchange=String(x.exchange||'').toUpperCase();
   const usEquity=['NASDAQ','NYSE','AMEX','US'].includes(exchange);
   return Number.isFinite(x._move)&&x._move>0&&Number(x.live_price??x.price??x.entry_price)>0&&usEquity;
  })
  .sort((a,b)=>b._move-a._move || Number(b.opening_opportunity_score||0)-Number(a.opening_opportunity_score||0))
  .slice(0,12);
 const pct=v=>(v>0?'+':'')+v.toFixed(2)+'%';
 const price=v=>{const n=Number(v);return Number.isFinite(n)&&n>0?'$'+n.toFixed(2):'—';};
 const conditionTags=x=>{
  const c=x.radar_checks||{},cl=x.classification||{},tags=[];
  if(c.momentum_label)tags.push('الزخم: '+c.momentum_label);
  else if(c.momentum)tags.push('زخم مرصود');
  if(c.sas_core)tags.push('اجتاز SAS Core');
  if(c.liquidity)tags.push('السيولة مستوفاة');
  if(c.rvol)tags.push('RVOL '+(c.momentum_rvol!=null?Number(c.momentum_rvol).toFixed(2)+'×':'متحقق'));
  if(c.target)tags.push('هدف فني مؤكد');
  else if(c.target_status)tags.push(c.target_status);
  if(c.advanced_confirmation)tags.push('تأكيد فني متقدم');
  if(c.no_distribution)tags.push('لا توجد علامة توزيع');
  if(c.no_chase)tags.push('دون مطاردة سعرية');
  const structure=c.market_structure||cl.market_structure;
  if(structure&&typeof structure==='string')tags.push('البنية: '+structure);
  const fib=c.fibonacci_zone||cl.fibonacci?.zone;
  if(fib&&typeof fib==='string')tags.push('فيبوناتشي: '+fib);
  const fvg=c.fvg;
  if(fvg&&typeof fvg==='string')tags.push('FVG: '+fvg);
  const divergence=c.rsi_divergence;
  if(divergence&&typeof divergence==='string'&&!['none','لا يوجد','محايد'].includes(divergence.toLowerCase()))tags.push('RSI: '+divergence);
  return tags.slice(0,6);
 };
 if(!stocks.length){
  el.innerHTML='<div class="sas-radar-movers-empty"><span>⌁</span><b>لا توجد أسهم رابحة رصدها النظام في آخر دورة</b><small>يعرض هذا القسم الأسهم الأمريكية الرابحة التي سجلها الرادار في الجلسة الحالية، بما فيها المرشحات تحت المراقبة. لن تُعرض قائمة مستقلة أو بيانات مخمّنة.</small></div>';
  return;
 }
 el.innerHTML=stocks.map((x,index)=>{
  const sym=String(x.symbol||x.ticker||'—'),safe=escHtml(sym);
  const change=x._move,positive=change>=0;
  const px=price(x.live_price??x.price??x.entry_price);
  const volume=Number(x.volume),rvol=x.radar_checks?.momentum_rvol??x.classification?.rvol;
  const score=Number(x.opening_opportunity_score);
  const tags=conditionTags(x);
  return '<button type="button" class="sas-radar-mover-card" data-symbol="'+safe+'" aria-label="فتح تحليل '+safe+'">'+
   '<span class="sas-radar-mover-rank">'+String(index+1).padStart(2,'0')+'</span>'+
   '<span class="sas-radar-mover-main"><span class="sas-radar-mover-heading"><b>'+safe+'</b><em class="'+(positive?'is-up':'is-down')+'">'+pct(change)+'</em></span>'+
   '<span class="sas-radar-mover-meta"><span>السعر <b>'+px+'</b></span>'+
   (Number.isFinite(volume)&&volume>0?'<span>الحجم <b>'+volume.toLocaleString('en-US',{maximumFractionDigits:0})+'</b></span>':'')+
   (Number.isFinite(Number(rvol))&&Number(rvol)>0?'<span>RVOL <b>'+Number(rvol).toFixed(2)+'×</b></span>':'')+
   (Number.isFinite(score)?'<span>قوة الفرصة <b>'+score.toFixed(0)+'/100</b></span>':'')+'</span>'+
   '<span class="sas-radar-condition-tags">'+(tags.length?tags.map(t=>'<span>'+escHtml(t)+'</span>').join(''):'<span>اجتاز بوابة الرادار</span>')+'</span></span>'+
   '<span class="sas-radar-mover-open">تحليل السهم <b>↗</b></span></button>';
 }).join('');
 el.querySelectorAll('.sas-radar-mover-card').forEach(btn=>btn.addEventListener('click',()=>openRadarMoverAnalysis(btn.dataset.symbol)));
}
function openRadarMoverAnalysis(symbol){
 switchTerminalTab('search');
 const input=document.getElementById('symbolSearch')||document.getElementById('searchSymbol')||document.querySelector('[data-symbol-search]');
 if(input){input.value=symbol;input.dispatchEvent(new Event('input',{bubbles:true}));}
 if(typeof window.analyzeSymbol==='function')window.analyzeSymbol(symbol);
}

function toggleSasMetric(id){
 const panel=document.getElementById('sasMetricDetail');
 if(!panel||!window.sasMetricDetails||!window.sasMetricDetails[id])return;
 const wasOpen=panel.dataset.active===id&&!panel.hidden;
 document.querySelectorAll('.sas-metric-card').forEach(b=>{const active=b.dataset.metric===id&&!wasOpen;b.classList.toggle('is-active',active);b.setAttribute('aria-expanded',String(active));});
 if(wasOpen){panel.hidden=true;panel.dataset.active='';panel.innerHTML='';return;}
 const item=window.sasMetricDetails[id];
 panel.dataset.active=id;
 panel.replaceChildren();
 const icon=document.createElement('span');icon.className='sas-metric-detail-icon';icon.textContent='ⓘ';
 const body=document.createElement('div');
 const title=document.createElement('strong');title.textContent=item.label;
 const detail=document.createElement('p');detail.textContent=item.detail;
 body.append(title,detail);
 const close=document.createElement('button');close.type='button';close.setAttribute('aria-label','إغلاق التوضيح');close.textContent='×';close.addEventListener('click',()=>toggleSasMetric(id));
 panel.append(icon,body,close);panel.hidden=false;
}
function renderPerpEquitiesMovers(data){
 const sourceLink=document.querySelector('.sas-movers-source');
 if(sourceLink){sourceLink.hidden=false;sourceLink.removeAttribute('aria-hidden');sourceLink.href='https://finviz.com/screener?v=111&f=exch_nasd%2Csh_curvol_o100%2Csh_float_u50%2Csh_relvol_o0.5%2Cta_perf_1wup&ft=4';sourceLink.textContent='فلاتر Finviz ↗';}

 const quickLinks=[
  ['أعلى الرابحين اليوم','https://finviz.com/screener?v=111&f=exch_nasd,sh_price_o0.3,sh_price_u15&s=ta_topgainers'],
  ['أعلى الخاسرين اليوم','https://finviz.com/screener?v=111&f=exch_nasd,sh_price_o0.3,sh_price_u15&s=ta_toplosers'],
  ['حجم غير اعتيادي','https://finviz.com/screener?v=111&f=exch_nasd,sh_price_o0.3,sh_price_u15&s=ta_unusualvolume'],
  ['Float منخفض','https://finviz.com/screener?v=111&f=exch_nasd,sh_price_o0.3,sh_price_u15,sh_float_u50,sh_curvol_o100']
 ];
 const title=document.querySelector('.sas-perp-movers-panel .sas-insight-title');
 if(title&&!document.getElementById('sasFinvizQuickLinks')){
  const links=document.createElement('nav');links.id='sasFinvizQuickLinks';links.className='sas-finviz-quick-links';links.setAttribute('aria-label','فلاتر Finviz');
  quickLinks.forEach(([label,url])=>{const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=label+' ↗';links.appendChild(a);});
  title.insertAdjacentElement('afterend',links);
 }

 const updated=document.getElementById('perpMoversUpdated'),state=document.getElementById('perpMoversState');
 const gainers=document.getElementById('perpMoversGainers'),losers=document.getElementById('perpMoversLosers');
 const gainersCount=document.getElementById('perpMoversGainersCount'),losersCount=document.getElementById('perpMoversLosersCount');
 if(!gainers||!losers||!data)return;
 const assets=Array.isArray(data.assets)?data.assets:[];
 terminalState.perpAssets=Object.fromEntries(assets.map(x=>[String(x?.symbol||'').toUpperCase(),x]));
 const fmtUsd=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4}):'—';
 if(!data.available){
  if(gainersCount)gainersCount.textContent='0 سهم';if(losersCount)losersCount.textContent='0 سهم';
  const empty=escHtml(data.message_ar||'لا توجد بيانات أسعار ضمن النطاق المحدد حاليًا.');
  gainers.innerHTML='<div class="sas-perp-movers-empty">'+empty+'</div>';losers.innerHTML='<div class="sas-perp-movers-empty">'+empty+'</div>';
  if(updated)updated.textContent='بانتظار تحديث الأسعار';
  if(state)state.textContent='نطاق السعر $0.30–$15.00 • بيانات السوق';
  return;
 }
 const makeRows=(items,direction,emptyLabel)=>items.length?items.map((item,index)=>{
  const value=Number(item.change_pct),change=(value>0?'+':'')+value.toFixed(2)+'%';
  const symbol=escHtml(String(item.symbol||'—')),name=escHtml(String(item.name||'اسم الشركة غير متوفر'));
  const price=fmtUsd(item.price),volume=fmtUsd(item.volume);
  const link='https://finviz.com/quote.ashx?t='+encodeURIComponent(String(item.symbol||''));
  return '<details class="sas-perp-mover-card '+direction+'"><summary class="sas-perp-mover"><span class="sas-perp-mover-rank">'+(index+1)+'</span><span class="sas-perp-mover-main"><strong class="sas-perp-mover-symbol">'+symbol+'</strong><small>'+name+'</small></span><span class="sas-perp-mover-price">'+price+'</span><b>'+change+'</b><span class="sas-perp-mover-open">⌄</span></summary><div class="sas-perp-mover-details"><div class="sas-perp-detail-grid"><div><small>السعر</small><b>'+price+'</b></div><div><small>التغير اليومي</small><b>'+change+'</b></div><div><small>حجم التداول</small><b>'+volume+'</b></div><div><small>الحالة</small><b>للمراجعة</b></div></div><a href="'+link+'" target="_blank" rel="noopener noreferrer">فتح تحليل السهم في Finviz ↗</a></div></details>';
 }).join(''):'<div class="sas-perp-movers-empty">'+emptyLabel+'</div>';
 const gainersList=(Array.isArray(data.gainers)?data.gainers:[]).filter(item=>Number(item?.change_pct)>0),losersList=(Array.isArray(data.losers)?data.losers:[]).filter(item=>Number(item?.change_pct)<0);
 gainers.innerHTML=makeRows(gainersList,'is-up','لا توجد أسهم رابحة مستوفية للفلاتر الآن');
 losers.innerHTML=makeRows(losersList,'is-down','لا توجد أسهم خاسرة مستوفية للفلاتر الآن');
 if(gainersCount)gainersCount.textContent=gainersList.length.toLocaleString('ar-SA-u-nu-latn')+' سهم';
 if(losersCount)losersCount.textContent=losersList.length.toLocaleString('ar-SA-u-nu-latn')+' سهم';
 if(updated&&data.updated_at){const date=new Date(data.updated_at);updated.textContent='آخر تحديث: '+(Number.isFinite(date.getTime())?new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{timeZone:'Asia/Riyadh',hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit',hour12:true}).format(date)+' بتوقيت الرياض':'—');}
 else if(updated)updated.textContent='وقت التحديث غير متاح';
 if(state)state.textContent=!['premarket','regular','afterhours','night'].includes(data.market_session)?'الرادار ينتظر جلسة الرصد التالية':'مصادر بيانات السوق • $0.30–$15.00';
}
function renderMacro(){
 const wanted=[
  {label:'S&P 500',symbols:['SPX','^GSPC','SPY']},
  {label:'NASDAQ',symbols:['IXIC','^IXIC','QQQ']},
  {label:'DOW JONES',symbols:['DJI','^DJI','DIA']},
  {label:'BTC',symbols:['BTC/USD','BTCUSD','BTCUSDT']},
  {label:'GOLD',symbols:['XAU/USD','XAUUSD','GCUSD','GLD']}
 ];
 const rows=wanted.map(item=>{
  const found=terminalState.ticker.find(x=>{
   const label=String(x?.label||'').toUpperCase();
   const symbol=String(x?.symbol||'').toUpperCase();
   return label===item.label.toUpperCase() || item.symbols.some(s=>s.toUpperCase()===symbol);
  });
  if(found && Number(found.price)>0){
   const validQuote={...found,label:item.label};
   // Keep the last real quote locally so a temporary provider delay never blanks the card.
   try{localStorage.setItem('saspro_macro_'+item.label.replace(/\\s+/g,'_'),JSON.stringify({price:validQuote.price,change_pct:validQuote.change_pct,source:validQuote.source,updated_at:Date.now()}));}catch(e){}
   return validQuote;
  }
  // Prefer a previously verified quote while the data provider refreshes.
  try{
   const cached=JSON.parse(localStorage.getItem('saspro_macro_'+item.label.replace(/\\s+/g,'_'))||'null');
   if(cached && Number(cached.price)>0)return {...(found||{}),label:item.label,price:cached.price,change_pct:cached.change_pct,source:'آخر سعر محفوظ • بانتظار التحديث',stale:true,diagnostic:null};
  }catch(e){}
  return found?{...found,label:item.label,source:found.source==='unavailable'?'تعذر جلب السعر':'جارٍ تحديث الأسعار'}:{label:item.label,price:null,change_pct:null,source:'جارٍ طلب السعر من مزود البيانات',diagnostic:'quote_refresh_in_progress'};
 }).filter(Boolean);
 const cards=rows.map(x=>{
  const valid=Number(x.price)>0;
  const labelKey=String(x.label||'').toLowerCase();
  const iconSlots={'btc':'icon-bitcoin','gold':'icon-gold','s&p 500':'icon-sp500','nasdaq':'icon-nasdaq','dow jones':'icon-dow-jones'};
  const iconSlot=iconSlots[labelKey]||null;
  const iconFallback=labelKey==='btc'?'₿':labelKey==='gold'?'🪙':labelKey==='s&p 500'?'📈':labelKey==='nasdaq'?'💹':labelKey==='dow jones'?'🏛️':'◈';
  const iconUrl=iconSlot?terminalState.siteMedia?.[iconSlot]?.url:null;
  const iconHtml='<span class="macro-card-icon" aria-hidden="true">'+(iconUrl?'<img src="'+esc(iconUrl)+'" alt="">':iconFallback)+'</span>';
  const changeValid=x.change_pct!==null&&x.change_pct!==undefined&&Number.isFinite(Number(x.change_pct));
  const changeText=changeValid?pct(Number(x.change_pct)):'—';
  const changeClass=changeValid?(Number(x.change_pct)>=0?'up':'down'):'neutral';
  const sourceText=x.stale?'آخر سعر محفوظ • تحقق من وقت التحديث':(valid?(x.source||'مزود البيانات'):(x.source==='unavailable'?'تعذر جلب السعر':'جارٍ تحديث الأسعار'));
  return '<button type="button" class="macro-card '+(valid?'':'macro-unavailable')+(x.stale?' macro-stale':'')+'" data-macro-label="'+labelKey+'" aria-label="تفاصيل '+escHtml(x.label)+'" title="اضغط لعرض تفاصيل '+escHtml(x.label)+'">'+iconHtml+'<span class="macro-card-label">'+escHtml(x.label)+'</span><b>'+ (valid?money(x.price):'غير متاح') +'</b><em class="'+changeClass+'">'+changeText+'</em><small class="macro-source">'+escHtml(sourceText)+'</small></button>';
 }).join('');
 document.getElementById('macroGrid').innerHTML=cards || '<div class="empty-state">لا تتوفر بيانات السوق حاليًا.</div>';
 // Keep the news/index ribbon synchronized as soon as the ticker response paints.
 window.__sasLastTicker=Array.isArray(terminalState.ticker)?terminalState.ticker:[];
 window.dispatchEvent(new CustomEvent('saspro:ticker-updated',{detail:{ticker:window.__sasLastTicker}}));
 if(!window.__sasMacroCardInteraction){
  window.__sasMacroCardInteraction=true;
  document.addEventListener('click',function(event){
   const macroCard=event.target.closest('.macro-card[data-macro-label]');
   const key=macroCard?.dataset.macroLabel;
   if(!key)return;
   const target=Array.from(document.querySelectorAll('.macro-card[data-macro-label]')).find(el=>el.dataset.macroLabel===key);
   document.querySelectorAll('.macro-card.is-selected').forEach(el=>el.classList.remove('is-selected'));
   if(target){target.classList.add('is-selected');target.scrollIntoView({behavior:'smooth',block:'center'});window.setTimeout(()=>target.classList.remove('is-selected'),2200);}
  });
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
  if(button){button.disabled=true;button.classList.add('is-scanning');button.setAttribute('aria-busy','true');button.innerHTML='<span class="radar-scan-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><circle cx="14" cy="14" r="9.5" stroke="currentColor" stroke-width="1.8"/><circle cx="14" cy="14" r="4.5" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 2"/><path d="M14 4.5v4M23.5 14h-4M14 23.5v-4M4.5 14h4M21 21l6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M14 14 20.5 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></span><span class="radar-scan-copy"><b>جارٍ المسح</b><small>نفحص الأسهم الآن</small></span><span class="radar-scan-arrow" aria-hidden="true">‹</span>';}
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
   if(!existing&&grid)grid.innerHTML='<div class="radar-empty-card radar-scanning-empty">'+
    '<div class="radar-empty-orbit" aria-hidden="true"><span></span><i></i><b></b></div>'+
    '<div class="radar-empty-content">'+
     '<span class="radar-empty-kicker">SAS PRO · LIVE SCAN</span>'+
     '<h3>الرادار يعمل الآن</h3>'+
     '<p>نراجع الأسهم الأمريكية ونختبر الحركة والسيولة والزخم والشروط الفنية. ستظهر هنا فقط الأسهم التي تجتاز شروط الرادار، ولن نحذف النتائج السابقة أثناء الفحص.</p>'+
     '<div class="dashboard-empty-tags"><span>⚡ تحليل الزخم</span><span>◉ فحص السيولة</span><span>✓ تأكيد فني</span></div>'+
     '<div class="radar-empty-status"><span class="radar-empty-dot"></span><span>المسح جارٍ — ننتظر النتائج المطابقة</span></div>'+
     '<div class="radar-empty-actions"><button type="button" class="radar-empty-primary" onclick="document.getElementById(\'radarScanProgress\')?.scrollIntoView({behavior:\'smooth\',block:\'center\'})">عرض تقدم المسح ↓</button><button type="button" class="radar-empty-secondary" onclick="toggleRadarEmptyHelp(this)" aria-expanded="false">ما الذي يفحصه؟ <span>⌄</span></button></div>'+
     '<div class="radar-empty-help" hidden>يفحص الرادار المرشحين ثم يطبق شروط السعر والسيولة والحركة والزخم والتأكيد الفني. قد يستغرق الفحص وقتًا؛ ولا يعني عدم ظهور سهم أثناء الفحص وجود عطل.</div>'+
    '</div></div>';
  }else if(showcase){
   const actions=showcase.querySelector('.sas-radar-actions');
   if(actions)actions.before(progress);
   else showcase.append(progress);
  }
  elapsedTimer=setInterval(()=>{
   const elapsed=Math.floor((Date.now()-startedAt)/1000);
   const label=progress?.querySelector('.radar-scan-elapsed');
   if(label)label.textContent='⏱️ '+elapsed+' ث · يستمر الرصد في الخلفية';
  },1000);
  // Start or join one shared background scan; polling short status calls avoids the old 180s request timeout.
  const started=await api('/api/radar/scan/start',{method:'POST',timeoutMs:20000,retries:1});
  let jobId=String(started.job_id||'');
  let autoRecoveries=0;
  let d=started.status==='completed'?(started.result||{}):null;
  if(!d){
   if(!jobId)throw new Error('لم يُرجع الخادم معرّف مهمة الرصد.');
   let consecutivePollErrors=0;
   while(true){
    await sleep(2000);
    let job;
    try{
     job=await api('/api/radar/scan/status?job_id='+encodeURIComponent(jobId),{timeoutMs:30000,retries:1});
     consecutivePollErrors=0;
    }catch(pollError){
     consecutivePollErrors++;
     const stage=progress?.querySelector('.radar-scan-stage');
     if(stage)stage.textContent='تأخر رد الخادم؛ نحاول الاتصال مجددًا دون إلغاء المسح ('+consecutivePollErrors+')…';
     console.warn('SAS PRO radar status poll delayed:',pollError?.message||pollError);
     // A slow scan can briefly delay status responses. Do not report a failed
     // scan merely because one or several polling requests timed out.
     if(consecutivePollErrors<15)continue;
     throw new Error('تأخر رد الخادم أكثر من المتوقع. لم نلغِ مهمة الرصد، واحتفظنا بالنتائج السابقة؛ أعد فتح الرادار بعد قليل للتحقق من النتيجة.');
    }
    const serverProgress=job.progress||{};
    const stage=progress?.querySelector('.radar-scan-stage');
    if(stage&&serverProgress.message_ar)stage.textContent=serverProgress.message_ar;
    if(stage&&Number(serverProgress.total)>0){
     stage.textContent=String(serverProgress.message_ar||'تحليل المرشحين')+' ('+Number(serverProgress.done||0)+'/'+Number(serverProgress.total)+')';
    }
    if(job.status==='running')continue;
    if(job.status==='success'){d=job.result||{};break;}
    if(job.status==='failed')throw new Error(job.error?.message||'تعذر إكمال مسح الأسهم.');
    if(job.status==='not_found'){
     // Recover once automatically if a worker restart lost the in-memory job state.
     // The backend start endpoint joins an active scan, avoiding a second manual search.
     if(autoRecoveries<1){
      autoRecoveries++;
      if(stage)stage.textContent='نعيد ربط مهمة الرصد تلقائيًا…';
      const recovered=await api('/api/radar/scan/start',{method:'POST',timeoutMs:20000,retries:1});
      if(recovered.status==='completed'){d=recovered.result||{};break;}
      jobId=String(recovered.job_id||'');
      if(!jobId)throw new Error('تعذر استعادة حالة الرصد تلقائيًا. احتفظنا بالنتائج السابقة؛ حاول لاحقًا.');
      consecutivePollErrors=0;
      continue;
     }
     throw new Error('تعذر استعادة حالة مهمة الرصد تلقائيًا. احتفظنا بالنتائج السابقة؛ حاول لاحقًا.');
    }
    throw new Error('حالة مهمة الرصد غير معروفة. النتائج السابقة محفوظة؛ أعد المحاولة.');
   }
  }
  if(d?.error){
    const reason=String(d.error.message||d.error.type||'خطأ غير معروف');
    if(show&&grid&&!terminalState.radar.length)grid.innerHTML='<div class="fatal"><b>🔴 تعذر إكمال مسح الأسهم</b><br><small>'+escHtml(reason)+'</small><br><small>تم الاحتفاظ بآخر نتائج الرصد إن وُجدت.</small></div>';
    if(copyText)copyText.textContent='تعذر إكمال المسح. يمكنك إعادة المحاولة؛ احتفظنا بالنتائج السابقة.';
    if(statusText)statusText.textContent='تعذر المسح';
    console.error('SAS PRO radar API error:',d.error);
    return;
  }
  if(d.enabled===false&&(!Array.isArray(d.stocks)||d.stocks.length===0)){
    // Do not present a previous scan as if it were current when live radar is disabled.
    terminalState.radar=[];
    try{localStorage.setItem('saspro_radar_snapshot','[]');}catch(e){}
    if(copyText)copyText.textContent=String(d.reason||'الرصد الحي غير متاح حاليًا؛ لا توجد فرص حالية مؤكدة.');
    if(statusText)statusText.textContent='لا توجد فرص حالية';
    renderRadar();
    return;
  }
  terminalState.radar=d.stocks||[];
  try{localStorage.setItem('saspro_radar_snapshot',JSON.stringify(terminalState.radar));}catch(e){console.warn('SAS PRO radar snapshot not cached:',e?.message||e);}
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
  const preliminary=Array.isArray(d.preliminary_stocks)?d.preliminary_stocks:[];
  terminalState.preliminaryRadar=preliminary.filter(x=>x&&String(x.symbol||'').trim()).slice(0,12);
  if(!terminalState.preliminaryRadar.length)terminalState.preliminaryRadar=[];
  const candidates=Array.isArray(diag.filtered_examples)?diag.filtered_examples:[];
  if(candidateEl && preliminary.length){
    candidateEl.hidden=false;
    candidateEl.innerHTML='<div class="candidate-title"><b>🔎 الرصد الأولي — '+preliminary.length.toLocaleString('ar-SA')+' مرشح</b><small>هذه نتائج أولية قيد التحقق وليست فرصًا مؤكدة أو توصيات. إذا اجتازت بوابة التأكيد والنشر ستظهر ضمن بطاقات الفرص وتُرسل للقناة حسب الإعدادات.</small></div>'+
      '<div class="candidate-grid">'+preliminary.slice(0,12).map(x=>{
        const sym=String(x.symbol||'—');
        const price=Number(x.live_price??x.price);
        const change=Number(x.live_change_pct??x.change_pct);
        const priceText=Number.isFinite(price)&&price>0?'$'+price.toLocaleString('en-US',{maximumFractionDigits:4}):'السعر غير متاح';
        const changeText=Number.isFinite(change)?(change>=0?'+':'')+change.toFixed(2)+'%':'التغير غير متاح';
        return '<article class="candidate-card"><div><b>'+escHtml(sym)+'</b><small>مرشح أولي • '+escHtml(String(x.source||x.data_source||'مصدر الرصد'))+'</small></div><p>'+escHtml(priceText)+' · '+escHtml(changeText)+'</p><button type="button" data-symbol="'+escHtml(sym)+'" onclick="openSymbol(this.dataset.symbol)">عرض التحليل</button></article>';
      }).join('')+'</div>';
  }else if(candidateEl && candidates.length){
    candidateEl.hidden=false;
    candidateEl.innerHTML='<div class="candidate-title"><b>🔎 تحليل المرشحين المستبعدين</b><small>هذه الأسهم لم تجتز شروط الإشارة النهائية؛ سبب الاستبعاد ظاهر لكل سهم.</small></div>'+
      '<div class="candidate-grid">'+candidates.slice(0,12).map(x=>{
        const sym=String(x.symbol||'—');
        return '<article class="candidate-card"><div><b>'+escHtml(sym)+'</b><small>'+escHtml(x.data_source||'مصدر الرصد')+'</small></div><p>'+escHtml(x.reason||'تم استبعاده في مرحلة لاحقة')+'</p><button type="button" data-symbol="'+escHtml(sym)+'" onclick="openSymbol(this.dataset.symbol)">عرض التحليل</button></article>';
      }).join('')+'</div>';
  }else if(candidateEl){
    candidateEl.hidden=true;
    candidateEl.innerHTML='';
  }
  renderRadar();
  renderDashboard({radar:{enabled:d.enabled,opportunities:terminalState.radar.length,scanCandidates:Number.isFinite(Number(diag.candidates))?Number(diag.candidates):undefined,scanPassed:Number.isFinite(Number(diag.passed))?Number(diag.passed):undefined,top_move_pct:Math.max(...terminalState.radar.map(x=>Number(x.change_pct)||-Infinity)),top_volume:Math.max(...terminalState.radar.map(x=>Number(x.volume)||-Infinity))}});
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
   if(show&&grid&&!terminalState.radar.length)grid.innerHTML='<div class="fatal"><b>تعذر إكمال مسح الأسهم</b><br>'+escHtml(msg||'حدث خطأ غير معروف')+'<br><small>يمكنك إعادة المحاولة؛ لم تُحذف نتائج الرصد السابقة.</small></div>';
   if(copyText)copyText.textContent='حدث خطأ أثناء المسح. احتفظنا بالنتائج السابقة؛ حاول مرة أخرى.';
   if(statusText)statusText.textContent='حدث خطأ في المسح';
 }finally{
   if(elapsedTimer)clearInterval(elapsedTimer);
   progress?.remove();
   if(button){button.disabled=false;button.classList.remove('is-scanning');button.removeAttribute('aria-busy');button.innerHTML='<span class="radar-scan-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><circle cx="14" cy="14" r="9.5" stroke="currentColor" stroke-width="1.8"/><circle cx="14" cy="14" r="4.5" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 2"/><path d="M14 4.5v4M23.5 14h-4M14 23.5v-4M4.5 14h4M21 21l6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M14 14 20.5 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></span><span class="radar-scan-copy"><b>مسح الأسهم</b><small>ابدأ فحص الفرص الآن</small></span><span class="radar-scan-arrow" aria-hidden="true">‹</span>';}
   scanButtons.forEach(b=>{b.disabled=false;b.textContent=b.dataset.originalText||'⟳ ابدأ مسحًا جديدًا';delete b.dataset.originalText;});
   if(radarVisual)radarVisual.disabled=false;
   terminalState.radarScanning=false;
   document.querySelectorAll('.sas-radar-showcase,.sas-radar-visual,.terminal-page').forEach(el=>el.classList.remove('sas-radar-scanning'));
 }
}
function renderRadar(){renderCards(document.getElementById('radarGrid'),terminalState.radar);}
function renderCards(el,rows){
 if(!el)return;
 const all=Array.isArray(rows)?rows:[];
 const isConfirmed=x=>(x.opportunity_status||x.classification?.opportunity_status)==='confirmed'||x.radar_tier==='confirmed';
 const isPreliminary=x=>{
  if(isConfirmed(x))return false;
  const gate=x.channel_gate||x.technical?.channel_gate||{};
  const status=String(x.radar_candidate_status||x.candidate_status||'');
  return gate.passed===true||status.includes('اجتاز بوابة النشر الفنية');
 };
 const confirmed=all.filter(isConfirmed);
 const preliminary=all.filter(isPreliminary);
 const reserved=new Set([...confirmed,...preliminary].map(x=>String(x.symbol||'').toUpperCase()));
 const watch=all.filter(x=>!reserved.has(String(x.symbol||'').toUpperCase()));
 const empty='<div class="radar-empty-card">'+
   '<div class="radar-empty-orbit" aria-hidden="true"><span></span><i></i><b></b></div>'+
   '<div class="radar-empty-content">'+
    '<span class="radar-empty-kicker">SAS PRO · SMART RADAR</span>'+
    '<h3>لا توجد فرص مكتملة حاليًا</h3>'+
    '<p>لم يجتز أي سهم جميع شروط الفرصة المؤكدة في هذه الدورة. سيعرض الرادار المرشحين تحت المراقبة عند توفر بيانات فنية موثوقة.</p>'+
    '<div class="radar-empty-status"><span class="radar-empty-dot"></span><span>بانتظار فرصة تستوفي الشروط</span></div>'+
    '<div class="radar-empty-actions"><button type="button" class="radar-empty-primary" onclick="runRadar()">⟳ ابدأ مسحًا جديدًا</button><button type="button" class="radar-empty-secondary" onclick="toggleRadarEmptyHelp(this)" aria-expanded="false">كيف يعمل الرادار؟ <span>⌄</span></button></div>'+
    '<div class="radar-empty-help" hidden>الفرصة المؤكدة تحتاج اجتياز الاستراتيجية وتوفر سعر حي ومستويات فنية صالحة ونسبة عائد إلى مخاطرة لا تقل عن 1.5. المرشح تحت المراقبة ليس توصية دخول.</div>'+
   '</div></div>';
 if(!all.length){el.innerHTML=empty;return;}
 const heading=(title,count,description)=>'<div class="radar-list-heading" style="grid-column:1/-1;width:100%;box-sizing:border-box"><b>'+title+'</b><span>'+count.toLocaleString('ar-SA')+' سهم</span><small>'+description+'</small></div>';
 const group=(title,count,description,items,status)=>items.length?'<section class="radar-category" style="grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:stretch;gap:10px;margin:0 0 18px;width:100%;min-width:0;box-sizing:border-box">'+heading(title,count,description)+items.map(x=>stockCard({...x,_radarDisplayStatus:status})).join('')+'</section>':'';
 const confirmedHtml=group('🟢 الفرص المؤكدة',confirmed.length,'اجتازت شروط الرادار الكاملة؛ راجع السعر والمستويات قبل اتخاذ القرار.',confirmed,'confirmed');
 const preliminaryHtml=group('🟡 الفرص المجتازة مبدئيًا',preliminary.length,'اجتازت بوابة الفحص الأولي فقط؛ لا تعد فرصة مؤكدة حتى تكتمل الشروط.',preliminary,'preliminary');
 const watchHtml=group('🔵 أسهم تحت المراقبة',watch.length,'مرشحة للتحليل والمتابعة ولم يثبت اجتيازها للبوابة الأولية.',watch,'watch');
 el.innerHTML=confirmedHtml+preliminaryHtml+watchHtml+(confirmed.length||preliminary.length||watch.length?'':empty);

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
 const displayStatus=x._radarDisplayStatus||((x.opportunity_status||cls.opportunity_status)==='confirmed'||x.radar_tier==='confirmed'?'confirmed':'watch');
 const statusBadge=displayStatus==='confirmed'?'<i class="radar-stock-status is-confirmed">فرصة مؤكدة</i>':displayStatus==='preliminary'?'<i class="radar-stock-status is-preliminary">اجتياز مبدئي</i>':'<i class="radar-stock-status is-watch">تحت المراقبة</i>';
 const preliminaryReason=String(x.channel_gate?.reason||x.radar_candidate_status||x.candidate_status||x.preliminary_reason||'اجتاز الفحص الأولي؛ بانتظار اكتمال شروط التأكيد').slice(0,220);
 const preliminaryPanel=displayStatus==='preliminary'?'<div class="sas-preliminary-reason"><b>سبب التصنيف الأولي</b><span>'+escHtml(preliminaryReason)+'</span><small>تصنيف أولي فقط — ليس توصية دخول ولا فرصة مؤكدة.</small></div>':'';
 return '<article class="stock-card '+(displayStatus==='watch'?'radar-watch-card':displayStatus==='preliminary'?'radar-preliminary-card':'radar-confirmed-card')+'"><div class="stock-head"><div><b>'+s+'</b><small>'+statusBadge+' '+(section==='large'?'سهم كبير / متوسط':'سهم صغير')+(x.created_at?' • '+formatTime(x.created_at):'')+'</small></div><span class="'+(Number(change)>=0?'up':'down')+'">'+pct(change)+'</span></div>'+preliminaryPanel+
  '<div class="sas-card-price-trend"><svg class="sas-card-uptrend" viewBox="0 0 180 48" role="img" aria-label="مؤشر بصري صاعد وليس بيانات شارت فعلية"><path d="M4 40 C18 39 20 25 34 28 S52 39 66 23 S88 27 103 18 S126 28 140 12 S161 14 176 3" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/></svg><strong>$'+money(price)+'</strong></div>'+
 '<div class="stock-levels"><span>دخول <b>$'+money(price)+'</b></span><span>وقف <b>$'+money(stop)+'</b></span><span>هدف 1 <b>$'+money(target)+'</b></span></div>'+
 '<div class="stock-meta"><span>RVOL '+(Number.isFinite(rvol)&&rvol>0?rvol.toFixed(2):'—')+'×</span><span>حجم '+(Number.isFinite(volume)&&volume>0?volume.toLocaleString('en-US'):'—')+'</span><span>R:R '+(rr!=null?Number(rr).toFixed(2):'—')+(warning?' ⚠️':'')+'</span></div>'+
 '<div class="stock-gates">'+gate(gates.sas_core!==false,'SAS Core')+gate(gates.liquidity!==false,'السيولة')+gate(gates.target!==false,'الهدف')+gate(gates.live_levels!==false,'المستويات')+'</div>'+
 ' <button type="button" title="اضغط للتحقق من شرعية السهم" aria-label="تحقق من شرعية '+s+'" class="stock-shariah stock-shariah-action '+shClass+'" style="min-height:30px;padding:5px 8px;gap:6px" onclick="event.stopPropagation();openShariah(\''+raw+'\')"><span class="stock-shariah-icon-label"><span class="stock-shariah-icon" aria-hidden="true">🕌</span><b>تحقق الشرعية</b></span><strong style="font-size:10px">'+(shClass==='sh-unknown'?'تحقق':escHtml(shLabel))+'</strong></button>'+

 '<div class="stock-summary">'+(Number.isFinite(score)?'<span>⭐ قوة '+score.toFixed(0)+'/100</span>':'')+'<span>'+catalystLabel+(catalyst>0?' '+catalyst+'/100':'')+'</span>'+(x.live_price_source?'<span>📡 '+escHtml(x.live_price_source)+'</span>':'')+'</div>'+
 perpContext+
 '<div class="stock-ai">'+escHtml(ai.key_takeaway||ai.headline_summary||cls.reason||'تحليل AI يظهر عند فتح التحليل الكامل.')+'</div>'+
 '<div class="stock-actions stock-card-actions"><button class="stock-chart-action" onclick="event.stopPropagation();openSymbol(\''+raw+'\',true)">🔍 تحليل السهم</button></div><footer class="sas-card-copyright">SAS PRO RADAR · © جميع الحقوق محفوظة · معلومات تعليمية وليست توصية استثمارية</footer></article>';
}





function showOpportunityCardModal(blob,file,symbol){
  const old=document.getElementById('sasOpportunityCardModal');if(old)old.remove();
  const url=URL.createObjectURL(blob);
  const modal=document.createElement('div');modal.id='sasOpportunityCardModal';modal.className='sas-card-modal';modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-label','مشاركة أو حفظ بطاقة السهم');
  modal.innerHTML='<div class="sas-card-modal-panel"><button type="button" class="sas-card-modal-close" aria-label="إغلاق">×</button><h3>بطاقة '+String(symbol).replace(/[<>&"]/g,'')+'</h3><p>اختر مشاركة البطاقة أو حفظها في جهازك</p><img class="sas-card-preview" alt="معاينة بطاقة السهم"><div class="sas-card-modal-actions"><button type="button" class="sas-card-modal-share">↗ مشاركة الصورة</button><button type="button" class="sas-card-modal-download">▣ حفظ في الجهاز</button></div><p class="sas-card-modal-status" role="status" aria-live="polite">إذا ما فتح الحفظ، افتح الصورة واضغط عليها مطولًا للحفظ أو المشاركة.</p><small>SAS PRO RADAR · معلومات تعليمية وليست توصية استثمارية</small></div>';
  const preview=modal.querySelector('.sas-card-preview');
  const status=modal.querySelector('.sas-card-modal-status');
  const shareBtn=modal.querySelector('.sas-card-modal-share');
  const saveBtn=modal.querySelector('.sas-card-modal-download');
  preview.src=url;
  preview.tabIndex=0;
  preview.style.cursor='zoom-in';
  const openImage=()=>{
    // Open the generated image directly so mobile WebViews can use their native
    // long-press Save/Share actions when download/share APIs are unavailable.
    const win=window.open(url,'_blank');
    if(!win){
      preview.style.maxHeight='none';
      preview.style.width='100%';
      status.textContent='الصورة ظاهرة أعلاه. اضغط عليها مطولًا ثم اختر حفظ الصورة أو مشاركتها.';
    }else{
      status.textContent='فتحت الصورة في صفحة مستقلة؛ اضغط عليها مطولًا للحفظ أو المشاركة.';
    }
  };
  const close=()=>{modal.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);};
  modal.querySelector('.sas-card-modal-close').addEventListener('click',close);
  modal.addEventListener('click',e=>{if(e.target===modal)close();});
  preview.addEventListener('click',openImage);
  preview.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openImage();}});
  saveBtn.addEventListener('click',()=>{
    try{
      const a=document.createElement('a');
      a.href=url;a.download=file.name||('SAS-PRO-'+symbol+'.png');
      a.style.display='none';document.body.appendChild(a);a.click();a.remove();
      status.textContent='إذا لم يبدأ التنزيل، اضغط على معاينة الصورة مطولًا واختر حفظ الصورة.';
      // WebViews often ignore the download attribute; provide a user-gesture fallback.
      setTimeout(()=>{if(document.getElementById('sasOpportunityCardModal')){status.textContent='إذا ما نزلت الصورة تلقائيًا، اضغط على المعاينة مطولًا للحفظ أو افتحها من زر المشاركة.';}},900);
    }catch(e){
      status.textContent='تعذر التنزيل التلقائي. افتح الصورة بالضغط على المعاينة ثم احفظها من جهازك.';
    }
  });
  shareBtn.addEventListener('click',async()=>{
    shareBtn.disabled=true;
    try{
      if(navigator.share&&typeof File!=='undefined'){
        const shareFile=new File([blob],file.name||('SAS-PRO-'+symbol+'.png'),{type:'image/png'});
        if(!navigator.canShare||navigator.canShare({files:[shareFile]})){
          await navigator.share({title:'SAS PRO — '+symbol,text:'بطاقة سهم من رادار SAS PRO. ليست توصية شراء أو بيع.',files:[shareFile]});
          status.textContent='تم فتح خيارات المشاركة.';
          return;
        }
      }
      status.textContent='المشاركة المباشرة غير مدعومة في نافذة التطبيق. افتح الصورة بالضغط على المعاينة ثم شاركها من جهازك.';
      openImage();
    }catch(e){
      if(e?.name!=='AbortError'){
        status.textContent='تعذرت المشاركة المباشرة. اضغط على معاينة الصورة مطولًا ثم اختر مشاركة.';
        openImage();
      }
    }finally{shareBtn.disabled=false;}
  });
  document.body.appendChild(modal);
}
function downloadOpportunityImage(blob,filename){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename; a.style.display='none';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);}
async function shareOpportunityCard(card,symbol,downloadOnly=false){
 if(!card){showActionNotice('radar','تعذر تحديد بطاقة السهم. حدّث الصفحة وجرب مرة ثانية.');return;}
 const btns=card.querySelectorAll('.stock-card-actions button');btns.forEach(b=>b.disabled=true);
 try{
  const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=1350;
  const ctx=canvas.getContext('2d');if(!ctx)throw new Error('تعذر تجهيز صورة البطاقة.');
  const bg=ctx.createLinearGradient(0,0,1080,1350);bg.addColorStop(0,'#061d2d');bg.addColorStop(.58,'#03111d');bg.addColorStop(1,'#061a29');ctx.fillStyle=bg;ctx.fillRect(0,0,1080,1350);
  ctx.strokeStyle='rgba(66,210,245,.55)';ctx.lineWidth=3;ctx.beginPath();ctx.roundRect(28,28,1024,1294,38);ctx.stroke();
  const glow=ctx.createRadialGradient(920,120,10,920,120,420);glow.addColorStop(0,'rgba(0,183,230,.18)');glow.addColorStop(1,'rgba(0,183,230,0)');ctx.fillStyle=glow;ctx.fillRect(0,0,1080,600);
  const rtl=(text,x,y,size,color,weight='500')=>{ctx.font=weight+' '+size+'px Arial, sans-serif';ctx.fillStyle=color;ctx.textAlign='right';ctx.direction='rtl';ctx.fillText(String(text||''),x,y,920);};
  const ltr=(text,x,y,size,color,weight='700')=>{ctx.font=weight+' '+size+'px Arial, sans-serif';ctx.fillStyle=color;ctx.textAlign='left';ctx.direction='ltr';ctx.fillText(String(text||''),x,y,920);};
  ltr('SAS PRO RADAR',72,100,29,'#55dffb','700');rtl('بطاقة فرصة مرصودة',1000,165,43,'#eaf8ff','700');ltr(symbol,72,230,52,'#f2fbff','700');
  const status=card.querySelector('.radar-stock-status')?.textContent||'تحت المراقبة';rtl(status,1000,230,27,status.includes('مؤكدة')?'#65e9b5':'#ffd27b','700');
  const price=card.querySelector(':scope > strong')?.textContent||'السعر غير متاح';ltr(price,72,325,58,'#66e4b8','700');
  const change=card.querySelector('.stock-head > span')?.textContent||'';ltr(change,72,380,30,change.includes('-')?'#ff7d91':'#67e6b8','700');
  const levelEls=[...card.querySelectorAll('.stock-levels span')].map(e=>e.innerText.replace(/\s+/g,' ').trim());
  const box=(x,y,w,h,label,value)=>{ctx.fillStyle='rgba(3,15,26,.86)';ctx.strokeStyle='rgba(65,151,183,.28)';ctx.lineWidth=2;ctx.beginPath();ctx.roundRect(x,y,w,h,22);ctx.fill();ctx.stroke();ctx.textAlign='center';ctx.direction='rtl';ctx.fillStyle='#91adbf';ctx.font='500 25px Arial';ctx.fillText(label,x+w/2,y+42);ctx.fillStyle='#f0fbff';ctx.font='700 31px Arial';ctx.direction='ltr';ctx.fillText(value,x+w/2,y+91);};
  [['دخول',levelEls.find(x=>x.includes('دخول'))],['وقف',levelEls.find(x=>x.includes('وقف'))],['الهدف',levelEls.find(x=>x.includes('هدف'))]].forEach((a,i)=>box(65+i*320,420,290,118,a[0],(a[1]||'—').split(' ').slice(-1)[0]));
  const meta=[...card.querySelectorAll('.stock-meta span')].map(e=>e.innerText.replace(/\s+/g,' ').trim());rtl(meta.join('     •     '),1000,600,26,'#b3cbd9','500');
  const sh=card.querySelector('.stock-shariah');ctx.fillStyle='rgba(52,42,16,.35)';ctx.strokeStyle='rgba(227,190,94,.82)';ctx.lineWidth=2;ctx.beginPath();ctx.roundRect(65,650,950,105,20);ctx.fill();ctx.stroke();rtl('🕌 تحقق من الشرعية',980,692,28,'#f1d58b','700');rtl(sh?.innerText?.replace(/\s+/g,' ').trim()||'غير واضح / يحتاج تحقق — تحقق داخل SAS PRO',980,735,23,'#f5d989','500');
  const gates=[...card.querySelectorAll('.stock-gates i')].map(e=>e.innerText.replace(/\s+/g,' ').trim());rtl(gates.join('     •     '),1000,825,25,'#82e8c1','500');
  const summary=card.querySelector('.stock-ai')?.innerText||'';rtl(summary,1000,920,27,'#c6d8e5','500');
  ctx.strokeStyle='rgba(55,177,207,.25)';ctx.beginPath();ctx.moveTo(65,1132);ctx.lineTo(1015,1132);ctx.stroke();rtl('SAS PRO • رادار الأسهم الأمريكية',1000,1180,26,'#5cddf8','700');rtl('© جميع الحقوق محفوظة',1000,1223,22,'#9ab1c1','500');rtl('معلومات تعليمية وليست توصية شراء أو بيع',1000,1262,20,'#9ab1c1','500');
  const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('تعذر إنشاء صورة البطاقة.')),'image/png'));
  const file=new File([blob],'SAS-PRO-'+symbol+'.png',{type:'image/png'});
  showOpportunityCardModal(blob,file,symbol);
 }catch(e){if(e?.name!=='AbortError')showActionNotice('radar',e?.message||'تعذر إنشاء البطاقة.');}
 finally{btns.forEach(b=>b.disabled=false);}
}

function openSymbol(symbol,focusChart=false){document.getElementById('symbolSearch').value=symbol;switchTerminalTab('search');analyzeSymbol().then(()=>{if(focusChart){const detail=document.getElementById('symbolResult');const chart=detail?.querySelector('.chart-title')||detail?.querySelector('canvas')||detail?.querySelector('.quote-line');if(chart){chart.scrollIntoView({behavior:'smooth',block:'start'});chart.classList.add('chart-focus');setTimeout(()=>chart.classList.remove('chart-focus'),1800);}}});}
function openShariah(symbol){const input=document.getElementById('symbolSearch');if(input)input.value=String(symbol||'').toUpperCase();switchTerminalTab('search');const result=document.getElementById('shariahSearchResult');if(result)result.hidden=false;searchShariah();setTimeout(()=>{const target=document.getElementById('shariahSearchResult');target?.scrollIntoView({behavior:'smooth',block:'center'});},120);}
async function searchShariah(){
 const actionBtn=document.getElementById('shariahSearchBtn');
 if(actionBtn?.disabled)return;
 const input=document.getElementById('symbolSearch');
 const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,'');
 const el=document.getElementById('shariahSearchResult');
 if(!el)return;
 if(!symbol){
  el.hidden=false;
  el.innerHTML='<div class="shariah-search-card sh-unknown"><b>🕌 بحث الشرعية</b><p>اكتب رمز السهم أولًا، مثل <b>AAPL</b> أو <b>HTZ</b>.</p></div>';
  return;
 }
 if(actionBtn){actionBtn.disabled=true;actionBtn.classList.add('is-searching');actionBtn.setAttribute('aria-busy','true');}
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
   const sourceName=String(src.source||'مصدر');
   const sourceGlyph=sourceName==='مصرف الراجحي'?'🏦':(sourceName==='بنك البلاد'?'🏛️':(sourceName==='يقين'?'🔎':'◎'));
   const name='<span class="shariah-source-identity"><span class="shariah-source-brand-icon" aria-hidden="true">'+sourceGlyph+'</span><b>'+escHtml(sourceName)+'</b></span>';
   const statusText=escHtml(src.status_ar||'غير واضح / يحتاج تحقق');
   let meaning='';
   if(src.source==='يقين') meaning=src.verified?'نتيجة منشورة مباشرة للسهم.':'لم تظهر نتيجة مباشرة موثقة.';
   else meaning=src.verified?'نتيجة مباشرة موثقة.':'لا توجد نتيجة مباشرة موثقة.';
   const sourceState=String(src.status||'').toLowerCase();
   const sourceClass=(sourceState==='compliant'||sourceState==='halal')?'source-ok':((sourceState==='non_compliant'||sourceState==='haram')?'source-bad':'source-unknown');
   const sourceIcon=sourceClass==='source-ok'?'✓':(sourceClass==='source-bad'?'×':'?');
   return '<div class="shariah-source-row '+sourceClass+'"><span class="shariah-source-description">'+name+'<small>'+escHtml(meaning)+'</small></span><b class="shariah-source-status"><i aria-hidden="true">'+sourceIcon+'</i>'+statusText+'</b></div>';
  }).filter(Boolean).join(''):'<div class="shariah-source-row"><span>المصادر</span><b>لا توجد نتيجة موثقة كافية</b></div>';
  const isDoubtful=status==='doubtful'||status==='questionable';
  const statusTitle=isDoubtful?'محل نظر بحسب المصدر المنشور':(cls==='sh-ok'?'متوافق بحسب النتيجة المنشورة':(cls==='sh-bad'?'غير متوافق بحسب النتيجة المنشورة':'غير واضح / يحتاج تحقق'));
  const statusIcon=cls==='sh-ok'?'✓':(cls==='sh-bad'?'×':(isDoubtful?'!':'?'));
  const statusExplanation=isDoubtful?(result?.message||'المصدر المباشر يصنف السهم «محل نظر»؛ هذه ليست موافقة شرعية. راجع تفاصيل المصدر وتاريخ التحديث.'):(cls==='sh-unknown'?'لم تتوفر نتيجة مباشرة موثقة أو توجد معلومات غير كافية. لن نخمن الحكم الشرعي.':(result?.reason||result?.message||'الحالة بحسب المصدر المنشور؛ راجع تفاصيل المصادر أدناه.'));
  el.innerHTML='<div class="shariah-search-card shariah-review '+cls+'">'+
   '<div class="shariah-review-hero">'+
    '<div class="shariah-emblem '+cls+'" aria-hidden="true"><span class="shariah-emblem-mark">'+statusIcon+'</span><span class="shariah-emblem-caption">SAS PRO</span></div>'+
    '<div class="shariah-verdict"><span class="shariah-eyebrow">التحقق من شرعية السهم</span><h3 dir="ltr">'+escHtml(symbol)+'</h3><div class="shariah-verdict-state '+cls+'"><span aria-hidden="true">'+statusIcon+'</span>'+escHtml(statusTitle)+'</div><p>'+escHtml(statusExplanation)+'</p></div>'+
   '</div>'+
   '<div class="shariah-review-section-title"><b>مصدر التحقق</b><span>'+sources.length+' مصدر</span></div>'+
   '<div class="shariah-source-list">'+sourceLines+'</div>'+
   (result?.updated_at?'<div class="shariah-updated">◷ آخر تحديث منشور: '+escHtml(result.updated_at)+'</div>':'')+
   '<div class="shariah-review-warning"><b>⚠ تنبيه مهم</b><p>لا يصدر SAS PRO حكمًا من عنده. عند غياب نتيجة مباشرة موثقة أو اختلاف المصادر، تبقى الحالة «غير واضح / يحتاج تحقق». هذه معلومات للمراجعة وليست فتوى، والتحقق النهائي مسؤولية المستخدم.</p></div>'+
   '</div>';
 }catch(e){
  el.innerHTML='<div class="shariah-search-card sh-unknown shariah-error-card" role="status" aria-live="polite"><div class="shariah-error-icon" aria-hidden="true">⚠</div><b>تعذر الاتصال بمصدر التحقق</b><p>لم تصلنا نتيجة موثقة الآن. جرّب إعادة الفحص؛ لن نخمن الحكم الشرعي.</p><button type="button" class="shariah-retry-btn" onclick="searchShariah()" aria-label="إعادة التحقق من شرعية السهم"><span aria-hidden="true">↻</span> إعادة التحقق الآن</button></div>';
 }finally{
  if(actionBtn){actionBtn.disabled=false;actionBtn.classList.remove('is-searching');actionBtn.removeAttribute('aria-busy');}
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
 const chartUrl=safeNewsUrl(data?.chart_url||data?.source_url)||'https://perpequities.com/ar';
 const embedTitle=document.createElement('div');embedTitle.className='perp-embed-title';embedTitle.innerHTML='<b>الشارت التفاعلي الأصلي</b><span>1D · 7D · 30D · 90D · All</span>';panel.appendChild(embedTitle);
 const frame=document.createElement('div');frame.className='perp-embed-frame';const iframe=document.createElement('iframe');iframe.title='شارت وتحليل '+symbol+' من PerpEquities';iframe.src=chartUrl;iframe.loading='lazy';iframe.referrerPolicy='strict-origin-when-cross-origin';iframe.allow='fullscreen';frame.appendChild(iframe);panel.appendChild(frame);
 const note=document.createElement('p');note.className='perp-analysis-disclaimer';note.textContent='بيانات العقود الدائمة قد تختلف عن سعر السهم الفوري. مصدر البيانات: PerpEquities. البيانات للمعلومات فقط وليست توصية تداول.';panel.appendChild(note);host.appendChild(panel);
}

function renderAvailableLevelPanel(title,subtitle,fields){
 const available=(Array.isArray(fields)?fields:[]).filter(([,value])=>value!==null&&value!==undefined&&String(value).trim()!==''&&!['غير متاح','غير متاحة','—','-','NaN','undefined'].includes(String(value).trim()));
 if(!available.length)return '';
 return '<section class="sas-terminal-panel" id="sasTerminalLevels"><div class="sas-terminal-section-title"><span>⌖</span><div><b>'+escHtml(title)+'</b><small>'+escHtml(subtitle)+'</small></div></div><div class="level-grid">'+available.map(([label,value])=>'<div><small>'+escHtml(label)+'</small><b>'+escHtml(value)+'</b></div>').join('')+'</div></section>';
}
function renderStockTerminalQuickMetrics(classification,targets,mini){
 const c=classification&&typeof classification==='object'?classification:{},t=targets&&typeof targets==='object'?targets:{},m=mini&&typeof mini==='object'?mini:{};
 const nested=c.indicators&&typeof c.indicators==='object'?c.indicators:{},ind=t.indicators&&typeof t.indicators==='object'?t.indicators:{};
 const pick=(...v)=>v.find(x=>x!==null&&x!==undefined&&x!==''&&Number.isFinite(Number(x)));
 const fmt=(v,d=2)=>{const n=Number(v);return v===undefined||v===null||v===''||!Number.isFinite(n)?null:n.toFixed(d);};
 const rsi=fmt(pick(c.rsi14,nested.rsi14,ind.rsi14));
 const rvol=fmt(pick(m.rvol,c.rvol,nested.rvol,ind.rvol));
 const atrValue=pick(c.atr14,c.atr,nested.atr14,ind.atr14,t.atr14);
 const atr=atrValue!==undefined&&Number(atrValue)>0?'$'+money(atrValue):null;
 const data=[
  {label:'الزخم (RSI 14)',value:rsi,tone:'violet',note:'مؤشر القوة النسبية'},
  {label:'السيولة (RVOL)',value:rvol===null?null:rvol+'×',tone:'green',note:'الحجم النسبي'},
  {label:'التقلب (ATR 14)',value:atr,tone:'blue',note:'متوسط المدى الحقيقي'}
 ].filter(x=>x.value!==null&&x.value!==undefined&&x.value!=='');
 if(!data.length)return '';
 return '<div class="sas-terminal-metrics" id="sasTerminalMetrics">'+data.map(x=>'<article class="sas-terminal-metric '+x.tone+'"><div class="sas-terminal-metric-icon">'+(x.tone==='violet'?'◉':x.tone==='green'?'▥':'∿')+'</div><div><small>'+x.label+'</small><strong>'+x.value+'</strong><span>'+x.note+'</span></div></article>').join('')+'</div>';
}
function renderStockTerminalAverages(classification,targets){
 const c=classification&&typeof classification==='object'?classification:{},t=targets&&typeof targets==='object'?targets:{};
 const nested=c.indicators&&typeof c.indicators==='object'?c.indicators:{},ind=t.indicators&&typeof t.indicators==='object'?t.indicators:{};
 const pick=(...v)=>v.find(x=>x!==null&&x!==undefined&&x!==''&&Number.isFinite(Number(x))&&Number(x)>0);
 const rows=[['MA 20',pick(c.ma20,c.sma20,nested.ma20,nested.sma20,ind.ma20,ind.sma20)],['MA 50',pick(c.ma50,c.sma50,nested.ma50,nested.sma50,ind.ma50,ind.sma50)],['MA 100',pick(c.ma100,c.sma100,nested.ma100,nested.sma100,ind.ma100,ind.sma100)],['MA 200',pick(c.ma200,c.sma200,nested.ma200,nested.sma200,ind.ma200,ind.sma200)]].filter(([,value])=>value!==undefined&&value!==null&&Number.isFinite(Number(value))&&Number(value)>0);
 if(!rows.length)return '';
 return '<section class="sas-terminal-panel" id="sasMovingAverages"><div class="sas-terminal-section-title"><span>▥</span><div><b>المتوسطات المتحركة</b><small>القيم الفعلية المستلمة من مصدر التحليل</small></div></div><div class="sas-terminal-averages">'+rows.map(([label,value])=>'<div><small>'+label+'</small><strong>$'+money(value)+'</strong><span>قيمة فعلية</span></div>').join('')+'</div></section>';
}
function toggleSasQualityDetails(id,button){const p=document.getElementById(id);if(!p)return;const opening=p.hidden;p.hidden=!opening;if(button){button.setAttribute('aria-expanded',String(opening));button.textContent=opening?'إخفاء تفاصيل التقييم':'عرض تفاصيل التقييم';}}
function renderSasQualityPanel(classification,tech,quote,entry,stop,target1,rr){
 const c=classification&&typeof classification==='object'?classification:{},t=tech&&typeof tech==='object'?tech:{};
 const plan=c.conditional_plan&&typeof c.conditional_plan==='object'?c.conditional_plan:(t.conditional_plan&&typeof t.conditional_plan==='object'?t.conditional_plan:{});
 const num=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?Number(v):null;
 const level=v=>{const n=num(v);return n!==null&&n>0?'$'+money(n):'غير متاح من البيانات';};
 const price=num(quote?.price),score=num(c.quality_score)??num(c.score)??num(t.quality_score)??num(t.score);
 const rs=num(c.stars)??num(t.stars),hasRating=rs!==null||score!==null,stars=Math.max(0,Math.min(5,Math.round(rs!==null?rs:(score!==null?score/20:0))));
 const starText='★'.repeat(stars)+'☆'.repeat(5-stars),riskScore=num(c.risk_score)??num(t.risk_score),riskLevel=String(c.risk_level||t.risk_level||'');
 const chase=Boolean(c.chase_risk||t.chase_risk),change=num(quote?.change_pct),dist=num(c.distance_from_ema20_pct)??num(t.distance_from_ema20_pct);
 const trigger=num(plan.trigger_level)??num(c.breakout_level)??num(t.breakout_level),stopVal=num(plan.stop_loss)??num(stop)??num(t.stop_loss),targetOne=num(plan.target_1)??num(target1),targetTwo=num(plan.target_2)??num(t.target_2);
 const overextended=chase||(change!==null&&change>=10)||(dist!==null&&dist>=8)||(price!==null&&trigger!==null&&trigger>0&&price>trigger*1.03);
 let risk='moderate',riskLabel='متوسط الخطورة',riskIcon='🟠',riskCopy='راقب السيولة والتذبذب قبل اتخاذ أي قرار';
 if(overextended||(riskScore!==null&&riskScore>=70)||/مرتفع|عالي|high|extreme/i.test(riskLevel)){risk='high';riskLabel=overextended?'خطر مطاردة السعر مرتفع':'خطورة مرتفعة';riskIcon='🔴';riskCopy=overextended?'السعر قد يكون تجاوز منطقة الدخول المناسبة؛ لا تطارد الحركة. انتظر تراجعًا أو إعادة اختبار مؤكدة.':'تذبذب أو مخاطر مرتفعة؛ راقب إبطال السيناريو وحدد المخاطرة مسبقًا.';}
 else if(riskScore!==null&&riskScore<=35&&!chase&&!/مرتفع|عالي|high|extreme/i.test(riskLevel)){risk='low';riskLabel='خطورة أقل نسبيًا';riskIcon='🟢';riskCopy='الخطورة النسبية أقل وفق البيانات المتاحة، لكنها لا تعني أن السهم آمن.';}
 const entryText=overextended?'تجاوز/ابتعد عن منطقة الدخول — لا تطارد السعر':trigger!==null&&price!==null&&trigger>price?'دخول مشروط بعد اختراق وإغلاق فوق '+level(trigger):trigger!==null?'انتظر تأكيد الاختراق أو إعادة الاختبار قرب '+level(trigger):'لا توجد منطقة دخول موثوقة مؤكدة';
 const checks=c.quality_checks&&typeof c.quality_checks==='object'?c.quality_checks:{};
 const labels={daily_candles:'كفاية الشموع اليومية',ema_trend:'اتجاه المتوسطات',trend_ema20_50_200:'اتجاه المتوسطات EMA20/50/200',rsi:'مؤشر RSI',rsi_55_70:'مؤشر RSI',rvol:'حجم التداول النسبي RVOL',relative_volume_1_5:'حجم التداول النسبي RVOL',relative_strength:'القوة النسبية مقابل المؤشر',relative_strength_vs_qqq:'القوة النسبية مقابل QQQ',breakout:'تأكيد الاختراق',breakout_confirmed:'تأكيد الاختراق',accumulation:'علامات التجميع',distribution_risk:'مخاطر التصريف',liquidity_distribution_risk:'مخاطر التصريف والسيولة',bearish_structure:'البنية الهابطة',bearish_head_shoulders:'نموذج الرأس والكتفين الهابط',chase_risk:'خطر مطاردة السعر',entry_not_chasing:'الدخول دون مطاردة السعر',risk_reward:'العائد مقابل المخاطرة',data_source:'موثوقية مصدر البيانات'};
 const checksHtml=Object.keys(labels).filter((k,i,a)=>Object.prototype.hasOwnProperty.call(checks,k)&&!a.slice(0,i).some(prev=>prev===k)).map(k=>{const v=checks[k],passed=typeof v==='boolean'?v:(v&&typeof v==='object'&&typeof v.pass==='boolean'?v.pass:null),detail=v&&typeof v==='object'?(v.label||v.reason||v.value||''):((typeof v==='string'||typeof v==='number')?String(v):'');return '<div class="sas-quality-check '+(passed===true?'pass':passed===false?'fail':'neutral')+'"><i>'+(passed===true?'✓':passed===false?'✕':'•')+'</i><span>'+labels[k]+'</span><small>'+escHtml(detail||(passed===true?'متحقق':passed===false?'غير متحقق':'غير متاح'))+'</small></div>';}).join('');
 const safeScore=score===null?'غير مكتمل':Math.round(Math.max(0,Math.min(100,score)))+'/100',starsDisplay=hasRating?stars+' / 5':'— / 5',recommendation=String(c.recommendation||t.recommendation||'مراقبة فقط — انتظر تأكيد البيانات والإشارة');
 const disclaimer=String(c.analysis_disclaimer||t.analysis_disclaimer||'تحليل تعليمي مبني على البيانات المتاحة فقط، وليس توصية شراء أو بيع. المستويات والأهداف والوقف احتمالية وغير مضمونة. قرار التداول وإدارة المخاطر مسؤولية المتداول.');
 const detailId='sasQualityDetails'+String(quote?.symbol||'stock').replace(/[^A-Za-z0-9_-]/g,'');
 return '<section class="sas-quality-panel risk-'+risk+'" aria-label="جودة السهم وخطورته"><div class="sas-quality-topline"><div><span class="sas-quality-eyebrow">SAS PRO · QUALITY & RISK ENGINE</span><h3>تقييم جودة السهم</h3><small>تقييم آلي من المؤشرات والشموع والشروط المتاحة</small></div><span class="sas-risk-pill">'+riskIcon+' '+riskLabel+'</span></div>'+
 '<div class="sas-quality-score-row"><div class="sas-quality-stars" aria-label="'+(hasRating?stars+' من 5 نجوم':'التقييم غير مكتمل')+'">'+(hasRating?starText:'☆☆☆☆☆')+'</div><div class="sas-quality-score"><b>'+safeScore+'</b><small>درجة الجودة</small></div><div class="sas-quality-score"><b>'+starsDisplay+'</b><small>النجوم</small></div></div><div class="sas-quality-glowbar"><span style="width:'+(score===null?0:Math.max(0,Math.min(100,score)))+'%"></span></div>'+
 '<div class="sas-risk-message"><b>'+riskIcon+' قراءة المخاطر</b><p>'+escHtml(riskCopy)+'</p></div><div class="sas-entry-plan"><div class="sas-entry-plan-head"><b>🎯 منطقة الدخول المحتملة</b><span class="'+(overextended?'entry-danger':trigger!==null?'entry-wait':'entry-unknown')+'">'+(overextended?'متجاوزة':trigger!==null?'مشروطة':'غير محددة')+'</span></div><strong>'+escHtml(entryText)+'</strong><small>التفعيل يحتاج إغلاق شمعة تأكيديًا وحجم تداول مناسبًا؛ لمس المستوى لحظيًا لا يكفي.</small>'+
 '<div class="sas-plan-levels"><div><small>سعر التحليل</small><b>'+level(price)+'</b></div><div><small>مستوى التفعيل</small><b>'+level(trigger)+'</b></div><div><small>وقف السيناريو</small><b>'+level(stopVal)+'</b></div><div><small>الهدف الأول</small><b>'+level(targetOne)+'</b></div><div><small>الهدف الثاني</small><b>'+level(targetTwo)+'</b></div><div><small>العائد/المخاطرة</small><b>'+(rr&&rr!=='—'?escHtml(rr)+'×':(num(t.risk_reward)!==null?escHtml(num(t.risk_reward).toFixed(2))+'×':'غير متاح'))+'</b></div></div></div>'+
 '<div class="sas-quality-recommendation"><small>الخلاصة الفنية الآلية</small><b>'+escHtml(recommendation)+'</b></div><button type="button" class="sas-quality-toggle" aria-expanded="false" onclick="toggleSasQualityDetails(\''+detailId+'\',this)">عرض تفاصيل التقييم</button><div class="sas-quality-details" id="'+detailId+'" hidden><h4>مراجعة الفلاتر والشروط</h4>'+(checksHtml?'<div class="sas-quality-checks">'+checksHtml+'</div>':'<p>تفاصيل فلاتر الجودة غير متاحة في استجابة التحليل الحالية؛ لم يتم افتراض اجتياز أي شرط.</p>')+'<p class="sas-quality-method">النجوم درجة فرز داخلية وليست احتمال ربح. نقص البيانات يقلل موثوقية التقييم، ولا تعني النجوم الأعلى ضمان النجاح.</p></div>'+
 '<div class="sas-quality-disclaimer"><b>⚠️ إخلاء مسؤولية</b><p>'+escHtml(disclaimer)+'</p><p>قد تتأخر الأسعار أو تختلف عن السعر القابل للتنفيذ. لا تدخل لمجرد ظهور النجوم أو ملامسة مستوى؛ تحقّق من الشموع والسيولة والأخبار وحدّد حجم المخاطرة مسبقًا.</p></div></section>';
}
async function analyzeSymbol(){
 if(activeSymbolSearchController)activeSymbolSearchController.abort();
 const searchController=new AbortController();activeSymbolSearchController=searchController;
 const actionBtn=document.getElementById('analyzeSymbolBtn');
 if(actionBtn?.disabled)return;
 if(actionBtn){actionBtn.disabled=true;actionBtn.classList.add('is-working');actionBtn.setAttribute('aria-busy','true');}
 const input=document.getElementById('symbolSearch');
 const symbol=(input.value||'').trim().toUpperCase().replace(/[^A-Z.\-]/g,'');
 if(!symbol){if(actionBtn){actionBtn.disabled=false;actionBtn.classList.remove('is-working');actionBtn.removeAttribute('aria-busy');}return;}
 const el=document.getElementById('symbolResult');
 startStockAnalysisLoader(el,symbol);
 const [qR,chartR,newsR,analysisR,miniR,shariahR,perpR,cuspR]=await Promise.allSettled([
   api('/api/stocks/'+encodeURIComponent(symbol)+'/quote',{signal:searchController.signal}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/chart',{signal:searchController.signal}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/news',{signal:searchController.signal}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/analyze',{method:'POST',timeoutMs:30000,signal:searchController.signal}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/mini-analysis',{timeoutMs:30000,signal:searchController.signal}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/shariah',{timeoutMs:12000}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/perpequities',{timeoutMs:10000,signal:searchController.signal}),
   api('/api/stocks/'+encodeURIComponent(symbol)+'/cusp',{timeoutMs:9000,signal:searchController.signal})
 ]);
 stopStockAnalysisLoader();
 if(searchController.signal.aborted){if(actionBtn){actionBtn.disabled=false;actionBtn.classList.remove('is-working');actionBtn.removeAttribute('aria-busy');}return;}
 if(actionBtn){actionBtn.disabled=false;actionBtn.classList.remove('is-working');actionBtn.removeAttribute('aria-busy');}
 const qRaw=qR.status==='fulfilled'&&qR.value&&typeof qR.value==='object'?qR.value:{symbol,price:null,change_pct:null,source:'غير متاح'};
 const miniPayload=miniR.status==='fulfilled'&&miniR.value&&typeof miniR.value==='object'?miniR.value:null;
 const miniQuote=miniPayload?.quote&&typeof miniPayload.quote==='object'?miniPayload.quote:{};
 const hasNumericValue=(v)=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
 const symbolMatches=(item)=>!item?.symbol||String(item.symbol).trim().toUpperCase()===symbol;
 const rawQuoteUsable=symbolMatches(qRaw)&&((hasNumericValue(qRaw?.price)&&Number(qRaw.price)>0)||hasNumericValue(qRaw?.change_pct));
 const miniQuoteUsable=symbolMatches(miniQuote)&&((hasNumericValue(miniQuote?.price)&&Number(miniQuote.price)>0)||hasNumericValue(miniQuote?.change_pct));
 const q=rawQuoteUsable?qRaw:(miniQuoteUsable?Object.assign({symbol},miniQuote,{source:miniQuote.source||'SAS PRO OHLCV'}):Object.assign({},qRaw,{symbol}));
 const chart=chartR.status==='fulfilled'&&chartR.value&&typeof chartR.value==='object'?chartR.value:{candles:[],available:false};
 const news=newsR.status==='fulfilled'&&Array.isArray(newsR.value)?newsR.value:[];
 const analysis=analysisR.status==='fulfilled'?analysisR.value:null;
 const shariah=shariahR.status==='fulfilled'?shariahR.value:(analysis?.sas_pro?.targets?.shariah||null);
 if(!analysis){
   const errors=[qR,chartR,newsR,analysisR].filter(x=>x.status==='rejected').map(x=>x.reason?.message).filter(Boolean);
   el.innerHTML='<div class="fatal">⚠️ تعذر إكمال التحليل الكامل<br><small>لكن تم إبقاء البيانات التي نجح تحميلها.</small>'+(errors.length?'<br><small>'+escHtml(errors[0])+'</small>':'')+'</div>';
   if(Number(q.price)>0||news.length||chart.candles?.length) renderPartialAnalysis(el,symbol,q,chart,news,miniR.status==='fulfilled'?miniR.value:null,cuspR.status==='fulfilled'?cuspR.value:null);
   return;
 }
 const perp=perpR.status==='fulfilled'&&perpR.value&&typeof perpR.value==='object'?perpR.value:{symbol,available:false,source_url:'https://perpequities.com/ar',message_ar:'تعذر تحميل بيانات PerpEquities.'};
 const cusp=cuspR.status==='fulfilled'&&cuspR.value&&typeof cuspR.value==='object'?cuspR.value:{symbol,available:false,source_url:'https://cuspwealth.com/ar/asset/'+encodeURIComponent(symbol),message_ar:'تعذر تحميل بيانات CUSP الآن.'};
 const cuspMetricsHtml=renderCuspWealthMetrics(cusp);
 const cuspFundamentalSummary=renderCuspFundamentalSummary(cusp,q.price);
 const cuspHasData=Boolean((cusp.available&&(cusp.title||cusp.description))||cuspMetricsHtml.includes('cusp-metrics-grid'));
 const cuspHtml=cuspHasData?'<section class="ai-box cusp-research-card"><div class="cusp-research-head"><b>🏦 CUSP Wealth · بيانات مالية إضافية</b>'+(me?.admin?'<a href="'+escHtml(safeNewsUrl(cusp.source_url)||('https://cuspwealth.com/ar/asset/'+encodeURIComponent(symbol)))+'" target="_blank" rel="noopener noreferrer">فتح صفحة السهم ↗</a>':'')+'</div>'+
   (cusp.available?'<p><b>'+escHtml(cusp.title||symbol)+'</b></p>'+(cusp.description?'<p>'+escHtml(cusp.description)+'</p>':'')+cuspFundamentalSummary+cuspMetricsHtml:cuspMetricsHtml)+
   '<small>مصدر مالي إضافي؛ السعر قد يمثل آخر سعر منشور لدى CUSP وليس بالضرورة سعرًا لحظيًا. القيم غير المتاحة لا تُقدّر.</small></section>':'';

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
 const classification=Object.assign({},analysis.technical_analysis||{},analysis.sas_pro?.classification||analysis.classification||{});
 const indicatorHtml=renderVerifiedTechnicalCards(classification,tech,mini);
 const recommendationHtml='<section class="recommendation-box"><b>📌 القراءة الفنية</b><strong>'+escHtml(recommendation)+'</strong><small>قراءة آلية وليست توصية شراء أو بيع.</small></section>';
 const quotePriceDisplay=hasNumericValue(q.price)&&Number(q.price)>0?String.fromCharCode(36)+money(q.price):'غير متاح';
 const quoteChangeDisplay=hasNumericValue(q.change_pct)?pct(q.change_pct):'التغير غير متاح';
 const quoteChangeClass=hasNumericValue(q.change_pct)?(Number(q.change_pct)>=0?'up':'down'):'';
 const shSources=Array.isArray(sh.sources)?sh.sources:[];
 const shHtml='<section class="shariah-box '+(sh.status==='halal'||sh.status==='compliant'?'sh-ok':(sh.status==='haram'||sh.status==='non_compliant'?'sh-bad':'sh-unknown'))+'"><div><b>🕌 التحقق الشرعي</b><strong>'+escHtml(sh.status_ar||'غير واضح / يحتاج تحقق')+'</strong></div><p>'+escHtml(sh.message||'لم تتوفر نتيجة موثقة؛ لم يتم التأليف.')+'</p><div class="shariah-help"><b>كيف تقرأ النتيجة؟</b><span>يقين: نتيجة منشورة تخص السهم نفسه إذا كانت متوفرة.</span><span></span><span>إذا ما ظهرت نتيجة مباشرة من يقين، بتكون الحالة «غير واضح / يحتاج تحقق» بدون تخمين.</span></div>'+(sh.ai?.summary?'<p>🧠 '+escHtml(sh.ai.summary)+'</p>':'')+'<div class="shariah-red-disclaimer"><b>⚠️ إبراء ذمّة</b><span>هذا اجتهاد في عرض المعلومات المنشورة، وليس فتوى شرعية ولا ضمانًا لصحة الحكم. تحقّق بنفسك قبل اتخاذ قرارك.</span></div></section>';
const miniAnalysisHtml=
   '<section class="mini-analysis sas-pro-technical">'+
     '<div class="mini-analysis-head"><div><span class="eyebrow">SAS PRO VERIFIED TECHNICAL</span><b>📊 التحليل الفني والمؤشرات</b><small>القيم الفعلية المتاحة من محرك التحليل</small></div></div>'+
     '<div class="mini-analysis-grid">'+
       '<div><small>📈 الاتجاه</small><b>'+escHtml(mini.direction||'غير واضح')+'</b></div>'+
       '<div><small>🚀 الزخم</small><b>'+escHtml(mini.momentum||'غير متاح')+'</b></div>'+
       '<div><small>💧 السيولة</small><b>'+escHtml(mini.liquidity||'غير متاحة')+'</b></div>'+
       '<div><small>🔎 الإشارة</small><b>'+escHtml(mini.signal||'غير متاحة')+'</b></div>'+
     '</div>'+
     indicatorHtml+
     '<div class="mini-levels">'+
       '<span>🎯 الهدف <b>&#36;'+money(mini.target)+'</b></span>'+
       '<span>🛑 الوقف <b>&#36;'+money(mini.stop)+'</b></span>'+
     '</div>'+
     '<p class="mini-takeaway">'+escHtml(mini.takeaway||summary)+'</p>'+
   '</section>';
 el.innerHTML=
   '<div class="sas-stock-terminal">'+
    '<header class="sas-terminal-stock-head"><button class="sas-terminal-back" type="button" onclick="document.getElementById(\'symbolResult\').innerHTML=\'\';document.getElementById(\'symbolSearch\').focus()" aria-label="رجوع">‹</button><div class="sas-terminal-brand"><b>SAS <span>PRO</span></b><small>تحليل الأسهم الأمريكية</small></div></header>'+
    '<section class="sas-terminal-hero"><div class="sas-terminal-company"><div class="sas-terminal-symbol">'+escHtml(symbol.slice(0,4))+'</div><div><span class="eyebrow">SAS PRO · STOCK ANALYSIS</span><h2>'+escHtml(symbol)+'</h2><p>'+escHtml(q.name||q.company_name||q.company||'سهم أمريكي')+'</p><div class="sas-terminal-tags"><span>'+escHtml(q.exchange||q.mic||'السوق الأمريكي')+'</span><span>'+escHtml(q.currency||'USD')+'</span></div></div></div><div class="sas-terminal-price"><strong>'+quotePriceDisplay+'</strong><span class="'+quoteChangeClass+'">'+quoteChangeDisplay+'</span><small>السعر حسب البيانات المستلمة</small><span class="sas-terminal-live '+(q.is_delayed||String(q.source||'').toLowerCase().includes('cusp')?'sas-quote-delayed':'')+'"><i></i>'+escHtml((q.is_delayed||String(q.source||'').toLowerCase().includes('cusp'))?'CUSP Wealth · آخر سعر منشور':(q.source||'مصدر السعر غير متاح'))+'</span><span class=\"sas-terminal-live sas-quote-delayed sas-price-freshness\"><i></i>قد يختلف وقت تحديث السعر عن وقت التحليل، وفقًا لمصدر البيانات وتوقيت التحديث.</span></div></section>'+
    '<nav class="sas-terminal-tabs" aria-label="أقسام تحليل السهم"><a href="#sasTerminalMetrics">المؤشرات</a><a href="#sasMovingAverages">المتوسطات</a><a href="#sasTerminalChart">الشارت</a><a href="#sasTerminalLevels">المستويات</a><a href="#sasTerminalNews">الأخبار</a><a href="#sasTerminalShariah">الشرعية</a></nav>'+
    notice+
    renderSasQualityPanel(classification,tech,q,entry,stop,target1,rr)+
    renderStockTerminalQuickMetrics(classification,tech,mini)+
    renderStockTerminalAverages(classification,tech)+
    renderAvailableLevelPanel('مستويات السهم','القيم الفعلية المستلمة من محرك SAS PRO',[['الدخول',Number.isFinite(entry)&&entry>0?'$'+money(entry):null],['وقف الخسارة',Number.isFinite(stop)&&stop>0?'$'+money(stop):null],['الهدف الأول',Number.isFinite(target1)&&target1>0?'$'+money(target1):null],['العائد/المخاطرة',rr==='—'?null:rr]])+
    (Array.isArray(chart.candles)&&chart.candles.length?'<section class="sas-terminal-panel sas-terminal-chart-panel" id="sasTerminalChart"><div class="sas-terminal-section-title"><span>▥</span><div><b>الشارت التفاعلي</b><small>شموع السعر وحجم التداول · SAS PRO</small></div>'+(me?.admin?'<a class="gocharting-link" href="https://gocharting.com/stock/'+encodeURIComponent(symbol)+'" target="_blank" rel="noopener noreferrer">فتح الشارت الكامل ↗</a>':'')+'</div><div class="chart-box"><canvas id="stockCanvas" height="290"></canvas></div><div id="perpEquitiesAnalysis"></div></section>':'')+
    '<section class="sas-terminal-panel" id="sasTerminalFundamentals"><div class="sas-terminal-section-title"><span>▤</span><div><b>التحليل والبيانات الإضافية</b><small>ملخص المصدر والأبحاث المتاحة</small></div></div>'+cuspHtml+'<div class="ai-box"><b>'+(aiAvailable?'ملخص التحليل المتاح':'الخلاصة الفنية')+'</b><p>'+escHtml(summary)+'</p>'+(aiAvailable&&ai.provider?'<small>المزود: '+escHtml(ai.provider)+'</small>':'')+'</div>'+fccHtml+'</section>'+
    '<section class="sas-terminal-panel" id="sasTerminalNews"><div class="sas-terminal-section-title"><span>▣</span><div><b>آخر أخبار السهم</b><small>الأخبار المتاحة من المصادر</small></div></div><div class="news-list">'+renderNewsItems(newsFromAnalysis,5,Array.isArray(ai.news_summaries)?ai.news_summaries:[])+'</div></section>'+
    '<section class="sas-terminal-panel" id="sasTerminalShariah">'+shHtml+'</section>'+
    '<div class="terminal-disclaimer">البيانات للتحليل والمعلومات فقط، وليست توصية شراء أو بيع. تحقق من الأسعار والمصادر قبل اتخاذ القرار.</div>'+
   '</div>';

 pruneUnavailableAnalysis(el);
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
function renderVerifiedTechnicalCards(classification,targets,mini){
 const cls=classification&&typeof classification==='object'?classification:{};
 const t=targets&&typeof targets==='object'?targets:{};
 const m=mini&&typeof mini==='object'?mini:{};
 const indicators=(t.indicators&&typeof t.indicators==='object'?t.indicators:{});
 const nested=(cls.indicators&&typeof cls.indicators==='object'?cls.indicators:{});
 const pick=(...values)=>values.find(v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v)));
 const number=(value,digits=2)=>{const n=Number(value);return value!==null&&value!==undefined&&value!==''&&Number.isFinite(n)?n.toFixed(digits):null;};
 const price=(value)=>{const n=Number(value);return value!==null&&value!==undefined&&value!==''&&Number.isFinite(n)&&n>0?'$'+money(n):null;};
 const cards=[
  ['RSI 14',number(pick(cls.rsi14,nested.rsi14,indicators.rsi14)), 'مؤشر القوة النسبية'],
  ['RVOL',(()=>{const v=pick(m.rvol,cls.rvol,nested.rvol,indicators.rvol);const n=number(v);return n===null?null:n+'×';})(), 'الحجم النسبي'],
  ['ATR 14',price(pick(cls.atr14,cls.atr,nested.atr14,indicators.atr14,t.atr14)), 'متوسط المدى الحقيقي'],
  ['EMA 20',price(pick(cls.ema20,nested.ema20,indicators.ema20)), 'المتوسط الأسي'],
  ['EMA 50',price(pick(cls.ema50,nested.ema50,indicators.ema50)), 'المتوسط الأسي'],
  ['EMA 200',price(pick(cls.ema200,nested.ema200,indicators.ema200)), 'المتوسط الأسي'],
  ['ADX 14',number(pick(cls.adx14,nested.adx14,indicators.adx14)), 'قوة الاتجاه']
 ].filter(([,value])=>value!==null&&value!==undefined&&value!=='');
 if(!cards.length)return '';
 return '<div class="sas-indicator-grid">'+cards.map(([label,value,note])=>'<div class="sas-indicator-card"><small>'+escHtml(label)+'</small><b>'+escHtml(value)+'</b><span>'+escHtml(note)+'</span></div>').join('')+'</div><small class="sas-data-integrity">✓ تُعرض المؤشرات التي وصلت بقيم فعلية فقط؛ لا تُعرض القيم المفقودة أو المخمّنة.</small>';
}
function renderCuspFundamentalSummary(cusp,currentQuote){
 const data=cusp&&typeof cusp==='object'?cusp:{},m=data.metrics&&typeof data.metrics==='object'?data.metrics:{};
 const parts=[];
 const score=Number(m.valuation_score);
 if(m.valuation_score!==undefined&&Number.isFinite(score))parts.push('تقييم CUSP المنشور '+score+'/100'+(m.valuation_label?' ('+String(m.valuation_label)+')':'')+'.');
 if(m.pe_ratio)parts.push('مكرر الربحية: '+String(m.pe_ratio)+'.');
 if(m.market_cap)parts.push('القيمة السوقية المنشورة: '+String(m.market_cap)+'.');
 if(m.analyst_consensus)parts.push('إجماع المحللين: '+String(m.analyst_consensus)+'.');
 const target=Number(String(m.analyst_target||'').replace(/[^0-9.\-]/g,''));
 const price=Number(currentQuote);
 if(m.analyst_target&&Number.isFinite(target)&&target>0&&Number.isFinite(price)&&price>0){
  const upside=(target-price)/price*100;
  parts.push('متوسط السعر المستهدف المنشور '+String(m.analyst_target)+'؛ الفرق الحسابي عن السعر المعروض '+(upside>=0?'+':'')+upside.toFixed(2)+'%. هذا حساب مقارنة وليس توقعًا مضمونًا.');
 }
 if(!parts.length)return '<p class="cusp-fundamental-summary">وصلت صفحة CUSP لكن لم تتوفر حقول كافية لبناء ملخص أساسي موثوق لهذا السهم.</p>';
 return '<div class="cusp-fundamental-summary"><b>القراءة الأساسية من CUSP</b><p>'+escHtml(parts.join(' '))+'</p><small>المعلومات المالية ليست بديلًا عن المؤشرات الفنية، ولا تُعد توصية تداول.</small></div>';
}
function renderCuspWealthMetrics(cusp){
 const data=cusp&&typeof cusp==='object'?cusp:{};
 const metrics=data.metrics&&typeof data.metrics==='object'?data.metrics:{};
 const cards=[];
 const add=(label,value)=>{if(value!==null&&value!==undefined&&String(value).trim()!==''&&!['-','—','N/A','غير متاح'].includes(String(value).trim()))cards.push([label,String(value)]);};
 if(data.price!==null&&data.price!==undefined&&Number.isFinite(Number(data.price))&&Number(data.price)>0)add('السعر في CUSP',String.fromCharCode(36)+money(data.price));
 if(data.change_pct!==null&&data.change_pct!==undefined&&Number.isFinite(Number(data.change_pct)))add('التغير المنشور',pct(data.change_pct));
 add('نتيجة التقييم',metrics.valuation_score);
 add('القيمة السوقية',metrics.market_cap);
 add('حجم التداول',metrics.volume);
 add('متوسط السعر المستهدف',metrics.analyst_target);
 add('إجماع المحللين',metrics.analyst_consensus);
 add('مكرر الربحية P/E',metrics.pe_ratio);
 add('السعر إلى المبيعات',metrics.price_sales);
 add('السعر إلى القيمة الدفترية',metrics.price_book);
 add('الإيرادات الفصلية',metrics.quarterly_revenue);
 add('صافي الدخل',metrics.net_income);
 add('ربحية السهم الفعلية EPS',metrics.eps_actual);
 add('ربحية السهم المقدرة EPS',metrics.eps_estimate);
 add('العائد على حقوق الملكية',metrics.roe);
 add('هامش الربح الإجمالي',metrics.gross_margin);
 add('نطاق 52 أسبوعًا',metrics.week_52_range);
 if(!cards.length)return '<p class="cusp-no-metrics">لم تُستخرج أرقام مالية قابلة للتحقق من صفحة CUSP لهذا السهم.</p>';
 return '<div class="cusp-metrics-grid">'+cards.map(([label,value])=>'<div><small>'+escHtml(label)+'</small><b>'+escHtml(value)+'</b></div>').join('')+'</div><small class="cusp-data-note">المصدر: صفحة CUSP Wealth · تُعرض القيم كما نُشرت، دون تقدير القيم المفقودة.</small>';
}
function pruneUnavailableAnalysis(root){
 if(!root)return;
 const missing=/^(?:غير متاح(?:ة)?|غير واضح|لا توجد قيمة موثقة|لا توجد قيمة موثوقة|—|-|N\/A|NaN|undefined)$/i;
 const selectors=['.sas-terminal-metric','.sas-indicator-card','.sas-terminal-averages > div','.level-grid > div','.mini-analysis-grid > div','.mini-levels > span','.cusp-metrics-grid > div'];
 selectors.forEach(selector=>root.querySelectorAll(selector).forEach(card=>{
  const value=card.querySelector('strong,b');
  if(value&&missing.test(value.textContent.trim()))card.remove();
 }));
 [['#sasTerminalMetrics','.sas-terminal-metric'],['#sasMovingAverages','.sas-terminal-averages > div'],['#sasTerminalLevels','.level-grid > div']].forEach(([section,items])=>{
  const panel=root.querySelector(section);
  if(panel&&!panel.querySelector(items))panel.remove();
 });
 root.querySelectorAll('.news-list').forEach(list=>{
  if(!list.querySelector('.news-entry'))list.closest('#sasTerminalNews')?.remove();
 });
 root.querySelectorAll('.sas-terminal-tabs').forEach(nav=>{
  nav.querySelectorAll('a[href^="#"]').forEach(link=>{
   const target=link.getAttribute('href');
   if(!target||!root.querySelector(target))link.remove();
  });
  if(!nav.querySelector('a'))nav.remove();
 });
 root.querySelectorAll('.sas-terminal-panel').forEach(panel=>{
  if(panel.id==='sasTerminalResearch'&&!panel.querySelector('.cusp-metrics-grid > div')&&!panel.querySelector('.cusp-fundamental-summary p')&&!panel.querySelector('.ai-box p'))panel.remove();
 });
}
function renderPartialAnalysis(el,symbol,q,chart,news,miniData,cuspData){
 const fallbackQuote=miniData?.quote&&typeof miniData.quote==='object'?miniData.quote:{};
 const hasNumericValue=(v)=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
 const quoteUsable=(item)=>item&&((hasNumericValue(item.price)&&Number(item.price)>0)||hasNumericValue(item.change_pct));
 const quoteSymbolMatches=(item)=>!item?.symbol||String(item.symbol).trim().toUpperCase()===symbol;
 const displayQuote=quoteUsable(q)&&quoteSymbolMatches(q)?q:(quoteUsable(fallbackQuote)&&quoteSymbolMatches(fallbackQuote)?fallbackQuote:{symbol,price:null,change_pct:null,source:'غير متاح'});
 const price=hasNumericValue(displayQuote?.price)?Number(displayQuote.price):NaN;
 const change=hasNumericValue(displayQuote?.change_pct)?Number(displayQuote.change_pct):NaN;
 const priceText=Number.isFinite(price)&&price>0?String.fromCharCode(36)+money(price):'غير متاح';
 const changeText=Number.isFinite(change)?pct(change):'التغير غير متاح';
 const classification=miniData?.classification&&typeof miniData.classification==='object'?miniData.classification:{};
 const targets=miniData?.targets&&typeof miniData.targets==='object'?miniData.targets:{};
 const mini=miniData?.mini_analysis&&typeof miniData.mini_analysis==='object'?miniData.mini_analysis:{};
 const candles=Array.isArray(chart?.candles)?chart.candles:[];
 const quoteSource=displayQuote?.source||'مصدر السعر غير محدد';
 const cuspUrl=safeNewsUrl(cuspData?.source_url)||('https://cuspwealth.com/ar/asset/'+encodeURIComponent(symbol));
 const cuspMetricsHtml=renderCuspWealthMetrics(cuspData);
 const cuspFundamentalSummary=renderCuspFundamentalSummary(cuspData,displayQuote?.price);
 const cuspText=cuspData?.available?(cuspData.title||cuspData.description||'تم الوصول إلى صفحة CUSP؛ قد تكون التفاصيل محمّلة داخل الموقع.'):(cuspData?.message_ar||'بيانات CUSP غير متاحة آليًا الآن.');
 const cuspHasData=Boolean((cuspData?.available&&(cuspData.title||cuspData.description))||cuspMetricsHtml.includes('cusp-metrics-grid'));
 const quickMetrics=renderStockTerminalQuickMetrics(classification,targets,mini);
 const averages=renderStockTerminalAverages(classification,targets);
 const level=(v)=>{const n=Number(v);return Number.isFinite(n)&&n>0?'$'+money(n):'غير متاح';};
 el.innerHTML=
  '<div class="sas-stock-terminal sas-stock-terminal-partial">'+
   '<header class="sas-terminal-stock-head"><button class="sas-terminal-back" type="button" onclick="document.getElementById(\'symbolResult\').innerHTML=\'\';document.getElementById(\'symbolSearch\').focus()" aria-label="رجوع">‹</button><div class="sas-terminal-brand"><b>SAS <span>PRO</span></b><small>تحليل الأسهم الأمريكية</small></div></header>'+
   '<section class="sas-terminal-hero"><div class="sas-terminal-company"><div class="sas-terminal-symbol">'+escHtml(symbol.slice(0,4))+'</div><div><span class="eyebrow">SAS PRO · STOCK ANALYSIS</span><h2>'+escHtml(symbol)+'</h2><p>'+escHtml(displayQuote?.name||displayQuote?.company_name||displayQuote?.company||'سهم أمريكي')+'</p><div class="sas-terminal-tags"><span>'+escHtml(displayQuote?.exchange||displayQuote?.mic||'السوق الأمريكي')+'</span><span>'+escHtml(displayQuote?.currency||'USD')+'</span></div></div></div><div class="sas-terminal-price"><strong>'+priceText+'</strong><span class="'+(change>=0?'up':'down')+'">'+changeText+'</span><small>السعر حسب البيانات المستلمة</small><span class="sas-terminal-live '+(displayQuote?.is_delayed||String(quoteSource).toLowerCase().includes('cusp')?'sas-quote-delayed':'')+'"><i></i>'+escHtml((displayQuote?.is_delayed||String(quoteSource).toLowerCase().includes('cusp'))?'CUSP Wealth · آخر سعر منشور':quoteSource)+'</span><span class=\"sas-terminal-live sas-quote-delayed sas-price-freshness\"><i></i>قد يختلف وقت تحديث السعر عن وقت التحليل، وفقًا لمصدر البيانات وتوقيت التحديث.</span></div></section>'+
   '<nav class="sas-terminal-tabs" aria-label="أقسام تحليل السهم"><a href="#sasTerminalMetrics">المؤشرات</a><a href="#sasMovingAverages">المتوسطات</a><a href="#sasTerminalChart">الشارت</a><a href="#sasTerminalLevels">المستويات</a><a href="#sasTerminalNews">الأخبار</a><a href="#sasTerminalResearch">الأبحاث</a></nav>'+
   quickMetrics+averages+
   renderAvailableLevelPanel('مستويات السهم','القيم الفعلية المستلمة من محرك التحليل',[['الهدف',level(mini.target)],['وقف الخسارة',level(mini.stop)],['الزخم',mini.momentum],['السيولة',mini.liquidity]])+(mini.takeaway?'<section class="sas-terminal-panel"><p class="mini-takeaway">'+escHtml(mini.takeaway)+'</p></section>':'')+
   (candles.length?'<section class="sas-terminal-panel sas-terminal-chart-panel" id="sasTerminalChart"><div class="sas-terminal-section-title"><span>▥</span><div><b>الشارت</b><small>شموع السعر وحجم التداول من البيانات المستلمة</small></div><a class="gocharting-link" href="https://gocharting.com/stock/'+encodeURIComponent(symbol)+'" target="_blank" rel="noopener noreferrer">فتح الشارت الكامل ↗</a></div><div class="chart-box"><canvas id="stockCanvas" height="290"></canvas></div></section>':'')+
   (cuspHasData?'<section class="sas-terminal-panel" id="sasTerminalResearch"><div class="sas-terminal-section-title"><span>▤</span><div><b>بحث مالي إضافي</b><small>المصدر: CUSP Wealth</small></div></div><div class="ai-box cusp-research-card"><div class="cusp-research-head"><b>🏦 CUSP Wealth</b>'+(me?.admin?'<a href="'+escHtml(cuspUrl)+'" target="_blank" rel="noopener noreferrer">فتح المصدر ↗</a>':'')+'</div><p>'+escHtml(cuspText)+'</p>'+cuspFundamentalSummary+cuspMetricsHtml+'<small>مصدر بحثي إضافي؛ السعر قد يكون آخر سعر منشور على صفحة المصدر وليس بالضرورة سعرًا لحظيًا.</small></div></section>':'')+
   (Array.isArray(news)&&news.length?'<section class="sas-terminal-panel" id="sasTerminalNews"><div class="sas-terminal-section-title"><span>▣</span><div><b>آخر أخبار السهم</b><small>الأخبار التي تم استلامها من المصادر</small></div></div><div class="news-list">'+renderNewsItems(news,5)+'</div></section>':'')+
   '<div class="terminal-disclaimer">البيانات المتاحة جزئية وليست توصية شراء أو بيع. لا يتم إنشاء مؤشرات أو أسعار غير مستلمة من المصادر.</div>'+
  '</div>';
 pruneUnavailableAnalysis(el);
 requestAnimationFrame(()=>drawChart(candles));
 if(!candles.length){
  const box=el.querySelector('.chart-box');
  if(box)box.insertAdjacentHTML('beforeend','<div class="chart-status">لم تصل بيانات شموع تاريخية من الخادم؛ لا يمكن رسم شارت موثوق حاليًا.</div>');
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
 const exp=me.expires_at||me.trial_expires;
 const active=!!me.pro;
 const source=me.access?.source||null;
 const name=me.user?.first_name||me.user?.username||'متداول SAS PRO';
 const username=me.user?.username?'@'+me.user.username:'غير مضاف';
 const accountName=document.getElementById('accountName');
 if(accountName)accountName.textContent=name;
 const accountPlan=document.getElementById('accountPlan');
 if(accountPlan)accountPlan.textContent=(me.user?.username?username+' • ':'')+(source==='trial'?'تجربة مجانية':(me.user?.plan||me.plan||'عضوية SAS PRO'));
 const accessText=document.getElementById('profileAccessText');
 if(accessText)accessText.textContent=active?'الوصول مفعّل — عضويتك جاهزة للاستفادة من SAS PRO':'الوصول غير مفعّل — راجع حالة الاشتراك';
 const statusLine=document.querySelector('.sas-profile-statusline');
 if(statusLine){statusLine.classList.toggle('is-active',active);statusLine.classList.toggle('is-inactive',!active);}
 // The shared site-media loader owns the profile avatar icon so uploads and
 // resets stay consistent regardless of whether account data or media loads first.
 const status=active?'<span class="sas-profile-active">● فعّال</span>':'<span class="sas-profile-inactive">● غير فعّال</span>';
 const plan=source==='trial'?'تجربة مجانية':(me.plan||me.user?.plan||'SAS PRO');
 const expiryLabel=exp?fmtDate(exp):(source==='free'?'مفتوح':'—');
 const daysLabel=exp?fmtDays(new Date(),exp):(source==='free'?'دون انتهاء':'—');
 const cards=document.getElementById('accountCards');
 if(cards)cards.innerHTML='<button type="button" class="sas-profile-fact sas-profile-fact-button" onclick="toggleProfileDetails()" aria-controls="sasProfileDetails"><small>حالة الوصول</small><b>'+status+'</b><span>اضغط للتفاصيل ↙</span></button><button type="button" class="sas-profile-fact sas-profile-fact-button" onclick="toggleProfileDetails()" aria-controls="sasProfileDetails"><small>الباقة الحالية</small><b>'+escHtml(plan)+'</b><span>عرض معلومات العضوية ↙</span></button><button type="button" class="sas-profile-fact sas-profile-fact-button" onclick="toggleProfileDetails()" aria-controls="sasProfileDetails"><small>الانتهاء • المتبقي</small><b>'+escHtml(expiryLabel)+'</b><span>'+escHtml(daysLabel)+' • عرض التفاصيل ↙</span></button>';
 const details=document.getElementById('sasProfileDetails');
 if(details){
  const values=details.querySelectorAll('[data-profile-value]');
  values.forEach(el=>{const key=el.dataset.profileValue;const map={username,plan,expiry:expiryLabel,days:daysLabel,source:source==='trial'?'تجربة مجانية':source==='free'?'وصول مجاني':active?'اشتراك مدفوع':'لا يوجد وصول'};if(map[key]!==undefined)el.textContent=map[key];});
 }
 const copyBtn=document.getElementById('profileCopyBtn');
 if(copyBtn)copyBtn.disabled=!me.user?.username;
}
async function copyProfileIdentity(){
 const username=me.user?.username;
 const value=username?'@'+username:(me.user?.first_name||'');
 if(!value){alert('لا يوجد اسم مستخدم متاح للنسخ.');return;}
 try{
  if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(value);
  else{const field=document.createElement('textarea');field.value=value;field.style.position='fixed';field.style.opacity='0';document.body.appendChild(field);field.select();const ok=document.execCommand('copy');field.remove();if(!ok)throw new Error('تعذر النسخ');}
  const btn=document.getElementById('profileCopyBtn');
  if(btn){const previous=btn.innerHTML;btn.innerHTML='<span>✓</span><b>تم النسخ</b><small>'+escHtml(value)+'</small>';setTimeout(()=>{if(btn.isConnected)btn.innerHTML=previous;},1800);}
 }catch(e){alert('تعذر نسخ البيانات تلقائيًا. اسم الحساب: '+value);}
}
function toggleProfileDetails(){
 const panel=document.getElementById('sasProfileDetails');
 if(!panel)return;
 panel.hidden=!panel.hidden;
 document.querySelectorAll('.sas-profile-action[aria-controls="sasProfileDetails"]').forEach(btn=>btn.setAttribute('aria-expanded',String(!panel.hidden)));
 const avatar=document.querySelector('.sas-profile-avatar');
 if(avatar)avatar.classList.toggle('is-expanded',!panel.hidden);
}

function renderTrialCard(days){
 const d=Number(days||30);
 const description=document.getElementById("trialDescription");
 if(description)description.textContent="للمستخدمين الجدد: استمتع بـ "+d+" يومًا مجانًا. بعد موافقتك على الشروط، ستدخل إلى التطبيق مباشرة، ويمكنك الانضمام إلى القناة.";
 const button=document.getElementById("trialBtn");
 if(button)button.innerHTML='<span aria-hidden="true">🚀</span> قراءة الشروط والمتابعة <span aria-hidden="true">←</span>';
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
   if(accepted.trial_started){
    closeTerms();
    // On approval, enter the app and show welcome immediately; the channel prompt comes later.
    localStorage.removeItem('saspro_channel_onboarding_done');
    localStorage.setItem('saspro_onboarding_pending','1');
    me={...me,terms_accepted:true,pro:true,trial_available:false};
    document.getElementById('subscriptionPage').hidden=true;
    document.getElementById('terminalPage').hidden=false;
    showSASWelcome();
    await initTerminal();
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
   if(tg?.openInvoice)tg.openInvoice(d.invoice_link,(status)=>{if(status==='paid')localStorage.setItem('saspro_onboarding_pending','1');setTimeout(()=>{loadStarted=false;load();},1200);});
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
function showActionNotice(kind,message){
 const id=kind==='channel'?'channelActionNotice':'radarActionNotice';
 let box=document.getElementById(id);
 if(!box){box=document.createElement('div');box.id=id;box.className='sas-action-notice';box.setAttribute('role','status');const anchor=kind==='channel'?document.querySelector('#tab-dashboard .sas-action-card[onclick="openChannel()"]'):document.getElementById('radarDiagnostics');(anchor?.parentElement||document.getElementById('tab-'+(kind==='channel'?'dashboard':'radar')))?.insertBefore(box,anchor?.nextSibling||null);}
 box.textContent=message;box.hidden=false;
}
let sasChannelOpening=false;
async function openChannel(){
 if(sasChannelOpening)return;
 sasChannelOpening=true;
 const button=document.querySelector('#tab-dashboard .sas-action-card[onclick="openChannel()"]');
 if(button){button.disabled=true;button.setAttribute('aria-busy','true');}
 try{
  showActionNotice('channel','جاري تجهيز رابط القناة… لا تقفل التطبيق.');
  const d=await api('/api/channel/access',{timeoutMs:12000,retries:0});
  if(tg?.openTelegramLink)tg.openTelegramLink(d.url);
  else if(tg?.openLink)tg.openLink(d.url);
  else window.open(d.url,'_blank');
  const notice=document.getElementById('channelActionNotice');if(notice)notice.hidden=true;
 }catch(e){
  const msg=String(e?.message||'تعذر تجهيز رابط القناة.');
  showActionNotice('channel',msg.includes('الوقت المحدد')?'تأخر إنشاء رابط القناة. انتهت مهلة الاتصال؛ جرّب مرة ثانية.':msg);
 }
 finally{
  sasChannelOpening=false;
  if(button){button.disabled=false;button.removeAttribute('aria-busy');}
 }
}
async function buyPlan(k){
 if(!me.terms_accepted){openTerms('buy:'+k);return;}
 try{
  const d=await api('/api/subscription/invoice/'+encodeURIComponent(k),{method:'POST'});
  if(tg?.openInvoice)tg.openInvoice(d.invoice_link,(status)=>{if(status==='paid')localStorage.setItem('saspro_onboarding_pending','1');setTimeout(load,1200);});
  else if(tg?.openLink)tg.openLink(d.invoice_link);
 }catch(e){alert(e.message);}
}

const SAS_SITE_MEDIA_MANAGED_ITEMS_V2=[
 {slot:'icon-profile',label:'أيقونة ملف المستخدم الشخصي',fallback:'👤',target:'.sas-profile-avatar'},
 {slot:'icon-radar',label:'أيقونة زر الرادار',fallback:'📡',target:'.radar-icon'},
 {slot:'icon-channel',label:'أيقونة زر القناة',fallback:'✈️',target:'.channel-icon'},
 {slot:'icon-home',label:'أيقونة تبويب الرئيسية',fallback:'⌂',target:'.terminal-tabs button[data-tab="dashboard"]'},
 {slot:'icon-tab-radar',label:'أيقونة تبويب الرادار',fallback:'📡',target:'.terminal-tabs button[data-tab="radar"]'},
 {slot:'icon-search',label:'أيقونة تبويب البحث',fallback:'🔎',target:'.terminal-tabs button[data-tab="search"]'},
 {slot:'icon-news',label:'أيقونة أخبار السوق الأمريكي',fallback:'📰',target:'.sas-market-news-icon'},
 {slot:'icon-account',label:'أيقونة تبويب الحساب',fallback:'👤',target:'.terminal-tabs button[data-tab="account"]'},





 {slot:'icon-live-movers',label:'أيقونة بطاقة أعلى الأسهم حركة',fallback:'📈',target:'.sas-insight-grid .sas-insight-panel .sas-insight-title > span'},
 {slot:'icon-perp-movers',label:'أيقونة بطاقة الأكثر تحركًا خلال 24 ساعة',fallback:'↕️',target:'.sas-perp-movers-panel .sas-insight-title > span'},
 {slot:'icon-market-indicators',label:'أيقونة عنوان مؤشرات السوق',fallback:'📊',target:'.section-title .market-indicators-icon'},
 {slot:'icon-shariah',label:'أيقونة الشرعية في بطاقة السهم',fallback:'🕌',target:'.stock-shariah-icon'},
 {slot:'icon-disclaimer-alert',label:'أيقونة التنبيه القانوني أسفل الصفحة',fallback:'⛔',target:'.sas-disclaimer-bar .disclaimer-badge .disclaimer-icon'}
];
const SITE_MEDIA_ICON_TARGETS=Object.fromEntries(SAS_SITE_MEDIA_MANAGED_ITEMS_V2.map(item=>[item.slot,item.target]));
function renderMediaAdminCards(items={}){
 const grid=document.getElementById('mediaIconsAdminGrid');
 if(!grid)return;
 grid.innerHTML=SAS_SITE_MEDIA_MANAGED_ITEMS_V2.map(item=>{
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
  const restoreIcon=()=>{
   el.innerHTML=el.dataset.mediaOriginalHTML||'';
   el.classList.remove('has-managed-icon');
   if(terminalState.siteMedia)delete terminalState.siteMedia[slot];
  };
  if(el.matches('button')){
   if(!el.dataset.mediaOriginalLabel)el.dataset.mediaOriginalLabel=el.textContent.trim();
   const label=el.dataset.mediaOriginalLabel.replace(/^[^\p{L}\p{N}]+/u,'').trim();
   el.innerHTML='<img class="sas-managed-tab-icon" src="'+esc(item.url)+'" alt="" aria-hidden="true"><span>'+esc(label)+'</span>';
  }else{
   el.innerHTML='<img class="sas-managed-icon-image" src="'+esc(item.url)+'" alt="" aria-hidden="true">';
  }
  const managedImage=el.querySelector('img.sas-managed-icon-image,img.sas-managed-tab-icon');
  if(managedImage){managedImage.loading='lazy';managedImage.addEventListener('error',restoreIcon,{once:true});}
  el.classList.add('has-managed-icon');
 }
 return items;
}
let sasMediaRefreshTimer=null;
function startSASMediaAutoRefresh(){
 if(sasMediaRefreshTimer)clearInterval(sasMediaRefreshTimer);
 sasMediaRefreshTimer=setInterval(()=>{if(document.visibilityState==='visible')loadSiteMedia().catch(()=>{});},60000);
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')loadSiteMedia().catch(()=>{});});
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
  if(status)status.textContent='تم تحديث معاينات الصور والأيقونات ('+SAS_SITE_MEDIA_MANAGED_ITEMS_V2.length+' موضعًا).';
 }catch(e){if(status)status.textContent='تعذر تحميل الصور: '+(e.message||'خطأ غير معروف');}
}
async function deleteSiteMedia(slot){
 const label=slot==='hero'?'صورة الواجهة الرئيسية':(SAS_SITE_MEDIA_MANAGED_ITEMS_V2.find(item=>item.slot===slot)?.label||'الأيقونة');
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
 ['adminNewsPanel','▤','أخبار السوق','آخر أخبار السوق الأمريكي ومصادرها'],
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

async function loadAdminNewsPreview(){
 const box=document.getElementById('adminNewsPreview');
 const status=document.getElementById('adminNewsStatus');
 if(!box)return;
 box.innerHTML='<div class="empty-state">جارٍ تحميل الأخبار الموثقة…</div>';
 try{
  const data=await api('/api/market/news',{timeoutMs:26000,retries:0});
  const items=Array.isArray(data)?data:(Array.isArray(data?.items)?data.items:[]);
  const valid=items.filter(n=>n&&safeNewsUrl(n.url)&&String(n.headline||'').trim()).sort((a,b)=>Number(b.datetime||0)-Number(a.datetime||0));
  box.innerHTML=renderNewsItems(valid,12);
  if(status)status.textContent=valid.length+' خبر موثق • '+new Date().toLocaleTimeString('ar-SA',{timeZone:'Asia/Riyadh',hour:'2-digit',minute:'2-digit'})+' الرياض';
 }catch(e){
  box.innerHTML='<div class="empty-state">تعذر تحميل الأخبار: '+escHtml(e?.message||'خطأ غير معروف')+'</div>';
  if(status)status.textContent='تعذر التحديث';
 }
}
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
  adminNewsPanel:p.includes('settings')||p.includes('radar'),
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

// تحميل العلامة البصرية من مساحة التخزين الدائمة؛ لا يتدخل في بيانات الرادار.
loadSiteMedia().catch(()=>{});
startSASMediaAutoRefresh();

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
