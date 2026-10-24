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
   if(!weekday)return {key:'weekend',label:'السوق الأمريكي مغلق · عطلة نهاية الأسبوع',interval:24*60*60,active:false,weekend:true};
   if(mins>=570&&mins<960)return {key:'open',label:'السوق الأمريكي مفتوح',interval:5*60,active:true};
   if(mins>=510&&mins<570)return {key:'preopen',label:'ما قبل الافتتاح · رصد هادئ',interval:20*60,active:false};
   if(mins>=960&&mins<1200)return {key:'after',label:'ما بعد الإغلاق · رصد هادئ',interval:30*60,active:false};
   return {key:'night',label:'الوضع الليلي · تحديثات متباعدة',interval:45*60,active:false};
 }
 let nextAt=Date.now()+session().interval*1000,lastMode='';
 function paint(){
   const s=session(),now=Date.now();
   if(s.key!==lastMode){lastMode=s.key;nextAt=now+s.interval*1000;}
   const left=Math.max(0,Math.ceil((nextAt-now)/1000)),mm=String(Math.floor(left/60)).padStart(2,'0'),ss=String(left%60).padStart(2,'0');
   const countdown=$('sasRadarCountdown'),circleCountdown=$('sasRadarCircleCountdown'),title=$('sasRadarAutoTitle'),detail=$('sasRadarAutoDetail'),panel=$('sasRadarAutoPanel'),gauge=$('sasRadarGaugeProgress'),visual=document.querySelector('.sas-radar-visual'),circleCaption=visual&&visual.querySelector('.sas-radar-live-countdown small');
   if(countdown)countdown.textContent=s.weekend?'متوقف':mm+':'+ss;if(circleCountdown)circleCountdown.textContent=s.weekend?'مغلق':mm+':'+ss;if(circleCaption)circleCaption.textContent=s.weekend?'عطلة نهاية الأسبوع':'حتى التحديث';if(visual)visual.classList.toggle('sas-radar-weekend-closed',!!s.weekend);
   if(title)title.textContent=s.label;
   if(detail)detail.textContent=s.active?'فحص دوري كل 5 دقائق خلال الجلسة الرئيسية':s.weekend?'المسح الآلي للأسهم متوقف خلال عطلة نهاية الأسبوع':s.key==='preopen'?'فحص هادئ كل 20 دقيقة قبل الافتتاح':s.key==='after'?'فحص هادئ كل 30 دقيقة بعد الإغلاق':'فحص هادئ كل 45 دقيقة خارج الجلسة';
   if(panel){panel.dataset.state=s.key;panel.setAttribute('aria-label',s.weekend?s.label:s.label+'، التحديث التالي خلال '+mm+':'+ss);}
   if(gauge){const circumference=2*Math.PI*18;gauge.style.strokeDasharray=String(circumference);gauge.style.strokeDashoffset=String(s.active?circumference*(1-left/s.interval):circumference);}
   const status=$('sasRadarScheduleStatus');if(status)status.textContent=s.weekend?s.label+' · المسح الآلي متوقف حتى افتتاح السوق':s.label+' · التحديث التالي '+mm+':'+ss;
   // Pause automatic stock scans on weekends only; preserve weekday radar cadence.
   if(left===0){nextAt=now+s.interval*1000;if(!s.weekend&&typeof window.runRadar==='function'&&!window.__sasRadarAutoRun){window.__sasRadarAutoRun=true;Promise.resolve(window.runRadar(false)).catch(()=>{}).finally(()=>{window.__sasRadarAutoRun=false;});}}
 }
 function init(){
   const visual=document.querySelector('.sas-radar-visual');
   if(visual&&!visual.querySelector('.sas-radar-live-countdown')){
     const timer=document.createElement('span');timer.className='sas-radar-live-countdown';timer.innerHTML='<b id="sasRadarCircleCountdown">--:--</b><small>حتى التحديث</small>';visual.appendChild(timer);
   }
   const panel=$('sasRadarAutoPanel');
   if(panel&&!$('sasRadarScheduleStatus')){const small=document.createElement('small');small.id='sasRadarScheduleStatus';small.className='sas-radar-schedule-status';panel.appendChild(small);}
   paint();window.setInterval(paint,1000);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
