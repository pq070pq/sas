(() => {
const $ = id => document.getElementById(id);
const form = $('searchForm'), query = $('query'), formType = $('formType'), period = $('period'), sort = $('sort');
const status = $('status'), company = $('company'), overview = $('overview'), resultsSection = $('resultsSection'), results = $('results'), btn = $('searchBtn');
let controller = null;
function escapeHTML(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function setStatus(title,detail,kind=''){status.className='status '+kind;status.innerHTML='<div class="status-icon">'+(kind==='error'?'!':kind==='loading'?'◌':'⌕')+'</div><div><strong>'+escapeHTML(title)+'</strong><p>'+escapeHTML(detail)+'</p></div>';status.hidden=false;}
function eventTag(f){
const s=((f.form||'')+' '+(f.description||'')).toLowerCase();
if(/reverse split|reverse stock split|stock split/.test(s))return ['تقسيم أسهم','warn'];
if(/424b5|s-1|s-3|offering|prospectus|at-the-market|atm offering/.test(s))return ['طرح / إصدار أسهم','risk'];
if(/delist|delisting|non-compliance|deficiency|nasdaq listing|nyse listing/.test(s))return ['مشكلة إدراج أو التزام','risk'];
if(/8-k/.test(s))return ['إفصاح عن تطور مهم','warn'];
if(/10-q/.test(s))return ['تقرير ربع سنوي',''];
if(/10-k/.test(s))return ['تقرير سنوي',''];
return ['إفصاح شركة',''];
}
function plainSummary(f){
const title=String(f.description||'').trim();
const text=(title+' '+String(f.items||'')).toLowerCase();
const itemText=String(f.items||'').match(/\b\d{1,2}\.\d{2}\b/g)||[];
const itemMap={
'1.01':'توقيع اتفاقية أو عقد مهم جديد',
'1.02':'إنهاء أو إلغاء اتفاقية مهمة',
'1.03':'إجراءات مرتبطة بالإفلاس أو إعادة الهيكلة',
'2.01':'إتمام صفقة أو استحواذ أو بيع أصل مهم',
'2.02':'إعلان نتائج مالية أو تحديث نتائج الشركة',
'2.03':'إنشاء التزام مالي كبير أو اقتراض جديد',
'2.04':'تغيّر أو تعثّر في التزامات مالية',
'2.05':'خطة لإعادة الهيكلة أو إغلاق أو تقليص أعمال',
'2.06':'تسجيل خسارة أو انخفاض في قيمة أصل',
'3.01':'مشكلة تتعلق باستمرار إدراج السهم أو قواعد السوق',
'5.02':'تغيير في الإدارة أو مجلس الإدارة',
'5.07':'نتائج تصويت المساهمين',
'7.01':'إعلان معلومات للمستثمرين أو السوق',
'8.01':'إعلان تطور مهم آخر',
'9.01':'مرفقات أو مستندات داعمة للإفصاح'
};
let headline='', meaning='', impact='';
if(/reverse split|reverse stock split/.test(text)){headline='الشركة أعلنت عن تجميع أسهمها';meaning='يتم جمع عدة أسهم في عدد أقل، وقد يتغير عدد الأسهم وسعرها الحسابي تبعًا لنسبة التجميع.';impact='هذا الإجراء وحده ما يعني أن قيمة استثمارك زادت؛ راجع النسبة والتاريخ والتفاصيل.';}
else if(/stock split/.test(text)){headline='الشركة أعلنت عن تقسيم أسهمها';meaning='يتغير عدد الأسهم وسعر السهم الحسابي حسب نسبة التقسيم.';impact='التقسيم بحد ذاته ما يثبت ارتفاع قيمة الاستثمار أو انخفاضها.';}
else if(/424b5|s-1|s-3|offering|prospectus|at-the-market|atm offering/.test(text)){headline='الإفصاح قد يكون متعلقًا بطرح أو إصدار أسهم';meaning='قد تكون الشركة بصدد جمع تمويل من المستثمرين. نحتاج مراجعة شروط الطرح والمستند للتأكد.';impact='إذا كان فيه إصدار أسهم جديدة، فقد تتأثر حصة المساهمين الحاليين؛ لا يمكن الجزم من نوع النموذج وحده.';}
else if(/delist|delisting|non-compliance|deficiency/.test(text)){headline='الإفصاح قد يتعلق بملاحظة على إدراج الشركة أو التزامها';meaning='قد تكون الجهة المشغلة للسوق نبهت الشركة إلى شرط يحتاج معالجة.';impact='تحقق من المهلة والإجراء المطلوب؛ نوع النموذج وحده ما يوضح هل تم حل المشكلة.';}
else if(/^10-q$/.test(String(f.form||'').toLowerCase())){headline='تقرير مالي ربع سنوي';meaning='يعرض أداء الشركة خلال الربع، مثل الإيرادات والأرباح والخسائر والتدفقات النقدية.';impact='لازم مقارنة الأرقام بالفترة السابقة وتوقعات الشركة لمعرفة إذا كان الأداء تحسن أو تراجع.';}
else if(/^10-k$/.test(String(f.form||'').toLowerCase())){headline='تقرير مالي سنوي';meaning='يعرض نتائج السنة ومخاطر الشركة ووضعها المالي.';impact='اقرأ قسم المخاطر والنتائج المالية لمعرفة أبرز التغييرات.';}
else if(itemText.length){const descriptions=[...new Set(itemText.map(code=>itemMap[code]).filter(Boolean))];if(descriptions.length){headline=descriptions.join('؛ ');meaning='هذه البنود توضح موضوع الإفصاح بشكل مبدئي، وليست بديلًا عن قراءة التفاصيل.';impact='الأثر على السهم يعتمد على تفاصيل الحدث وحجمه وشروطه، ولا يمكن تحديده من رقم البند فقط.';}}
if(!headline){headline=title||('إفصاح من نوع '+String(f.form||'غير محدد'));meaning='وصف الإيداع المتاح ما يكفي لتحديد الحدث بدقة، لذلك ما راح نفترض تفاصيل غير مذكورة.';impact='افتح المستند الأصلي لمعرفة وش صار بالضبط وأي أثر معلن على الشركة.';}
let impactLabel='الأثر غير واضح';
let impactClass='unknown';
const evidence=(title+' '+String(f.items||'')).toLowerCase();
if(/raises guidance|raised guidance|record revenue|record profit|net income increased|revenue increased|profit increased|contract award|awarded a contract|definitive agreement to acquire/.test(evidence)){
 impactLabel='إشارة إيجابية مذكورة في الوصف'; impactClass='positive';
}else if(/going concern|bankruptcy|chapter 11|defaulted|covenant violation|material weakness|restatement|delisting|terminated agreement|withdraws guidance|reduced guidance|revenue declined|net loss increased/.test(evidence)){
 impactLabel='إشارة سلبية مذكورة في الوصف'; impactClass='negative';
}
if(/^10-q$|^10-k$/i.test(String(f.form||''))){
 impactLabel='غير محدد — يحتاج مراجعة الأرقام'; impactClass='unknown';
}
if(/CURRENT REPORT/i.test(title) && !itemText.length){
 headline='إفصاح جديد للشركة — تفاصيل الحدث غير ظاهرة';
 meaning='البيانات المعروضة هنا ما تحدد وش صار بالضبط. ما راح نفترض أنه عقد أو خبر إيجابي أو سلبي بدون نص واضح.';
 impact='الأثر على السهم غير محدد من المعلومات المتاحة. افتح المستند الأصلي لمعرفة الحدث قبل الحكم عليه.';
 impactLabel='لا يمكن تحديده من البيانات الحالية'; impactClass='unknown';
}
return {headline,meaning,impact,impactLabel,impactClass,hasDetails:!!(title||itemText.length)};
}
function renderOverview(items, co, q){
 const filings=items||[];
 const text=filings.map(f=>[f.form,f.description,f.items,f.analysis&&f.analysis.eventSummary].join(' ')).join(' ').toLowerCase();
 const checks=[
 ['هل رُصد تقسيم عكسي؟',/reverse split|reverse stock split/.test(text)],
 ['هل يوجد طرح أو إصدار أسهم؟',/s-1|s-3|424b5|offering|at.the.market|atm offering/.test(text)],
 ['هل توجد مشكلة إدراج أو عدم امتثال؟',/non.compliance|deficiency notice|delisting|minimum bid price/.test(text)],
 ['هل يوجد استحواذ أو اتفاق مهم؟',/acquisition|merger agreement|material agreement|item 1.01|item 2.01/.test(text)],
 ['هل تغيّرت الإدارة؟',/resignation|appointed|chief executive officer|item 5.02/.test(text)],
 ['هل توجد تقارير مالية؟',/10-q|10-k|earnings|financial results|quarterly report/.test(text)]
 ];
 overview.innerHTML='<div class="overview-head"><div><div class="eyebrow">SAS PRO • COMPANY CHECK</div><h2>فحص الشركة باختصار</h2><p>ملخص من الإفصاحات التي ظهرت في البحث الحالي.</p></div><div class="overview-total"><b>'+filings.length+'</b><span>إفصاحًا</span></div></div><div class="overview-company"><b>'+escapeHTML((co&&co.name)||q)+'</b><span>'+escapeHTML((co&&co.ticker)||String(q||'').toUpperCase())+'</span></div><div class="overview-checks">'+checks.map(c=>'<div class="overview-check"><b>'+escapeHTML(c[0])+'</b><span class="'+(c[1]?'found':'not-found')+'">'+(c[1]?'يوجد إفصاح ذو صلة — راجع التفاصيل':'لم يُرصد في النتائج المعروضة')+'</span></div>').join('')+'</div><p class="overview-note">«لم يُرصد» لا يعني تأكيد عدم وجود الحدث؛ بل يعني أنه لم يظهر ضمن نتائج البحث الحالية. راجع المستند الأصلي قبل اتخاذ أي قرار.</p>';
 overview.hidden=false;
}
function render(items, data){results.innerHTML='';if(sort.value==='oldest')items.sort((a,b)=>(a.filingDate||'').localeCompare(b.filingDate||''));else items.sort((a,b)=>(b.filingDate||'').localeCompare(a.filingDate||''));$('resultCount').textContent=items.length+' مستند';$('resultSummary').textContent='ملخصات مبسطة للإفصاحات الرسمية — افتح المستند للتفاصيل';if(!items.length){results.innerHTML='<div class="empty">لا توجد إفصاحات تطابق الفلاتر المحددة في الفترة المختارة.</div>';return;}for(const f of items){const [tag,kind]=eventTag(f);const summary=plainSummary(f);if(f.analysis){summary.headline=f.analysis.eventSummary||summary.headline;summary.meaning=f.analysis.eventSummary||summary.meaning;summary.impact=f.analysis.impactEvidence||summary.impact;summary.impactLabel=f.analysis.impactLabel||summary.impactLabel;summary.impactClass=f.analysis.impactClass||summary.impactClass;}const card=document.createElement('article');card.className='filing';card.innerHTML='<div class="filing-top"><div class="filing-title">'+escapeHTML(summary.headline)+'</div><div class="filing-date">'+escapeHTML(f.filingDate||'—')+'</div></div><div class="chips"><span class="chip form">'+escapeHTML(f.form||'SEC')+'</span><span class="chip '+kind+'">'+tag+'</span>'+(f.reportDate?'<span class="chip">الفترة: '+escapeHTML(f.reportDate)+'</span>':'')+'</div><div class="impact-label '+escapeHTML(summary.impactClass)+'"><b>تصنيف الأثر:</b> '+escapeHTML(summary.impactLabel)+'</div><div class="plain-summary"><div><b>وش صار حسب المستند؟</b><p>'+escapeHTML(summary.meaning)+'</p></div><div><b>الدليل:</b><p>'+escapeHTML(summary.impact)+'</p><p class="summary-note">'+escapeHTML(f.analysis?(f.analysis.textAvailable?'تمت قراءة نص المستند':'لم يتوفر نص المستند؛ الأثر غير محسوم'):'ملخص أولي من البيانات المتاحة')+'</p></div></div><div class="filing-desc">'+(f.items?'<span class="muted-label">أرقام البنود في الإفصاح:</span> '+escapeHTML(f.items):'')+' <span class="summary-note">ملخص آلي مبسط من وصف الإيداع والبنود المتاحة؛ قد لا يوضح كل التفاصيل.</span></div><div class="filing-actions"><a class="primary" href="'+escapeHTML(f.documentUrl||f.filingUrl)+'" target="_blank" rel="noopener">اقرأ التفاصيل والمستند الأصلي ↗</a><a href="'+escapeHTML(f.filingUrl)+'" target="_blank" rel="noopener">كل مرفقات الإيداع ↗</a></div>';results.appendChild(card);}}
form.addEventListener('submit', async e=>{e.preventDefault();const q=query.value.trim();if(!q)return;if(controller)controller.abort();controller=new AbortController();btn.disabled=true;btn.textContent='جارٍ البحث…';resultsSection.hidden=true;company.hidden=true;overview.hidden=true;setStatus('جارٍ جلب الإفصاحات الحقيقية','الاتصال بخدمة بحث SEC EDGAR عبر SAS PRO…','loading');try{const params=new URLSearchParams({q,form:formType.value,days:period.value,limit:'100'});const response=await fetch('/api/disclosures/search?'+params.toString(),{signal:controller.signal,headers:{Accept:'application/json'}});const data=await response.json();if(!response.ok)throw new Error(data.detail||'تعذر الوصول إلى بيانات SEC.');if(data.error)throw new Error(data.error);const co=data.company||{};company.innerHTML='<div><div class="company-name">'+escapeHTML(co.name||q)+'</div><div class="company-meta">CIK '+escapeHTML(co.cik||'—')+' · SEC EDGAR · '+escapeHTML(data.asOf||'')+'</div></div><span class="company-ticker">'+escapeHTML(co.ticker||q.toUpperCase())+'</span>';company.hidden=false;renderOverview(data.filings||[],co,q);resultsSection.hidden=false;render(data.filings||[],data);setStatus('اكتمل البحث','تم جلب '+(data.filings||[]).length+' إيداعًا من SEC EDGAR. تصنيف الأحداث آلي وليس حكمًا نهائيًا.');}catch(err){if(err.name==='AbortError')return;setStatus('تعذر إكمال البحث',err.message||'خطأ غير معروف. أعد المحاولة بعد قليل.','error');}finally{btn.disabled=false;btn.innerHTML='<span class="btn-icon">⌕</span> افحص الإفصاحات';}});
})();