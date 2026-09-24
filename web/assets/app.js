const tg=window.Telegram&&window.Telegram.WebApp;
if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#020912');tg.setBackgroundColor('#020912');}catch(e){}}
const getInitData=()=>tg?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')||new URLSearchParams(location.search).get('tgWebAppData')||'';
const headers=()=>({'X-Telegram-Init-Data':getInitData()});
async function api(path,opt={}){opt.headers=Object.assign(headers(),opt.headers||{});const r=await fetch(path,opt);if(!r.ok){let msg='تعذر تنفيذ العملية';try{const d=await r.json();msg=d.detail||d.message||msg;}catch(e){try{const t=(await r.text()).trim();if(t)msg=t;}catch(_){} }throw new Error(msg);}return r.json();}
const fmtDate=v=>v?new Date(v).toLocaleDateString('ar-SA'):'—';
const fmtDays=(a,b)=>{if(!a||!b)return'—';const n=Math.ceil((new Date(b)-new Date(a))/86400000);return n>0?n+' يوم':'منتهي';};
let me=null,plans=null,termAction=null;

async function load(){
 try{
  me=await api('/api/me');
  if(me.admin){document.getElementById('subscriptionPage').hidden=true;document.getElementById('adminPage').hidden=false;
   if((me.admin_permissions||[]).includes('admins')){document.getElementById('staffPanel').hidden=false;await loadStaff();}
   await adminRefresh();return;}
  document.getElementById('userName').textContent=me.user?.first_name||me.user?.username||'مستخدم SAS PRO';
  renderStatus(me);
  const cfg=await api('/api/subscription/config');
  plans=await api('/api/subscription/plans');
  renderTrialCard(cfg.trial_days);
  document.getElementById('paidPlansSection').hidden=!cfg.paid_plans_visible;
  renderPlans(plans.plans||{});
 }catch(e){document.body.innerHTML='<div class="fatal">تعذر التحقق من Telegram. افتح SAS PRO من داخل Telegram.</div>';}
}

function renderTrialCard(days){document.querySelector("#trialCard p").textContent=days+" أيام • تجربة مجانية • يمكن تمديدها أو تعديل مدتها من الإدارة.";}

function renderStatus(x){
 const active=!!x.pro;
 document.getElementById('subStatus').textContent=active?'🟢 فعال':'🔒 غير مشترك';
 document.getElementById('subStatus').className=active?'ok':'bad';
 document.getElementById('subPlan').textContent=active?(x.user?.plan||'اشتراك'):'—';
 document.getElementById('subStart').textContent='—';
 document.getElementById('subEnd').textContent=x.expires_at?fmtDate(x.expires_at):'—';
 document.getElementById('subDays').textContent=x.expires_at?fmtDays(new Date(),x.expires_at):'—';
 document.getElementById('trialState').textContent=x.trial_available?'متاحة مرة واحدة':(x.trial_expires?'منتهية/مستخدمة':'غير متاحة');
 document.getElementById('terminalBtn').hidden=!active;
 if(x.trial_expires&&new Date(x.trial_expires)>new Date()){document.getElementById('trialState').textContent='🎁 فعالة حتى '+fmtDate(x.trial_expires);}
 if(!x.trial_available)document.getElementById('trialBtn').disabled=true;
}

function renderPlans(p){
 const names={monthly:'شهري','3month':'3 أشهر','6month':'6 أشهر',yearly:'سنة'};
 const icons={monthly:'🟢','3month':'🔷','6month':'💎',yearly:'👑'};
 document.getElementById('plans').innerHTML=Object.entries(p).map(([k,x])=>
  '<article class="plan"><div class="plan-icon">'+(icons[k]||'💠')+'</div><b>'+names[k]+'</b><strong>'+x.sar+' ريال</strong><span>'+x.days+' يوم</span>'+
  '<em>'+(x.stars>0?x.stars+' ⭐':'سعر Stars غير مضبوط')+'</em>'+
  '<button '+(x.stars>0?'':'disabled')+' onclick="buyPlan(\''+k+'\')">الدفع عبر Stars</button></article>'
 ).join('');
}

function openTerms(action){termAction=action;document.getElementById('termsAgree').checked=false;document.getElementById('termsContinue').disabled=action==='view';document.getElementById('termsText').textContent='جاري تحميل الشروط...';document.getElementById('termsModal').hidden=false;api('/api/subscription/plans').then(d=>{document.getElementById('termsText').textContent=d.terms_text||'';}).catch(e=>{document.getElementById('termsText').textContent=e.message;});}
function toggleTermsButton(){if(termAction!=='view')document.getElementById('termsContinue').disabled=!document.getElementById('termsAgree').checked;}
function closeTerms(){document.getElementById('termsModal').hidden=true;termAction=null;}
async function continueTerms(){
 if(!document.getElementById('termsAgree').checked)return;
 try{
  await api('/api/terms/accept',{method:'POST'});
  if(termAction==='trial'){
   const d=await api('/api/subscription/trial',{method:'POST'});
   closeTerms();
   if(tg?.openLink)tg.openLink(d.channel_link);
   else window.open(d.channel_link,'_blank');
  }else if(termAction?.startsWith('buy:')){
   const plan=termAction.slice(4);
   const d=await api('/api/subscription/invoice/'+encodeURIComponent(plan),{method:'POST'});
   closeTerms();
   if(tg?.openInvoice)tg.openInvoice(d.invoice_link,()=>setTimeout(load,1200));
   else if(tg?.openLink)tg.openLink(d.invoice_link);
  }else closeTerms();
 }catch(e){alert(e.message);}
}
async function openTerminal(){
 try{
  const d=await api('/api/terminal/access');
  if(tg?.openLink)tg.openLink(d.url);
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
 if(p.includes('users')){tasks.push(loadAdminStats(),adminSearch());}
 if(p.includes('settings')){document.getElementById('planEditor').closest('.admin-panel').hidden=false;document.getElementById('plansEditorPanel').hidden=false;tasks.push(loadAdminPlans(),loadSubscriptionConfig());}
 else {document.getElementById('planEditor').closest('.admin-panel').hidden=true;document.getElementById('plansEditorPanel').hidden=true;document.getElementById('planEditor').closest('.admin-panel').previousElementSibling.hidden=true;}
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
async function loadAdminStats(){const d=await api('/api/admin/overview');document.getElementById('adminStats').innerHTML=[['active','🟢 النشطون'],['expired','🔴 المنتهية'],['trial_users','🎁 التجارب'],['new_users','👥 الجدد'],['payments','💳 المدفوعات'],['stars','⭐ Stars']].map(x=>'<div><small>'+x[1]+'</small><strong>'+d[x[0]]+'</strong></div>').join('');}
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
async function saveSubscriptionConfig(){
 const paid=!!document.getElementById('paidPlansVisible').checked;
 const days=Number(document.getElementById('trialDays').value||3);
 try{
  const d=await api('/api/admin/subscription-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paid_plans_visible:paid,trial_days:days})});
  document.getElementById('plansEditorPanel').hidden=!d.paid_plans_visible;
  alert(d.paid_plans_visible?'تم إظهار الاشتراكات المدفوعة.':'تم إخفاء الاشتراكات المدفوعة وإبقاء المجاني فقط.');
 }catch(e){alert(e.message);}
}
document.addEventListener('change',e=>{if(e.target?.id==='paidPlansVisible')document.getElementById('plansEditorPanel').hidden=!e.target.checked;});
load();