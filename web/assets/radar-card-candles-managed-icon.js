/* SAS PRO: render genuine OHLC candles in radar cards and honor managed sharia icon */
(() => {
  'use strict';
  const seen = new WeakSet();
  const esc = (s) => String(s || '').replace(/[^A-Z0-9.\-]/gi, '').toUpperCase();
  const money = (v) => Number(v).toFixed(2);
  function draw(canvas, candles) {
    const w = Math.max(240, canvas.clientWidth || 320), h = 132, dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,h);
    ctx.fillStyle = '#061522'; ctx.fillRect(0,0,w,h);
    for(let i=0;i<=4;i++){const y=10+i*(h-20)/4;ctx.strokeStyle='rgba(112,170,195,.12)';ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
    const rows = candles.map(c => ({o:Number(c.open),h:Number(c.high),l:Number(c.low),c:Number(c.close)}))
      .filter(c => [c.o,c.h,c.l,c.c].every(Number.isFinite) && c.o>0 && c.h>0 && c.l>0 && c.c>0 && c.h>=Math.max(c.o,c.c) && c.l<=Math.min(c.o,c.c));
    if(rows.length < 2){ canvas.dataset.chartStatus='empty'; return false; }
    const hi=Math.max(...rows.map(x=>x.h)),lo=Math.min(...rows.map(x=>x.l)),pad=(hi-lo||hi*.01)*.08,min=lo-pad,max=hi+pad;
    const y=v=>h-12-(v-min)/(max-min)*(h-24),step=(w-16)/rows.length,bw=Math.max(2,Math.min(9,step*.58));
    rows.forEach((c,i)=>{const x=8+i*step+step/2,up=c.c>=c.o;ctx.strokeStyle=ctx.fillStyle=up?'#2cdda6':'#ff6b83';ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(x,y(c.h));ctx.lineTo(x,y(c.l));ctx.stroke();ctx.fillRect(x-bw/2,Math.min(y(c.o),y(c.c)),bw,Math.max(1.5,Math.abs(y(c.o)-y(c.c))));});
    ctx.fillStyle='#b6d1df';ctx.font='11px sans-serif';ctx.textAlign='right';ctx.fillText('OHLC · آخر إغلاق $'+money(rows[rows.length-1].c),w-7,12);
    canvas.dataset.chartStatus='ready'; return true;
  }
  async function hydrate(card) {
    if(seen.has(card)) return; seen.add(card);
    const symbol=esc(card.querySelector('.stock-head > div > b')?.textContent);
    const svg=card.querySelector('.sas-card-price-trend .sas-card-uptrend');
    if(symbol && svg){
      const wrap=svg.parentElement; const canvas=document.createElement('canvas');
      canvas.className='sas-card-candles';canvas.setAttribute('role','img');canvas.setAttribute('aria-label','شموع يابانية من بيانات OHLC '+symbol);
      const status=document.createElement('div');status.className='sas-card-candle-status';status.textContent='جارٍ تحميل بيانات الشموع…';
      svg.replaceWith(canvas);wrap.insertBefore(status,canvas.nextSibling);
      try{
        const response=await fetch('/api/stocks/'+encodeURIComponent(symbol)+'/chart',{headers:{Accept:'application/json'},cache:'no-store'});
        if(!response.ok) throw new Error('chart unavailable');
        const data=await response.json();
        const candles=Array.isArray(data)?data:(Array.isArray(data?.candles)?data.candles:Array.isArray(data?.data)?data.data:[]);
        if(draw(canvas,candles)){status.remove();}else{canvas.hidden=true;status.textContent='بيانات OHLC غير كافية لعرض الشموع';}
      }catch(_){canvas.hidden=true;status.textContent='تعذر تحميل بيانات الشموع حاليًا';}
    }
    const icon=card.querySelector('.stock-shariah-icon');
    const managed=(typeof terminalState !== 'undefined' ? terminalState.siteMedia?.['icon-shariah']?.url : null);
    if(icon && managed && !icon.querySelector('img')){
      const img=document.createElement('img');img.src=managed;img.alt='';img.setAttribute('aria-hidden','true');img.className='sas-managed-icon-image';
      icon.replaceChildren(img);icon.classList.add('has-managed-icon');
    }
  }
  function applyManagedIcons(root=document){\n    const managed=(typeof terminalState !== 'undefined' ? terminalState.siteMedia?.['icon-shariah']?.url : null);\n    if(!managed)return;\n    root.querySelectorAll?.('.stock-card .stock-shariah-icon').forEach(icon=>{if(icon.querySelector('img'))return;const img=document.createElement('img');img.src=managed;img.alt='';img.setAttribute('aria-hidden','true');img.className='sas-managed-icon-image';icon.replaceChildren(img);icon.classList.add('has-managed-icon');});\n  }\n  function scan(root=document){root.querySelectorAll?.('.stock-card').forEach(card=>hydrate(card));applyManagedIcons(root);}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>scan(),{once:true});else scan();
  const observer=new MutationObserver(records=>records.forEach(r=>r.addedNodes.forEach(n=>{if(n.nodeType===1){if(n.matches?.('.stock-card'))hydrate(n);scan(n);}})));
  observer.observe(document.documentElement,{childList:true,subtree:true});
  document.addEventListener('sas-site-media-updated',()=>scan());
})();
