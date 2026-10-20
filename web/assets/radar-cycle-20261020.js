/* SAS PRO radar cycle UI helpers.
   The authoritative scan state and next-run countdown are owned by app-ticker-fix.
   This file must not start a second scheduler or overwrite real scan status. */
(function(){
 'use strict';
 if(window.__sasRadarCycleUI)return;
 window.__sasRadarCycleUI=true;
 const CACHE_TTL=90*1000;
 const candleCache=new Map();
 window.SASProCandleCache={
   get(key){const item=candleCache.get(String(key));if(!item)return null;if(Date.now()-item.at>CACHE_TTL){candleCache.delete(String(key));return null;}return item.value;},
   set(key,value){if(!key||!value)return;candleCache.set(String(key),{at:Date.now(),value});if(candleCache.size>300){for(const [k,v] of candleCache){if(Date.now()-v.at>CACHE_TTL)candleCache.delete(k);}while(candleCache.size>300)candleCache.delete(candleCache.keys().next().value);}},
   clear(key){if(key==null)candleCache.clear();else candleCache.delete(String(key));}
 };
 function mirrorCountdown(){
   const visual=document.querySelector('.sas-radar-visual');
   if(visual&&!visual.querySelector('.sas-radar-live-countdown')){
     const timer=document.createElement('span');
     timer.className='sas-radar-live-countdown';
     timer.innerHTML='<b id="sasRadarCircleCountdown">--:--</b><small>حتى التحديث</small>';
     visual.appendChild(timer);
   }
   const source=document.getElementById('sasRadarCountdown');
   const target=document.getElementById('sasRadarCircleCountdown');
   if(source&&target)target.textContent=source.textContent||'--:--';
 }
 function init(){
   mirrorCountdown();
   window.setInterval(mirrorCountdown,1000);
   const observer=new MutationObserver(mirrorCountdown);
   const visual=document.querySelector('.sas-radar-visual');
   if(visual)observer.observe(visual,{childList:true,subtree:true});
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
 else init();
})();