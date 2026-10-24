/* SAS PRO radar cycle UI: session-aware cadence, circular countdown, and short-lived candle cache. */
(function(){
 'use strict';
 if(window.__sasRadarCycleUI)return;window.__sasRadarCycleUI=true;
 const $=id=>document.getElementById(id);
 const TZ='America/New_York';
 const CACHE_TTL=90*1000;
 const candleCache=new Map();
 window.SASProCandleCache={
   get(key){const item=candleCache.get(String(key));if(!item)return null;if(Date.now()-item.at>CACHE_TTL){candleCache.delete(String(key));return null;}return item.value;},
   set(key,value){if(!key||!value)return; candleCache.set(String(key),{at:Date.now(),value});if(candleCache.size>300){for(const [k,v] of candleCache){if(Date.now()-v.at>CACHE_TTL)candleCache.delete(k);}while(candleCache.size>300)candleCache.delete(candleCache.keys().next().value);}},
   clear(key){if(key==null)candleCache.clear();else candleCache.delete(String(key));}
 };
 const parts=()=>{const a=new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date());const o={};a.forEach(x=>o[x.type]=x.value);return {day:o.weekday,h:+o.hour,m:+o.minute};};
 function session(){
   const p=parts(),mins=p.h*60+p.m,weekday=['Mon','Tue','Wed','Thu','Fri'].includes(p.day);
   // US equities weekend spans Friday after the regular close through Sunday.
   // Friday evening is still "Fri" in New York, so weekday-only detection is insufficient.
   if(!weekday||p.day==='Fri'&&mins>=960)return {key:'weekend',label:'السوق الأمريكي مغلق · عطلة نهاية الأسبوع',interval:24*60*60,active:false,weekend:true};
   if(mins>=570&&mins<960)return {key:'open',label:'السوق الأمريكي مفتوح',interval:5*60,active:true};
   if(mins>=510&&mins<570)return {key:'preopen',label:'ما قبل الافتتاح · رصد هادئ',interval:20*60,active:false};
   if(mins>=960&&mins<1200)return {key:'after',label:'ما بعد الإغلاق · رصد هادئ',interval:30*60,active:false};
   return {key:'night',label:'الوضع الليلي · تحديثات متباعدة',interval:45*60,active:false};
 }
 let nextAt=Date.now()+session().interval*1000,lastMode='',scanInProgress=false;
 function paint(){
   const s=session(),now=Date.now();
   if(s.key!==lastMode){lastMode=s.key;nextAt=now+s.interval*1000;}
   const left=Math.max(0,Math.ceil((nextAt-now)/1000)),mm=String(Math.floor(left/60)).padStart(2,'0'),ss=String(left%60).padStart(2,'0');
   const title=$('sasRadarAutoTitle'),detail=$('sasRadarAutoDetail'),panel=$('sasRadarAutoPanel'),visual=document.querySelector('.sas-radar-visual');
   const countdown=$('sasRadarCountdown'),circleCountdown=$('sasRadarCircleCountdown'),gauge=$('sasRadarGaugeProgress');
   if(visual)visual.classList.toggle('sas-radar-weekend-closed',!!s.weekend);
   // Weekend is a hard stopped state: never display a ticking countdown or imply that a scan is running.
   const gaugeWrap=countdown&&countdown.closest('.sas-radar-auto-gauge');
   const circleWrap=circleCountdown&&circleCountdown.closest('.sas-radar-live-countdown');
   if(gaugeWrap){gaugeWrap.hidden=!!s.weekend;gaugeWrap.style.setProperty('display',s.weekend?'none':'','important');}
   if(circleWrap){circleWrap.hidden=!!s.weekend;circleWrap.style.setProperty('display',s.weekend?'none':'','important');}
   if(countdown)countdown.textContent=s.weekend?'مغلق':scanInProgress?'فحص':mm+':'+ss;
   if(circleCountdown)circleCountdown.textContent=s.weekend?'مغلق':scanInProgress?'فحص':mm+':'+ss;
   if(title)title.textContent=s.weekend?'السوق الأمريكي مغلق':scanInProgress?'جارٍ تنفيذ المسح':s.label;
   if(detail)detail.textContent=s.weekend?'عطلة نهاية الأسبوع · المسح الآلي متوقف':scanInProgress?'يتم فحص الأسهم وتحديث النتائج':s.active?'الرصد نشط · الفحص التالي حسب الجدول':s.key==='preopen'?'الرصد مستمر قبل الافتتاح · كل 20 دقيقة':s.key==='after'?'الرصد مستمر بعد الإغلاق · كل 30 دقيقة':'الرصد الليلي · لا يوجد مسح مستمر';
   if(panel){panel.dataset.state=s.weekend?'weekend':scanInProgress?'scanning':s.key;panel.setAttribute('aria-label',s.weekend?s.label:scanInProgress?'جارٍ تنفيذ المسح':s.label+' · '+mm+':'+ss);}
   if(gauge){const circumference=2*Math.PI*18;gauge.style.strokeDasharray=String(circumference);gauge.style.strokeDashoffset=String(s.weekend||scanInProgress?circumference:circumference*(1-left/s.interval));}
   const status=$('sasRadarScheduleStatus');if(status)status.textContent=scanInProgress?'جارٍ تنفيذ المسح وتحديث النتائج':s.weekend?s.label+' · المسح الآلي متوقف':s.label+' · التحديث التالي '+mm+':'+ss;
   // Start one scan at zero; restart the countdown only after the scan settles,
   // so it cannot jump back while results are still being refreshed.
   if(left===0&&!scanInProgress){
     if(s.weekend){nextAt=now+s.interval*1000;return;}
     else if(typeof window.runRadar==='function'&&!window.__sasRadarAutoRun){
       scanInProgress=true;window.__sasRadarAutoRun=true;
       Promise.resolve().then(()=>window.runRadar(false)).catch(()=>{}).finally(()=>{
         window.__sasRadarAutoRun=false;scanInProgress=false;
         nextAt=Date.now()+session().interval*1000;
         paint();
       });
     }else{nextAt=now+s.interval*1000;}
   }
 }
 function init(){
   const panel=$('sasRadarAutoPanel'),visual=document.querySelector('.sas-radar-visual');
   const copy=panel&&panel.querySelector('.sas-radar-auto-copy');
   if(copy&&!$('sasRadarScheduleStatus')){const small=document.createElement('small');small.id='sasRadarScheduleStatus';small.className='sas-radar-schedule-status';copy.appendChild(small);}
   else if(copy&&$('sasRadarScheduleStatus')&&$('sasRadarScheduleStatus').parentElement!==copy){copy.appendChild($('sasRadarScheduleStatus'));}
   if(panel&&!$('sasRadarGaugeProgress')){
     const gauge=document.createElement('span');gauge.className='sas-radar-auto-gauge';gauge.setAttribute('aria-hidden','true');
     gauge.innerHTML='<svg viewBox="0 0 44 44"><circle class="sas-radar-gauge-track" cx="22" cy="22" r="18"></circle><circle id="sasRadarGaugeProgress" class="sas-radar-gauge-progress" cx="22" cy="22" r="18"></circle></svg><span id="sasRadarCountdown">--:--</span>';
     panel.prepend(gauge);
   }
   if(visual&&!$('sasRadarCircleCountdown')){
     const timer=document.createElement('span');timer.className='sas-radar-live-countdown';timer.innerHTML='<b id="sasRadarCircleCountdown">--:--</b><small>حتى الفحص التالي</small>';visual.appendChild(timer);
   }
   paint();window.setInterval(paint,1000);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
