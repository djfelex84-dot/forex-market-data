(function(){
'use strict';
/* ---------- language ---------- */
const LANGS=[['et','Eesti'],['en','English'],['ru','Русский']];
const LOCALE={et:'et-EE',en:'en-IE',ru:'ru-RU'};
const MONTHS={
  ru:['январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь'],
  et:['jaanuar','veebruar','märts','aprill','mai','juuni','juuli','august','september','oktoober','november','detsember'],
  en:['January','February','March','April','May','June','July','August','September','October','November','December']};
let LANG=(()=>{try{const l=localStorage.getItem('kontor.lang');if(l&&MONTHS[l])return l}catch(e){}const n=(navigator.language||'').slice(0,2);return MONTHS[n]?n:'et'})();
// Strings are written in Russian in the code and looked up in i18n.js for Estonian and English.
function T(s,...a){let out=(LANG!=='ru'&&window.I18N&&window.I18N[LANG]&&window.I18N[LANG][s])||s;a.forEach((v,i)=>{out=out.split('{'+i+'}').join(v)});return out}
let EUR,NUM;
function setLocale(){EUR=new Intl.NumberFormat(LOCALE[LANG],{style:'currency',currency:'EUR',minimumFractionDigits:2});NUM=LOCALE[LANG];document.documentElement.lang=LANG}
setLocale();

/* ---------- constants ---------- */
const COLS=['products','purchases','invoices','expenses','adjustments','partners','bank'];
const DEF_RATES=[{rate:24,label:''},{rate:13,label:''},{rate:9,label:''},{rate:0,label:''}];
const EXP_CATS=[['ads','Реклама'],['web','Сайт и сервисы'],['shipping','Доставка и почта'],['packaging','Упаковка'],['bank','Банк и платежи'],['accounting','Бухгалтерия'],['rent','Аренда'],['transport','Транспорт'],['telecom','Связь'],['office','Офис'],['other','Прочее']];
const catName=k=>{const c=EXP_CATS.find(x=>x[0]===k);return c?T(c[1]):(k||'')};
const PROD_CATS=['Футболки','Худи и свитшоты','Штаны','Головные уборы','Аксессуары'];
const TABS=[['home','Обзор'],['stock','Склад'],['sales','Продажи'],['buys','Закупки'],['exp','Расходы'],['bank','Банк'],['vat','НДС'],['partners','Партнёры'],['set','Настройки']];
const EU=['EE','LV','LT','FI','SE','DE','PL','NL','DK','FR','IT','ES','PT','BE','AT','IE','CZ','SK','SI','HU','RO','BG','HR','GR','CY','MT','LU'];
const NONEU=['NO','CH','GB','US','CN','TR','UA'];
const isEU=c=>EU.includes(c);
function countryName(c){if(!c)return'';if(c==='XX')return T('Другая страна');try{return new Intl.DisplayNames([LOCALE[LANG]],{type:'region'}).of(c)}catch(e){return c}}
const SALE_T={ee:'Эстония, НДС по ставке',eu_b2c:'ЕС, частное лицо (эстонский НДС)',eu_b2b:'ЕС, фирма с VAT-номером (0%)',export:'Вне ЕС, экспорт (0%)'};
const BUY_T={ee:'Эстония, НДС в счёте',eu_goods:'Товар из ЕС (обратное начисление)',eu_services:'Услуга из ЕС (обратное начисление)',foreign_services:'Услуга из-за пределов ЕС (обратное начисление)',none:'Без НДС'};
const RC=['eu_goods','eu_services','foreign_services'];
const IGNORE=[['tax','Налоги (EMTA)'],['transfer','Перевод между своими счетами'],['owner','Взнос или выплата владельцу'],['loan','Кредит или займ'],['other','Другое']];

/* ---------- state ---------- */
const today=()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')};
const S={me:null,company:{},tab:'home',month:today().slice(0,7),q:'',fab:false,loaded:false,online:true};
for(const c of COLS)S[c]=[];
try{const t=localStorage.getItem('kontor.tab');if(t&&TABS.some(x=>x[0]===t))S.tab=t}catch(e){}

const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const r2=x=>Math.round((+x||0)*100)/100;
const money=x=>`<span class="money">${EUR.format(r2(x))}</span>`;
const eur=x=>EUR.format(r2(x));
const qfmt=x=>{x=+x||0;return Number.isInteger(x)?String(x):x.toLocaleString(NUM,{maximumFractionDigits:3})};
const mLabel=m=>{const[y,mm]=m.split('-');return MONTHS[LANG][+mm-1]+' '+y};
const dfmt=d=>d?String(d).split('-').reverse().join('.'):'';
const inMonth=d=>(d||'').startsWith(S.month);
const byDateDesc=(a,b)=>(b.date||'').localeCompare(a.date||'')||(b.createdAt||0)-(a.createdAt||0);
const norm=s=>String(s||'').toLowerCase().replace(/\s+/g,' ').trim();
const normVat=s=>String(s||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
const rates=()=>(Array.isArray(S.company.vatRates)&&S.company.vatRates.length?S.company.vatRates:DEF_RATES);
const defRate=()=>S.company.defaultRate??rates()[0].rate;
const unitDefault=()=>T('шт');

/* ---------- server ---------- */
async function api(method,url,body,raw){
  const opt={method,credentials:'same-origin',headers:{'X-Kontor':'1'}};
  if(raw){opt.body=raw.body;Object.assign(opt.headers,raw.headers)}
  else if(body!==undefined){opt.body=JSON.stringify(body);opt.headers['Content-Type']='application/json'}
  let r;try{r=await fetch(url,opt)}catch(e){throw{code:'offline'}}
  const j=await r.json().catch(()=>({}));
  if(r.status===401&&url!=='/api/login'){S.me=null;renderLogin();throw{code:'auth'}}
  if(!r.ok)throw{code:j.error||'error',status:r.status};
  return j;
}
function apply(col,id,data){
  if(col==='settings'){if(id==='company')S.company=data||{};scheduleRender();return}
  const a=S[col];if(!a)return;const i=a.findIndex(x=>x.id===id);
  if(data){if(i>-1)a[i]=data;else a.push(data)}else if(i>-1)a.splice(i,1);
  scheduleRender();
}
const store={
  async add(col,data){const r=await api('POST','/api/doc/'+col,data);apply(col,r.id,r.data);return r.id},
  async set(col,id,data){const r=await api('PUT',`/api/doc/${col}/${encodeURIComponent(id)}`,data);apply(col,id,r.data)},
  async update(col,id,data){const r=await api('PATCH',`/api/doc/${col}/${encodeURIComponent(id)}`,data);apply(col,id,r.data)},
  async del(col,id){await api('DELETE',`/api/doc/${col}/${encodeURIComponent(id)}`);apply(col,id,null)},
};
async function uploadFile(file){
  const type=file.type||(/\.pdf$/i.test(file.name)?'application/pdf':'');
  return api('POST','/api/files',undefined,{body:file,headers:{'Content-Type':type||'application/octet-stream','X-Filename':encodeURIComponent(file.name||'file')}});
}
function saveFile(name,data,type){
  const blob=data instanceof Blob?data:new Blob([data],{type:type||'application/octet-stream'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1000);
}
let es=null;
function connectEvents(){
  if(es)es.close();
  es=new EventSource('/api/events');
  let broke=false;
  es.onmessage=e=>{try{const ev=JSON.parse(e.data);apply(ev.col,ev.id,ev.data)}catch(_){}};
  es.onerror=()=>{broke=true;S.online=false;scheduleRender()};
  es.onopen=()=>{S.online=true;if(broke){broke=false;loadData()}scheduleRender()};
}
async function loadData(){
  const d=await api('GET','/api/data');
  for(const c of COLS)S[c]=d[c]||[];
  S.company=(d.settings||[]).find(x=>x.id==='company')||{};
  S.loaded=true;render();
}
let rq=0;function scheduleRender(){if(rq)return;rq=requestAnimationFrame(()=>{rq=0;if(S.me)render()})}

/* ---------- calculations ---------- */
function calc(lines){
  const by={};let net=0,vat=0;
  for(const l of lines||[]){
    const n=r2((+l.qty||0)*(+l.price||0)),rate=+l.vat||0,v=r2(n*rate/100);
    net+=n;vat+=v;by[rate]=by[rate]||{net:0,vat:0};by[rate].net+=n;by[rate].vat+=v;
  }
  return{net:r2(net),vat:r2(vat),total:r2(net+vat),by};
}
const expLines=e=>[{qty:1,price:+e.net||0,vat:(e.treatment||'ee')==='ee'?+e.vatRate||0:0}];
function docCalc(d,kind){
  const c=calc(kind==='expense'?expLines(d):d.lines);
  const t=d.treatment||'ee';
  c.rcRate=+(d.rcRate??defRate());
  c.rc=(kind!=='invoice'&&RC.includes(t))?r2(c.net*c.rcRate/100):0;
  c.deductible=kind==='expense'&&d.vatDeductible===false?0:c.vat;
  return c;
}
const prodName=id=>(S.products.find(p=>p.id===id)||{}).name||T('удалённый товар');
function stock(){
  const m={};
  for(const p of S.products)m[p.id]={qty:0,bought:0,cost:0,sold:0,soldM:0,revM:0};
  for(const d of S.purchases)for(const l of d.lines||[]){const x=m[l.productId];if(!x)continue;x.qty+=+l.qty||0;x.bought+=+l.qty||0;x.cost+=(+l.qty||0)*(+l.price||0)}
  for(const d of S.invoices)for(const l of d.lines||[]){const x=m[l.productId];if(!x)continue;x.qty-=+l.qty||0;x.sold+=+l.qty||0;if(inMonth(d.date)){x.soldM+=+l.qty||0;x.revM+=(+l.qty||0)*(+l.price||0)}}
  for(const a of S.adjustments){const x=m[a.productId];if(x)x.qty+=+a.qty||0}
  for(const k in m){const x=m[k];x.avg=x.bought?x.cost/x.bought:0;x.value=Math.max(x.qty,0)*x.avg}
  return m;
}
function monthNums(){
  const inv=S.invoices.filter(d=>inMonth(d.date)),exp=S.expenses.filter(d=>inMonth(d.date));
  const sales=calc(inv.flatMap(d=>d.lines||[]));
  const expNet=r2(exp.reduce((a,e)=>a+(+e.net||0),0));
  const st=stock();let cogs=0;
  for(const d of inv)for(const l of d.lines||[]){const x=st[l.productId];if(x)cogs+=(+l.qty||0)*x.avg}
  return{inv,sales,profit:r2(sales.net-cogs-expNet),vat:vatReport()};
}
function partnerOf(d){return S.partners.find(p=>p.id===d.partnerId)||null}
function vatReport(){
  const R={byRate:{},row3:0,r31:0,r311:0,r32:0,outVat:0,rcVat:0,inVat:0,inPlain:0,r6:0,r61:0,r7:0,vd:{},inf:{}};
  const addRate=(rate,net,vat)=>{R.byRate[rate]=R.byRate[rate]||{net:0,vat:0};R.byRate[rate].net+=net;R.byRate[rate].vat+=vat};
  const addInf=(side,key,name,net)=>{const k=side+'|'+key;R.inf[k]=R.inf[k]||{side,name,key,net:0};R.inf[k].net+=net};
  for(const d of S.invoices.filter(x=>inMonth(x.date))){
    const c=calc(d.lines),t=d.treatment||'ee';
    if(t==='eu_b2b'||t==='export'){
      R.row3+=c.net;
      if(t==='eu_b2b'){
        R.r31+=c.net;const goods=calc((d.lines||[]).filter(l=>l.productId)).net;R.r311+=goods;
        const k=normVat(d.customerVat)||d.customer;R.vd[k]=R.vd[k]||{vat:d.customerVat,name:d.customer,country:d.customerCountry,goods:0,services:0};
        R.vd[k].goods+=goods;R.vd[k].services+=r2(c.net-goods);
      }else R.r32+=c.net;
    }else{
      for(const[rate,x]of Object.entries(c.by)){if(+rate===0)R.row3+=x.net;else addRate(+rate,x.net,x.vat)}
      R.outVat+=c.vat;
      if(t==='ee'&&d.customerReg)addInf('sale',d.customerReg,d.customer,c.net);
    }
  }
  const inDoc=(d,kind)=>{
    const c=docCalc(d,kind),t=d.treatment||'ee';
    if(t==='ee'){R.inVat+=c.deductible;R.inPlain+=c.deductible;const p=partnerOf(d);if(p&&p.regCode&&(p.country||'EE')==='EE')addInf('buy',p.regCode,p.name,c.net)}
    else if(RC.includes(t)){addRate(c.rcRate,c.net,c.rc);R.rcVat+=c.rc;R.inVat+=c.rc;if(t==='foreign_services')R.r7+=c.net;else{R.r6+=c.net;if(t==='eu_goods')R.r61+=c.net}}
  };
  for(const d of S.purchases.filter(x=>inMonth(x.date)))inDoc(d,'purchase');
  for(const d of S.expenses.filter(x=>inMonth(x.date)))inDoc(d,'expense');
  for(const k of['row3','r31','r311','r32','outVat','rcVat','inVat','inPlain','r6','r61','r7'])R[k]=r2(R[k]);
  R.row4=r2(R.outVat+R.rcVat);R.due=r2(R.row4-R.inVat);
  return R;
}
function nextInvNo(){
  const pre=S.company.invoicePrefix??'';let max=(+S.company.invoiceStart||1)-1;
  for(const d of S.invoices){const s=String(d.number||'');if(pre&&!s.startsWith(pre))continue;const n=parseInt(s.slice(pre.length),10);if(n>max)max=n}
  return pre+(max+1);
}
// Estonian payment reference number, 7-3-1 check digit.
function refNo(num){const base=String(num||'').replace(/\D/g,'');if(!base)return'';const w=[7,3,1];let s=0;for(let i=0;i<base.length;i++)s+=(+base[base.length-1-i])*w[i%3];return base+((10-s%10)%10)}
function paidPill(d,kind){
  if(d.paid)return`<span class="pill good">${T('оплачено')}</span>`;
  if(kind==='in'&&d.dueDate&&d.dueDate<today())return`<span class="pill bad">${T('просрочен')}</span>`;
  return`<span class="pill warn">${kind==='in'?T('ждём оплату'):T('не оплачено')}</span>`;
}
function findPartner(name,vat,reg){
  const v=normVat(vat),r=String(reg||'').trim(),n=norm(name);
  return S.partners.find(p=>v&&normVat(p.vatNo)===v)||S.partners.find(p=>r&&String(p.regCode||'').trim()===r)||S.partners.find(p=>n&&norm(p.name)===n)||null;
}
function saleT(p){if(!p||!p.country||p.country==='EE')return'ee';if(isEU(p.country))return p.vatNo?'eu_b2b':'eu_b2c';return'export'}
function buyT(p,kind){if(!p||!p.country||p.country==='EE')return'ee';if(isEU(p.country))return kind==='purchase'?'eu_goods':'eu_services';return kind==='purchase'?'none':'foreign_services'}
const fileHref=id=>'/api/files/'+encodeURIComponent(id);
const fileLinks=files=>(files||[]).map(f=>`<a href="${fileHref(f.id)}" target="_blank" rel="noopener">📎 ${esc(f.name||T('файл'))}</a>`).join('');

/* ---------- login ---------- */
function langSwitch(id){return`<select id="${id}" class="langsel" aria-label="Language">${LANGS.map(([k,n])=>`<option value="${k}" ${k===LANG?'selected':''}>${n}</option>`).join('')}</select>`}
function renderLogin(msg){
  document.body.classList.add('is-login');
  $('#main').innerHTML=`<div class="login"><form id="loginForm" class="login-card">
    <div class="login-top"><div class="brand">Kontor</div>${langSwitch('loginLang')}</div>
    <p class="note">${T('Учёт склада, счетов и НДС')}</p>
    <label class="f">${T('E-mail')}<input id="lg_email" type="email" autocomplete="username" required></label>
    <label class="f">${T('Пароль')}<input id="lg_pass" type="password" autocomplete="current-password" required></label>
    ${msg?`<div class="info warn">${esc(msg)}</div>`:''}
    <button class="btn pri" type="submit">${T('Войти')}</button>
  </form></div>`;
  $('#fab').innerHTML='';$('#tabs').innerHTML='';
  const e=$('#lg_email');e&&e.focus();
}
async function boot(){
  try{
    const me=await api('GET','/api/me');
    S.me=me.user;S.companyInfo=me.company;
    if(me.user.lang&&me.user.lang!==LANG){LANG=me.user.lang;setLocale()}
    document.body.classList.remove('is-login');
    render();await loadData();connectEvents();
  }catch(e){if(e.code!=='auth')renderLogin(e.code==='offline'?T('Нет связи с сервером.'):'')}
}
function setLang(l){
  if(!MONTHS[l])return;LANG=l;setLocale();try{localStorage.setItem('kontor.lang',l)}catch(e){}
  if(S.me){api('POST','/api/me/lang',{lang:l}).catch(()=>{});S.me.lang=l;render()}else renderLogin();
}

/* ---------- render ---------- */
function render(){
  if(!S.me)return;
  $('#coName').textContent=S.company.name||S.companyInfo&&S.companyInfo.name||'';
  $('#mLabel').textContent=mLabel(S.month);
  $('#hdrLang').innerHTML=langSwitch('appLang');
  $('#tabs').innerHTML=TABS.map(([k,t])=>`<button data-tab="${k}" ${S.tab===k?'aria-current="page"':''}>${T(t)}</button>`).join('');
  const views={home:vHome,stock:vStock,sales:vSales,buys:vBuys,exp:vExp,bank:vBank,vat:vVat,partners:vPartners,set:vSet};
  const ae=document.activeElement;
  const editing=ae&&ae.closest&&ae.closest('#main form');
  if(!editing){$('#main').innerHTML=(S.online?'':`<div class="banner">${T('Нет связи с сервером. Изменения не сохранятся, пока связь не вернётся.')}</div>`)+(S.loaded?views[S.tab]():`<div class="empty">${T('Загрузка…')}</div>`);if(S.tab==='set'&&S.loaded){loadUsers();loadAdmin()}}
  $('#fab').innerHTML=(S.fab?`<div class="fab-menu">
      <button data-new="upload">${T('Загрузить счёт (PDF, фото)')}</button><button data-new="bankImport">${T('Выписка из банка')}</button>
      <button data-new="invoice">${T('Счёт клиенту')}</button><button data-new="purchase">${T('Закупка товара')}</button>
      <button data-new="expense">${T('Расход')}</button><button data-new="product">${T('Новый товар')}</button><button data-new="partner">${T('Новый партнёр')}</button><button data-new="adjust">${T('Списание / инвентаризация')}</button></div>`:'')
    +`<button class="fab-main" data-act="fab" aria-label="${T('Добавить')}" aria-expanded="${S.fab}">${S.fab?'×':'+'}</button>`;
  const q=$('#q');if(q&&S._focusQ){q.focus();q.setSelectionRange(q.value.length,q.value.length)}
}
function topProducts(n){
  const st=stock();
  return S.products.map(p=>({p,x:st[p.id]})).filter(o=>o.x.soldM>0).map(o=>({...o,margin:o.x.revM-o.x.soldM*o.x.avg})).sort((a,b)=>b.x.revM-a.x.revM).slice(0,n);
}
function thumb(p,size){const st=size?` style="width:${size}px;height:${size}px"`:'';return p&&p.photo?`<img class="thumb"${st} src="${fileHref(p.photo)}" alt="">`:`<span class="thumb ph"${st}>${esc(((p&&(p.category||p.name))||'?').slice(0,2).toUpperCase())}</span>`}
function prodCell(p){
  const chips=[p.size&&T('размер {0}',p.size),p.color,p.material,p.brand].filter(Boolean);
  return`<div class="pcell">${thumb(p)}<div><b>${esc(p.name)}</b><span class="sub">${esc([p.sku,p.category].filter(Boolean).join(' · '))}</span>${chips.length?`<div class="chips">${chips.map(c=>`<span class="chip">${esc(c)}</span>`).join('')}</div>`:''}</div></div>`;
}
function vHome(){
  const m=monthNums(),st=stock(),V=m.vat;
  const stockVal=Object.values(st).reduce((a,x)=>a+x.value,0);
  const unpaid=S.invoices.filter(d=>!d.paid).sort(byDateDesc);
  const low=S.products.filter(p=>st[p.id]&&st[p.id].qty<=(+p.minStock||0));
  const unBank=S.bank.filter(t=>(t.status||'new')==='new').length;
  const top=topProducts(5);
  const recent=[
    ...S.invoices.map(d=>({t:T('Счёт'),tab:'invoice',d,sum:calc(d.lines).total,who:d.customer,sign:1})),
    ...S.purchases.map(d=>({t:T('Закупка'),tab:'purchase',d,sum:docCalc(d,'purchase').total,who:d.supplier,sign:-1})),
    ...S.expenses.map(d=>({t:T('Расход'),tab:'expense',d,sum:docCalc(d,'expense').total,who:d.supplier||catName(d.category),sign:-1})),
  ].sort((a,b)=>byDateDesc(a.d,b.d)).slice(0,8);
  const empty=!S.products.length&&!S.invoices.length&&!S.purchases.length&&!S.expenses.length;
  return `${empty?`<div class="info">${T('Добро пожаловать! Начните с вкладки «Настройки»: данные фирмы нужны для счетов. Затем нажмите «+» внизу справа и загрузите первый счёт поставщика.')}</div>`:''}
  <div class="tiles">
    <div class="tile"><span class="k">${T('Продажи без НДС')}</span><span class="v">${eur(m.sales.net)}</span><span class="s">${T('{0} счетов за {1}',m.inv.length,mLabel(S.month))}</span></div>
    <div class="tile"><span class="k">${T('Прибыль (оценка)')}</span><span class="v">${eur(m.profit)}</span><span class="s">${T('продажи − себестоимость − расходы')}</span></div>
    <div class="tile accent"><span class="k">${V.due>=0?T('НДС к уплате'):T('НДС к возврату')}</span><span class="v">${eur(Math.abs(V.due))}</span><span class="s">${T('KMD до 20-го числа следующего месяца')}</span></div>
    <div class="tile"><span class="k">${T('Склад по себестоимости')}</span><span class="v">${eur(stockVal)}</span><span class="s">${T('{0} товаров',S.products.length)}</span></div>
  </div>
  ${unBank?`<div class="info warn">${T('В банке платежей без пары со счетами: {0}.',unBank)} <button class="btn sm" data-tab="bank">${T('Разобрать')}</button></div>`:''}
  <div class="cols">
    <section class="card"><div class="card-h"><h2>${T('Ждём оплату')}</h2><span class="hint">${money(unpaid.reduce((a,d)=>a+calc(d.lines).total,0))}</span></div>
      ${unpaid.length?`<div class="rowlist">${unpaid.slice(0,6).map(d=>`<div class="r click" data-open="invoice:${d.id}"><div class="grow"><b>${esc(d.number)}</b> · ${esc(d.customer)}<span class="sub muted">${T('срок')} ${dfmt(d.dueDate)}</span></div>${paidPill(d,'in')} ${money(calc(d.lines).total)}</div>`).join('')}</div>`:`<div class="empty">${T('Все счета оплачены.')}</div>`}
    </section>
    <section class="card"><div class="card-h"><h2>${T('Лучшие товары месяца')}</h2></div>
      ${top.length?`<div class="rowlist">${top.map(o=>`<div class="r click" data-open="product:${o.p.id}">${thumb(o.p)}<div class="grow"><b>${esc(o.p.name)}</b><span class="sub muted">${qfmt(o.x.soldM)} ${esc(o.p.unit||'')} · ${T('маржа')} ${eur(o.margin)}</span></div>${money(o.x.revM)}</div>`).join('')}</div>`:`<div class="empty">${T('В этом месяце продаж товаров нет.')}</div>`}
    </section>
  </div>
  ${low.length?`<section class="card"><div class="card-h"><h2>${T('Заканчивается на складе')}</h2></div><div class="rowlist">${low.map(p=>`<div class="r click" data-open="product:${p.id}">${thumb(p)}<div class="grow"><b>${esc(p.name)}</b><span class="sub muted">${esc([p.size,p.color].filter(Boolean).join(' · '))} · ${T('минимум')} ${qfmt(p.minStock||0)}</span></div><span class="pill ${st[p.id].qty<=0?'bad':'warn'}">${qfmt(st[p.id].qty)} ${esc(p.unit||'')}</span></div>`).join('')}</div></section>`:''}
  <section class="card"><div class="card-h"><h2>${T('Последние операции')}</h2></div>
    ${recent.length?`<div class="rowlist">${recent.map(x=>`<div class="r click" data-open="${x.tab}:${x.d.id}"><div class="grow"><b>${x.t}</b> · ${esc(x.who||'—')}<span class="sub muted">${dfmt(x.d.date)}${x.d.number?' · '+esc(x.d.number):x.d.docNo?' · '+esc(x.d.docNo):''}</span></div><span class="money ${x.sign>0?'amt-in':''}">${x.sign>0?'+':'−'}${eur(x.sum)}</span></div>`).join('')}</div>`:`<div class="empty">${T('Пока пусто.')}</div>`}
  </section>`;
}
function vStock(){
  const st=stock(),q=norm(S.q);
  const list=S.products.filter(p=>!q||norm([p.name,p.sku,p.ean,p.size,p.color,p.brand,p.category].join(' ')).includes(q)).sort((a,b)=>(a.category||'').localeCompare(b.category||'')||a.name.localeCompare(b.name));
  let tq=0,tv=0;for(const p of list){tq+=Math.max(st[p.id].qty,0);tv+=st[p.id].value}
  const adj=S.adjustments.slice().sort(byDateDesc).slice(0,10);
  return `<section class="card"><div class="card-h"><h2>${T('Остатки')}</h2><input id="q" class="search" type="search" placeholder="${T('Поиск: название, код, размер, цвет')}" value="${esc(S.q)}"><button class="btn sm pri" data-new="product">+ ${T('Товар')}</button></div>
    ${list.length?`<div class="scroll"><table><thead><tr><th>${T('Товар')}</th><th class="n">${T('Остаток')}</th><th class="n">${T('Себест.')}</th><th class="n">${T('Цена продажи')}</th><th class="n">${T('Продано за месяц')}</th><th class="n">${T('Стоимость')}</th></tr></thead><tbody>
    ${list.map(p=>{const x=st[p.id];const cls=x.qty<=0?'bad':x.qty<=(+p.minStock||0)?'warn':'';return`<tr class="click" data-open="product:${p.id}"><td>${prodCell(p)}</td>
      <td class="n">${cls?`<span class="pill ${cls}">${qfmt(x.qty)} ${esc(p.unit||'')}</span>`:`${qfmt(x.qty)} ${esc(p.unit||'')}`}</td><td class="n">${x.avg?money(x.avg):'—'}</td><td class="n">${money(p.price)}<span class="sub">+${T('НДС')} ${+p.vatRate||0}%</span></td><td class="n">${qfmt(x.soldM)}</td><td class="n">${money(x.value)}</td></tr>`}).join('')}
    </tbody><tfoot><tr><td>${T('Итого')}</td><td class="n">${qfmt(tq)}</td><td></td><td></td><td></td><td class="n">${money(tv)}</td></tr></tfoot></table></div>`:`<div class="empty">${S.products.length?T('Ничего не найдено.'):T('Товаров пока нет. Их удобно создать прямо из счёта поставщика: «+», затем «Загрузить счёт».')}</div>`}
  </section>
  <section class="card"><div class="card-h"><h2>${T('Списания и инвентаризация')}</h2><button class="btn sm" data-new="adjust">+ ${T('Корректировка')}</button></div>
    ${adj.length?`<div class="rowlist">${adj.map(a=>`<div class="r click" data-open="adjust:${a.id}"><div class="grow"><b>${esc(prodName(a.productId))}</b><span class="sub muted">${dfmt(a.date)} · ${esc(a.reason||'')}</span></div><span class="pill ${a.qty<0?'bad':'good'}">${a.qty>0?'+':''}${qfmt(a.qty)}</span></div>`).join('')}</div>`:`<div class="empty">${T('Брак, подарки, пересчёт склада: всё, что меняет остаток без продажи и закупки. Начальные остатки вносятся здесь же, с плюсом.')}</div>`}
  </section>
  <p class="note">${T('Остаток = закупки − продажи ± корректировки. Себестоимость считается как средняя цена всех закупок без НДС.')}</p>`;
}
function docTable(list,kind){
  if(!list.length)return'';
  const isInv=kind==='invoice',isExp=kind==='expense';
  const T0={net:0,vat:0,total:0};
  const rows=list.map(d=>{const c=docCalc(d,kind);T0.net+=c.net;T0.vat+=c.vat;T0.total+=c.total;const t=d.treatment||'ee';
    return`<tr class="click" data-open="${kind}:${d.id}"><td class="num">${dfmt(d.date)}</td>
   <td><b>${esc(isInv?d.number:isExp?catName(d.category):d.supplier)}</b>${(d.files||[]).length?' 📎':''}<span class="sub">${esc(isInv?d.customer:isExp?(d.supplier||'')+(d.note?' · '+d.note:''):(d.docNo||'')+' · '+T('{0} поз.',(d.lines||[]).length))}</span>${t!=='ee'?`<span class="chip">${T((isInv?SALE_T:BUY_T)[t]||t)}</span>`:''}</td>
   <td class="n">${money(c.net)}</td><td class="n">${money(c.vat)}${c.rc?`<span class="sub">${T('обр.')} ${eur(c.rc)}</span>`:''}</td><td class="n">${money(c.total)}</td><td>${paidPill(d,isInv?'in':'out')}</td></tr>`}).join('');
  return `<div class="scroll"><table><thead><tr><th>${T('Дата')}</th><th>${isInv?T('Счёт / клиент'):isExp?T('Расход'):T('Поставщик')}</th><th class="n">${T('Без НДС')}</th><th class="n">${T('НДС')}</th><th class="n">${T('К оплате')}</th><th>${T('Статус')}</th></tr></thead><tbody>${rows}</tbody>
  <tfoot><tr><td></td><td>${T('Итого за месяц')}</td><td class="n">${money(T0.net)}</td><td class="n">${money(T0.vat)}</td><td class="n">${money(T0.total)}</td><td></td></tr></tfoot></table></div>`;
}
function vSales(){
  const list=S.invoices.filter(d=>inMonth(d.date)).sort(byDateDesc);
  const top=topProducts(20);
  return `<section class="card"><div class="card-h"><h2>${T('Счета за {0}',mLabel(S.month))}</h2><button class="btn sm pri" data-new="invoice">+ ${T('Счёт')}</button></div>
  ${docTable(list,'invoice')||`<div class="empty">${T('В этом месяце счетов нет.')}</div>`}</section>
  ${top.length?`<section class="card"><div class="card-h"><h2>${T('Что продаётся')}</h2><span class="hint">${mLabel(S.month)}</span></div><div class="scroll"><table><thead><tr><th>${T('Товар')}</th><th class="n">${T('Продано')}</th><th class="n">${T('Выручка')}</th><th class="n">${T('Себестоимость')}</th><th class="n">${T('Маржа')}</th></tr></thead><tbody>
  ${top.map(o=>`<tr class="click" data-open="product:${o.p.id}"><td>${prodCell(o.p)}</td><td class="n">${qfmt(o.x.soldM)}</td><td class="n">${money(o.x.revM)}</td><td class="n">${money(o.x.soldM*o.x.avg)}</td><td class="n">${money(o.margin)}<span class="sub">${o.x.revM?Math.round(o.margin/o.x.revM*100):0}%</span></td></tr>`).join('')}</tbody></table></div></section>`:''}
  <p class="note">${T('Продажа через интернет-магазин тоже оформляется здесь: один счёт на заказ или один сводный за день. Товар со склада списывается автоматически.')}</p>`;
}
function vBuys(){
  const list=S.purchases.filter(d=>inMonth(d.date)).sort(byDateDesc);
  return `<section class="card"><div class="card-h"><h2>${T('Закупки за {0}',mLabel(S.month))}</h2><button class="btn sm" data-new="upload">${T('Загрузить счёт')}</button><button class="btn sm pri" data-new="purchase">+ ${T('Закупка')}</button></div>
  ${docTable(list,'purchase')||`<div class="empty">${T('В этом месяце закупок нет. Закупка добавляет товар на склад.')}</div>`}</section>`;
}
function vExp(){
  const list=S.expenses.filter(d=>inMonth(d.date)).sort(byDateDesc);
  const byCat={};for(const e of list){byCat[e.category]=(byCat[e.category]||0)+(+e.net||0)}
  const cats=Object.entries(byCat).sort((a,b)=>b[1]-a[1]),max=Math.max(1,...cats.map(c=>c[1]));
  return `<section class="card"><div class="card-h"><h2>${T('Расходы за {0}',mLabel(S.month))}</h2><button class="btn sm" data-new="upload">${T('Загрузить счёт')}</button><button class="btn sm pri" data-new="expense">+ ${T('Расход')}</button></div>
  ${docTable(list,'expense')||`<div class="empty">${T('В этом месяце расходов нет. Сюда идут все траты, кроме товара для продажи: реклама, сайт, доставка, банк.')}</div>`}</section>
  ${cats.length?`<section class="card"><div class="card-h"><h2>${T('По категориям')}</h2><span class="hint">${T('без НДС')}</span></div><div class="rowlist">${cats.map(([c,v])=>`<div class="r"><div class="grow">${esc(catName(c))}<div class="bar-track"><div class="bar-fill" style="width:${(v/max*100).toFixed(1)}%"></div></div></div>${money(v)}</div>`).join('')}</div></section>`:''}`;
}
function candidates(t){
  const amt=Math.abs(+t.amount||0),hay=norm((t.desc||'')+' '+(t.ref||'')),nm=norm(t.name);
  let pool;
  if(t.amount>0)pool=S.invoices.filter(d=>!d.paid).map(d=>({col:'invoices',d,total:calc(d.lines).total,label:T('Счёт')+' '+d.number,who:d.customer,keys:[d.number,d.refNo]}));
  else pool=[...S.purchases.filter(d=>!d.paid).map(d=>({col:'purchases',d,total:docCalc(d,'purchase').total,label:T('Закупка')+' '+(d.docNo||''),who:d.supplier,keys:[d.docNo]})),
             ...S.expenses.filter(d=>!d.paid).map(d=>({col:'expenses',d,total:docCalc(d,'expense').total,label:T('Расход')+' '+(d.docNo||catName(d.category)),who:d.supplier,keys:[d.docNo]}))];
  return pool.map(c=>{let s=0;if(Math.abs(c.total-amt)<0.011)s+=3;
    if(c.keys.some(k=>k&&String(k).length>=2&&hay.includes(norm(k))))s+=3;
    const w=norm(c.who).split(' ').filter(x=>x.length>2);if(w.length&&w.some(x=>nm.includes(x)))s+=1;
    return{...c,score:s}}).filter(c=>c.score>=3).sort((a,b)=>b.score-a.score).slice(0,3);
}
function matchLabel(m){if(!m)return'';return(m.col==='invoices'?T('Счёт'):m.col==='purchases'?T('Закупка'):T('Расход'))+' '+(m.label||'')}
function vBank(){
  const list=S.bank.filter(t=>inMonth(t.date)).sort(byDateDesc);
  const inn=r2(list.filter(t=>t.amount>0).reduce((a,t)=>a+t.amount,0)),out=r2(list.filter(t=>t.amount<0).reduce((a,t)=>a-t.amount,0));
  const open=list.filter(t=>(t.status||'new')==='new').length;
  return `<div class="tiles">
    <div class="tile"><span class="k">${T('Поступления')}</span><span class="v">${eur(inn)}</span></div>
    <div class="tile"><span class="k">${T('Платежи')}</span><span class="v">${eur(out)}</span></div>
    <div class="tile ${open?'accent':''}"><span class="k">${T('Без пары')}</span><span class="v">${open}</span><span class="s">${T('из {0} операций',list.length)}</span></div>
    <div class="tile"><span class="k">${T('Разница')}</span><span class="v">${eur(inn-out)}</span></div>
  </div>
  <section class="card"><div class="card-h"><h2>${T('Банк за {0}',mLabel(S.month))}</h2><button class="btn sm pri" data-new="bankImport">${T('Загрузить выписку')}</button></div>
  ${list.length?`<div class="rowlist">${list.map(t=>{const st=t.status||'new';let act='';
    if(st==='matched')act=`<span class="pill good">${T('связано')}: ${esc(matchLabel(t.match))}</span> <button class="btn sm" data-act="unlink" data-id="${t.id}">${T('Отвязать')}</button>`;
    else if(st==='ignored')act=`<span class="pill mute">${T((IGNORE.find(x=>x[0]===t.ignoreReason)||[0,'не учитывается'])[1])}</span> <button class="btn sm" data-act="unignore" data-id="${t.id}">${T('Вернуть')}</button>`;
    else{act=candidates(t).map(c=>`<button class="btn sm pri" data-act="link" data-id="${t.id}" data-col="${c.col}" data-doc="${c.d.id}">${T('Связать')}: ${esc(c.label)} · ${esc(c.who||'')}</button>`).join('')
      +(t.amount<0?`<button class="btn sm" data-act="txExpense" data-id="${t.id}">${T('Создать расход')}</button>`:`<button class="btn sm" data-act="txSale" data-id="${t.id}">${T('Создать продажу')}</button>`)
      +`<button class="btn sm" data-act="txIgnore" data-id="${t.id}">${T('Не учитывать')}</button>`}
    return`<div class="r top"><div class="grow"><b>${esc(t.name||'—')}</b><span class="sub muted">${dfmt(t.date)} · ${esc(t.desc||'')}${t.ref?' · '+T('виитенумбер')+' '+esc(t.ref):''}</span><div class="tx-act">${act}</div></div><span class="money ${t.amount>0?'amt-in':''}">${t.amount>0?'+':'−'}${eur(Math.abs(t.amount))}</span></div>`}).join('')}</div>`
  :`<div class="empty">${T('Выгрузите выписку из интернет-банка (Swedbank, SEB, LHV, Coop: формат CSV или XML camt.053) и загрузите сюда. Программа найдёт, какие счета оплачены.')}</div>`}
  </section>`;
}
function vVat(){
  const V=vatReport(),sales=calc(S.invoices.filter(d=>inMonth(d.date)).flatMap(d=>d.lines||[]));
  const [y,mm]=S.month.split('-');const nm=+mm===12?`20.01.${+y+1}`:`20.${String(+mm+1).padStart(2,'0')}.${y}`;
  const rateRows=Object.entries(V.byRate).sort((a,b)=>b[0]-a[0]);
  const inf=Object.values(V.inf).filter(x=>x.net>=1000);
  const vd=Object.values(V.vd);
  const row=(n,label,v)=>`<tr><td>${n}</td><td>${label}</td><td class="n">${money(v)}</td></tr>`;
  return `<div class="tiles">
    <div class="tile"><span class="k">${T('Начислено НДС')}</span><span class="v">${eur(V.row4)}</span><span class="s">${T('продажи')} ${eur(V.outVat)}${V.rcVat?' · '+T('обратное')+' '+eur(V.rcVat):''}</span></div>
    <div class="tile"><span class="k">${T('НДС к вычету')}</span><span class="v">${eur(V.inVat)}</span><span class="s">${T('по счетам')} ${eur(V.inPlain)}${V.rcVat?' · '+T('обратное')+' '+eur(V.rcVat):''}</span></div>
    <div class="tile accent"><span class="k">${V.due>=0?T('К уплате'):T('К возврату')}</span><span class="v">${eur(Math.abs(V.due))}</span><span class="s">${T('KMD и оплата до {0}',nm)}</span></div>
    <div class="tile"><span class="k">${T('Оборот без НДС')}</span><span class="v">${eur(sales.net)}</span></div>
  </div>
  <section class="card"><div class="card-h"><h2>${T('Для декларации KMD')}</h2><span class="hint">${mLabel(S.month)}</span></div>
    <div class="scroll"><table class="kmd"><thead><tr><th>${T('Строка')}</th><th>${T('Что')}</th><th class="n">${T('Сумма')}</th></tr></thead><tbody>
    ${rateRows.length?rateRows.map(([r,x])=>row(r+'%',T('Облагаемый оборот по ставке {0}% (продажи и обратное начисление)',r),x.net)).join(''):row('—',T('Облагаемый оборот'),0)}
    ${row('3',T('Оборот по ставке 0%'),V.row3)}
    ${row('3.1',T('в т.ч. поставки фирмам из ЕС с VAT-номером'),V.r31)}
    ${row('3.1.1',T('из них товары'),V.r311)}
    ${row('3.2',T('в т.ч. экспорт'),V.r32)}
    ${row('4',T('НДС всего'),V.row4)}
    ${row('5',T('Входящий НДС к вычету'),V.inVat)}
    ${row('6',T('Приобретения из ЕС (товары и услуги)'),V.r6)}
    ${row('6.1',T('из них товары'),V.r61)}
    ${row('7',T('Прочие приобретения с обратным начислением'),V.r7)}
    ${V.due>=0?row('12',T('НДС к уплате'),V.due):row('13',T('Переплата НДС'),-V.due)}
    </tbody></table></div>
    <p class="note pad">${T('Номера строк сверьте с формой KMD в e-MTA: форма иногда меняется. Обратное начисление при покупке из ЕС попадает и в начисленный НДС, и в вычет, поэтому к уплате само по себе ничего не добавляет.')}</p>
  </section>
  <section class="card"><div class="card-h"><h2>${T('Отчёт VD: продажи фирмам ЕС')}</h2></div>
    ${vd.length?`<div class="scroll"><table><thead><tr><th>${T('VAT-номер покупателя')}</th><th>${T('Фирма')}</th><th class="n">${T('Товары')}</th><th class="n">${T('Услуги')}</th></tr></thead><tbody>${vd.map(x=>`<tr><td class="num">${esc(x.vat||T('нет номера!'))}</td><td>${esc(x.name)}<span class="sub">${esc(countryName(x.country))}</span></td><td class="n">${money(x.goods)}</td><td class="n">${money(x.services)}</td></tr>`).join('')}</tbody></table></div><p class="note pad">${T('Отчёт VD подаётся в e-MTA до 20-го числа вместе с KMD.')}</p>`:`<div class="empty">${T('В этом месяце продаж фирмам ЕС с 0% не было, отчёт VD не нужен.')}</div>`}
  </section>
  <section class="card"><div class="card-h"><h2>${T('KMD INF: эстонские партнёры от 1000 €')}</h2></div>
    ${inf.length?`<div class="rowlist">${inf.map(x=>`<div class="r"><div class="grow"><b>${esc(x.name)}</b><span class="sub muted">${x.side==='sale'?T('Продажа'):T('Покупка')} · ${T('рег. код')} ${esc(x.key)}</span></div>${money(x.net)}</div>`).join('')}</div><p class="note pad">${T('Счета этих эстонских плательщиков НДС за месяц в сумме от 1000 € без НДС. Их нужно показать в приложении KMD INF.')}</p>`:`<div class="empty">${T('В этом месяце нет эстонских партнёров с суммой от 1000 € без НДС.')}</div>`}
  </section>
  <p class="note">${T('Это расчёт по вашим данным, а не сама декларация. Если продаёте частным лицам в другие страны ЕС больше чем на 10 000 € в год, нужна регистрация OSS и НДС страны покупателя.')}</p>`;
}
function vPartners(){
  const q=norm(S.q);
  const list=S.partners.filter(p=>!q||norm([p.name,p.regCode,p.vatNo,p.email].join(' ')).includes(q)).sort((a,b)=>a.name.localeCompare(b.name));
  const sums={};for(const d of[...S.invoices,...S.purchases]){if(d.partnerId)sums[d.partnerId]=(sums[d.partnerId]||0)+calc(d.lines).net}
  return `<section class="card"><div class="card-h"><h2>${T('Партнёры')}</h2><input id="q" class="search" type="search" placeholder="${T('Поиск: имя, код, VAT')}" value="${esc(S.q)}"><button class="btn sm pri" data-new="partner">+ ${T('Партнёр')}</button></div>
  ${list.length?`<div class="scroll"><table><thead><tr><th>${T('Фирма')}</th><th>${T('Страна')}</th><th>${T('Рег. код / VAT')}</th><th class="n">${T('Оборот')}</th></tr></thead><tbody>${list.map(p=>`<tr class="click" data-open="partner:${p.id}"><td><b>${esc(p.name)}</b><span class="sub">${esc(p.email||'')}</span></td><td>${esc(countryName(p.country))}${p.country&&!isEU(p.country)?` <span class="chip">${T('вне ЕС')}</span>`:''}</td><td class="num">${esc(p.regCode||'')}<span class="sub">${esc(p.vatNo||'')}</span></td><td class="n">${money(sums[p.id]||0)}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty">${S.partners.length?T('Ничего не найдено.'):T('Партнёры появятся сами, когда вы загрузите счёт или выставите счёт клиенту.')}</div>`}
  </section>
  <p class="note">${T('Страна и VAT-номер определяют НДС: фирме из ЕС с номером счёт выставляется с 0%, покупки у фирм ЕС идут с обратным начислением. Номер можно проверить в системе VIES Европейской комиссии.')}</p>`;
}
function vSet(){
  const c=S.company,owner=S.me.role==='owner';
  return `<form id="coForm" class="stack">
  <section class="card"><div class="card-h"><h2>${T('Данные фирмы для счетов')}</h2></div>
    <div class="pad"><div class="grid">
      <label class="f full">${T('Название')}<input id="co_name" value="${esc(c.name||S.companyInfo&&S.companyInfo.name||'')}" placeholder="Näide OÜ"></label>
      <label class="f">${T('Регистрационный код')}<input id="co_reg" value="${esc(c.regCode)}" inputmode="numeric"></label>
      <label class="f">${T('Номер KMKR (VAT)')}<input id="co_vat" value="${esc(c.vatNo)}" placeholder="EE10…"></label>
      <label class="f full">${T('Адрес')}<input id="co_addr" value="${esc(c.address)}"></label>
      <label class="f">IBAN<input id="co_iban" value="${esc(c.iban)}"></label>
      <label class="f">${T('Банк и SWIFT')}<input id="co_bank" value="${esc(c.bank)}" placeholder="Swedbank, HABAEE2X"></label>
      <label class="f">${T('E-mail')}<input id="co_email" value="${esc(c.email)}"></label>
      <label class="f">${T('Телефон')}<input id="co_phone" value="${esc(c.phone)}"></label>
      <label class="f">${T('Сайт')}<input id="co_web" value="${esc(c.web)}"></label>
      <label class="f">${T('Префикс номера счёта')}<input id="co_pre" value="${esc(c.invoicePrefix??'')}" placeholder="A-"></label>
      <label class="f">${T('Первый номер счёта')}<input id="co_start" type="number" min="1" value="${esc(c.invoiceStart||1)}"></label>
      <label class="f">${T('Срок оплаты, дней')}<input id="co_due" type="number" min="0" value="${esc(c.dueDays??7)}"></label>
      <label class="f">${T('Пеня за просрочку, % в день')}<input id="co_pen" type="number" step="0.01" min="0" value="${esc(c.penalty??'')}" placeholder="0.05"></label>
    </div></div>
  </section>
  <section class="card"><div class="card-h"><h2>${T('Ставки НДС')}</h2></div>
    <div class="pad stack">
      <div class="rates" id="rates">${rates().map(r=>rateRow(r,(c.defaultRate??rates()[0].rate)===r.rate)).join('')}</div>
      <div class="actions"><button class="btn sm" type="button" data-act="addRate">+ ${T('Ставка')}</button></div>
      <p class="note">${T('Точка выбирает основную ставку: она подставляется в новые документы и в обратное начисление. Ставка записывается в каждый документ, поэтому, если государство её поменяет, старые счета останутся со старой.')}</p>
    </div>
  </section>
  <div class="actions"><button class="btn pri" type="submit">${T('Сохранить настройки')}</button></div>
  </form>
  <section class="card"><div class="card-h"><h2>${T('Распознавание счетов')}</h2></div>
    <div class="pad stack">
      <p class="note">${S.companyInfo&&S.companyInfo.aiReady?T('Распознавание включено.'):T('Распознавание выключено.')} ${T('Для него нужен ключ Anthropic API (console.anthropic.com). Один счёт стоит несколько центов, оплата идёт напрямую Anthropic.')}</p>
      ${owner?`<form id="aiForm" class="actions"><input id="ai_key" type="password" class="grow-input" placeholder="sk-ant-…" autocomplete="off"><button class="btn" type="submit">${T('Сохранить ключ')}</button>${S.companyInfo&&S.companyInfo.aiReady?`<button class="btn danger" type="button" data-act="aiRemove">${T('Удалить ключ')}</button>`:''}</form>`:''}
    </div>
  </section>
  <section class="card"><div class="card-h"><h2>${T('Выгрузка')}</h2></div>
    <div class="pad stack">
      <p class="note">${T('Журнал за {0}: все счета, закупки и расходы одной таблицей для бухгалтера или Excel. Полная копия сохраняет все данные.',mLabel(S.month))}</p>
      <div class="actions"><button class="btn" data-act="csv">${T('Журнал месяца (CSV)')}</button><button class="btn" data-act="json">${T('Полная копия (JSON)')}</button></div>
    </div>
  </section>
  <section class="card"><div class="card-h"><h2>${T('Мой вход')}</h2><span class="hint">${esc(S.me.email)}</span></div>
    <form id="pwForm" class="pad stack"><div class="grid">
      <label class="f">${T('Текущий пароль')}<input id="pw_cur" type="password" autocomplete="current-password"></label>
      <label class="f">${T('Новый пароль (от 8 символов)')}<input id="pw_new" type="password" autocomplete="new-password"></label>
    </div><div class="actions"><button class="btn" type="submit">${T('Сменить пароль')}</button><span class="sp"></span><button class="btn" type="button" data-act="logout">${T('Выйти')}</button></div></form>
  </section>
  ${owner?`<section class="card"><div class="card-h"><h2>${T('Пользователи фирмы')}</h2></div><div id="usersBox" class="pad stack"><div class="note">${T('Загрузка…')}</div></div></section>`:''}
  ${S.me.isAdmin?`<section class="card"><div class="card-h"><h2>${T('Администрирование: фирмы')}</h2></div><div id="adminBox" class="pad stack"><div class="note">${T('Загрузка…')}</div></div></section>`:''}`;
}
function rateRow(r,isDef){return`<div class="rrow"><input class="rt-rate" type="number" step="0.1" min="0" value="${esc(r.rate)}" aria-label="%"><input class="rt-label" value="${esc(r.label||'')}" placeholder="${T('Название, например «основная»')}"><label class="chk"><input type="radio" name="rt-def" ${isDef?'checked':''}> ${T('осн.')}</label><button class="btn sm" type="button" data-act="delRate" aria-label="×">×</button></div>`}
async function loadUsers(){
  const box=$('#usersBox');if(!box)return;
  try{const list=await api('GET','/api/company/users');
    box.innerHTML=`<div class="rowlist bordered">${list.map(u=>`<div class="r"><div class="grow"><b>${esc(u.name||u.email)}</b><span class="sub muted">${esc(u.email)} · ${u.role==='owner'?T('владелец'):T('сотрудник')}</span></div>${u.role!=='owner'&&u.id!==S.me.id?`<button class="btn sm danger" data-act="delUser" data-id="${u.id}">${T('Удалить')}</button>`:''}</div>`).join('')}</div>
    <form id="userForm" class="stack"><p class="note">${T('Добавьте человека, который будет работать в этой же фирме (например, бухгалтера). Он увидит все данные фирмы.')}</p><div class="grid">
      <label class="f">${T('Имя')}<input id="nu_name"></label><label class="f">${T('E-mail')}<input id="nu_email" type="email"></label>
      <label class="f">${T('Пароль (от 8 символов)')}<input id="nu_pass" type="text" autocomplete="off"></label><label class="f">${T('Язык')}${langSelect('nu_lang')}</label>
    </div><div class="actions"><button class="btn" type="submit">${T('Добавить пользователя')}</button></div></form>`;
  }catch(e){box.innerHTML=`<div class="note">${T('Не удалось загрузить.')}</div>`}
}
async function loadAdmin(){
  const box=$('#adminBox');if(!box)return;
  try{const list=await api('GET','/api/admin/companies');
    box.innerHTML=`<p class="note">${T('Здесь вы создаёте фирмы для друзей. У каждой фирмы свой вход, и она видит только свои данные.')}</p>
    <div class="scroll"><table><thead><tr><th>${T('Фирма')}</th><th>${T('Владелец')}</th><th class="n">${T('Записей')}</th><th></th></tr></thead><tbody>${list.map(c=>`<tr><td><b>${esc(c.name)}</b><span class="sub">${dfmt(new Date(c.created_at).toISOString().slice(0,10))}</span></td><td>${esc(c.owner||'')}</td><td class="n">${c.docs}</td><td><button class="btn sm" data-act="resetOwner" data-id="${c.id}">${T('Новый пароль')}</button></td></tr>`).join('')}</tbody></table></div>
    <form id="companyForm" class="stack"><h3 class="h3">${T('Новая фирма')}</h3><div class="grid">
      <label class="f">${T('Название фирмы')}<input id="nc_name"></label><label class="f">${T('Имя владельца')}<input id="nc_owner"></label>
      <label class="f">${T('E-mail для входа')}<input id="nc_email" type="email"></label><label class="f">${T('Пароль (от 8 символов)')}<input id="nc_pass" type="text" autocomplete="off"></label>
      <label class="f">${T('Язык')}${langSelect('nc_lang')}</label>
    </div><div class="actions"><button class="btn pri" type="submit">${T('Создать фирму')}</button></div></form>`;
  }catch(e){box.innerHTML=`<div class="note">${T('Не удалось загрузить.')}</div>`}
}
const langSelect=id=>`<select id="${id}">${LANGS.map(([k,n])=>`<option value="${k}" ${k===LANG?'selected':''}>${n}</option>`).join('')}</select>`;

/* ---------- sheets ---------- */
let sheet=null,SH={};
function openSheet(html,state,onMount){
  $('#sheetRoot').innerHTML=`<div class="veil" data-act="veil"><div class="sheet" role="dialog" aria-modal="true">${html}</div></div>`;
  sheet=$('#sheetRoot .sheet');SH=state||{};onMount&&onMount(sheet);
  const f=sheet.querySelector('input:not([type=checkbox]):not([type=file]):not([type=radio]),select');f&&f.focus({preventScroll:true});
}
function closeSheet(){$('#sheetRoot').innerHTML='';sheet=null;SH={}}
function toast(t){$('#toastRoot').innerHTML=`<div class="toast" role="status">${esc(t)}</div>`;clearTimeout(toast.t);toast.t=setTimeout(()=>$('#toastRoot').innerHTML='',3500)}
const val=id=>(sheet.querySelector('#'+id)||{}).value??'';
const chk=id=>!!(sheet.querySelector('#'+id)||{}).checked;
function rateOpts(v){const list=rates().map(r=>+r.rate);if(v!=null&&v!==''&&!list.includes(+v))list.push(+v);return list.map(r=>`<option value="${r}" ${+v===r?'selected':''}>${r}%</option>`).join('')}
const tOpts=(map,v)=>Object.entries(map).map(([k,t])=>`<option value="${k}" ${v===k?'selected':''}>${T(t)}</option>`).join('');
function countryOpts(v){return`<option value="">—</option><optgroup label="${T('ЕС')}">${EU.map(k=>`<option value="${k}" ${v===k?'selected':''}>${esc(countryName(k))} (${k})</option>`).join('')}</optgroup><optgroup label="${T('Вне ЕС')}">${[...NONEU,'XX'].map(k=>`<option value="${k}" ${v===k?'selected':''}>${esc(countryName(k))}</option>`).join('')}</optgroup>`}
const head=t=>`<div class="sheet-h"><h2>${t}</h2><button class="x" data-act="close" aria-label="${T('Закрыть')}">×</button></div>`;
const foot=(col,id)=>`<div class="actions">${id?`<button class="btn danger" data-del="${col}:${id}">${T('Удалить')}</button>`:''}<span class="sp"></span><button class="btn" data-act="close">${T('Отмена')}</button><button class="btn pri" data-act="save">${T('Сохранить')}</button></div>`;
const plist=()=>`<datalist id="plist">${S.partners.map(p=>`<option value="${esc(p.name)}">`).join('')}</datalist>`;
const filesBlock=()=>`<div class="actions"><div class="files" id="fileList"></div><span class="sp"></span><label class="btn sm filebtn">${T('Прикрепить файл')}<input type="file" id="attach" accept="application/pdf,image/*" hidden></label></div>`;
function drawFiles(){const el=sheet&&sheet.querySelector('#fileList');if(el)el.innerHTML=(SH.files||[]).length?fileLinks(SH.files):`<span class="note">${T('Файлов нет')}</span>`}

function lineRow(l,mode){
  const opts=S.products.slice().sort((a,b)=>a.name.localeCompare(b.name)).map(p=>`<option value="${p.id}" ${l.productId===p.id?'selected':''}>${esc(p.name)}${p.size&&!p.name.includes(p.size)?' · '+esc(p.size):''}</option>`).join('');
  const first=mode==='sale'?`<option value="">— ${T('услуга / доставка')} —</option>`:`<option value="">— ${T('выберите')} —</option><option value="__new" ${l.productId==='__new'?'selected':''}>+ ${T('новый товар из описания')}</option>`;
  const extra=esc(JSON.stringify({sku:l.sku||'',ean:l.ean||'',size:l.size||'',color:l.color||''}));
  return `<div class="line" data-extra="${extra}">
    <label class="wide"><span class="lbl">${T('Товар')}</span><select class="l-prod">${first}${opts}</select></label>
    <label class="wide"><span class="lbl">${T('Описание')}</span><input class="l-name" value="${esc(l.name||'')}"></label>
    <label><span class="lbl">${T('Кол-во')}</span><input class="l-qty" type="number" step="any" inputmode="decimal" value="${esc(l.qty??1)}"></label>
    <label><span class="lbl">${T('Цена без НДС')}</span><input class="l-price" type="number" step="0.0001" inputmode="decimal" value="${esc(l.price??'')}"></label>
    <label><span class="lbl">${T('НДС')}</span><select class="l-vat">${rateOpts(l.vat??defRate())}</select></label>
    <button class="del" type="button" data-act="delLine" aria-label="×">×</button></div>`;
}
function readLines(){
  return [...sheet.querySelectorAll('.line')].map(r=>{let ex={};try{ex=JSON.parse(r.dataset.extra||'{}')}catch(_){}
    const l={productId:r.querySelector('.l-prod').value||null,name:r.querySelector('.l-name').value.trim(),qty:+r.querySelector('.l-qty').value||0,price:+(+r.querySelector('.l-price').value||0).toFixed(4),vat:+r.querySelector('.l-vat').value};
    for(const k of['sku','ean','size','color'])if(ex[k])l[k]=ex[k];return l}).filter(l=>l.productId||l.name||l.price);
}
const TREAT_HINT={eu_b2b:'Счёт будет с НДС 0% и пометкой «reverse charge». Нужен VAT-номер покупателя, продажа попадёт в отчёт VD.',export:'Экспорт с 0%. Храните документ, подтверждающий вывоз товара из ЕС.',eu_b2c:'Частному лицу в ЕС счёт с эстонским НДС (пока общие продажи в ЕС меньше 10 000 € в год).',
  eu_goods:'В счёте поставщика НДС нет. Программа сама начислит эстонский НДС и сразу поставит его в вычет.',eu_services:'В счёте НДС нет. Программа сама начислит эстонский НДС и сразу поставит его в вычет.',foreign_services:'Услуга из-за пределов ЕС: НДС начисляется и принимается в вычет так же, обратным начислением.',none:'НДС не учитывается.'};
function zeroVat(){
  const t=val('treat');const zero=SH.kind==='invoice'?(t==='eu_b2b'||t==='export'):t!=='ee';
  sheet.querySelectorAll('.l-vat').forEach(s=>{if(zero)s.value='0';s.disabled=zero});
  const ev=sheet.querySelector('#e_vat');if(ev){if(zero)ev.value='0';ev.disabled=zero}
  const hint=sheet.querySelector('#treatHint');
  if(hint){hint.textContent=TREAT_HINT[t]?T(TREAT_HINT[t]):'';hint.hidden=!TREAT_HINT[t]}
  refreshTotals();
}
function refreshTotals(){
  if(!sheet)return;const t=sheet.querySelector('#totals');if(!t)return;
  const tr=val('treat')||'ee';let c;
  if(sheet.querySelector('#e_net'))c=calc([{qty:1,price:+val('e_net'),vat:tr==='ee'?+val('e_vat'):0}]);else c=calc(readLines());
  const rc=SH.kind!=='invoice'&&RC.includes(tr)?r2(c.net*defRate()/100):0;
  t.innerHTML=`<div><span>${T('Без НДС')}</span>${money(c.net)}</div>${Object.entries(c.by).filter(([,x])=>x.net).map(([r,x])=>`<div><span>${T('НДС')} ${r}%</span>${money(x.vat)}</div>`).join('')}<div class="grand"><span>${SH.kind==='invoice'?T('К оплате'):T('Итого')}</span>${money(c.total)}</div>${rc?`<div class="muted"><span>${T('Обратное начисление')} ${defRate()}%</span>${money(rc)}</div>`:''}`;
}
function addDays(d,n){const x=new Date(d+'T12:00:00');x.setDate(x.getDate()+n);return x.toISOString().slice(0,10)}
function extractedNote(pre){
  if(!pre||!pre._extracted)return'';const x=pre._extracted;const w=[];
  if(x.currency&&x.currency.toUpperCase()!=='EUR')w.push(T('Счёт в валюте {0}: пересчитайте цены в евро по курсу Европейского центрального банка на дату счёта.',esc(x.currency)));
  const sum=calc(pre.lines||[{qty:1,price:pre.net,vat:pre.vatRate}]).total;
  if(x.total&&Math.abs(r2(x.total)-sum)>0.05&&!RC.includes(x.treatment))w.push(T('Итог в документе {0} не совпадает с суммой строк. Проверьте строки.',eur(x.total)));
  return`<div class="info">${T('Данные прочитаны из файла. Проверьте их и нажмите «Сохранить».')}${x.notes?' '+esc(x.notes):''}</div>${w.map(t=>`<div class="info warn">${t}</div>`).join('')}`;
}

/* ---------- forms ---------- */
function fInvoice(id,pre){
  const d=id?S.invoices.find(x=>x.id===id):Object.assign({number:nextInvNo(),date:today(),dueDate:addDays(today(),+(S.company.dueDays??7)),treatment:'ee',lang:'et',lines:[{qty:1,vat:defRate()}]},pre||{});
  openSheet(`${head(id?T('Счёт {0}',esc(d.number)):T('Новый счёт'))}${plist()}
   <div class="grid">
    <label class="f">${T('Номер счёта')}<input id="i_no" value="${esc(d.number)}"></label>
    <label class="f">${T('Дата')}<input id="i_date" type="date" value="${esc(d.date)}"></label>
    <label class="f">${T('Клиент')}<input id="pname" list="plist" autocomplete="off" value="${esc(d.customer)}" placeholder="${T('Имя или фирма')}"></label>
    <label class="f">${T('Срок оплаты')}<input id="i_due" type="date" value="${esc(d.dueDate)}"></label>
    <label class="f">${T('Рег. код клиента')}<input id="i_creg" value="${esc(d.customerReg)}"></label>
    <label class="f">${T('VAT-номер клиента')}<input id="i_cvat" value="${esc(d.customerVat)}" placeholder="FI12345678"></label>
    <label class="f">${T('Страна')}<select id="i_country">${countryOpts(d.customerCountry||'EE')}</select></label>
    <label class="f">${T('Адрес или e-mail')}<input id="i_caddr" value="${esc(d.customerAddr)}"></label>
    <label class="f">${T('НДС')}<select id="treat">${tOpts(SALE_T,d.treatment||'ee')}</select></label>
    <label class="f">${T('Язык счёта')}<select id="i_lang"><option value="et" ${d.lang!=='en'?'selected':''}>Eesti</option><option value="en" ${d.lang==='en'?'selected':''}>English</option></select></label>
    <label class="f full">${T('Примечание в счёте')}<input id="i_note" value="${esc(d.note)}" placeholder="${T('например, номер заказа в магазине')}"></label>
   </div>
   <div class="info" id="treatHint" hidden></div>
   <div class="lines" id="lines">${(d.lines||[]).map(l=>lineRow(l,'sale')).join('')}</div>
   <div class="actions"><button class="btn sm" data-act="addLine" data-mode="sale">+ ${T('Строка')}</button><span class="sp"></span><label class="chk"><input type="checkbox" id="i_paid" ${d.paid?'checked':''}> ${T('Оплачен')}</label></div>
   <div class="totals" id="totals"></div>
   ${foot('invoices',id)}`,{kind:'invoice',id:id||'',bankTx:pre&&pre._bankTx},()=>zeroVat());
}
function vInvoiceDoc(id){
  const d=S.invoices.find(x=>x.id===id);if(!d)return;const c=calc(d.lines),co=S.company,t=d.treatment||'ee';
  openSheet(`${head(T('Счёт {0}',esc(d.number)))}
   <div class="inv">
    <div class="inv-top"><div><h3>${esc(co.name||T('Название фирмы'))}</h3><small>${esc([co.regCode&&T('Рег. код')+' '+co.regCode,co.vatNo&&'KMKR '+co.vatNo].filter(Boolean).join(' · '))}</small><small>${esc(co.address||'')}</small></div>
    <div class="kv"><span>${T('Номер')}</span><b class="num">${esc(d.number)}</b><span>${T('Дата')}</span><span class="num">${dfmt(d.date)}</span><span>${T('Срок оплаты')}</span><span class="num">${dfmt(d.dueDate)}</span><span>${T('Виитенумбер')}</span><span class="num">${esc(d.refNo||refNo(d.number))}</span></div></div>
    <div><small>${T('Покупатель')}</small><b>${esc(d.customer||'—')}</b>${[d.customerReg&&T('Рег. код')+' '+d.customerReg,d.customerVat&&'VAT '+d.customerVat,countryName(d.customerCountry),d.customerAddr].filter(Boolean).map(x=>`<small>${esc(x)}</small>`).join('')}</div>
    <div class="scroll"><table><thead><tr><th>${T('Наименование')}</th><th class="n">${T('Кол-во')}</th><th class="n">${T('Цена')}</th><th class="n">${T('НДС')}</th><th class="n">${T('Сумма')}</th></tr></thead><tbody>
    ${(d.lines||[]).map(l=>`<tr><td>${esc(l.name||prodName(l.productId))}</td><td class="n">${qfmt(l.qty)}</td><td class="n">${money(l.price)}</td><td class="n">${l.vat}%</td><td class="n">${money(l.qty*l.price)}</td></tr>`).join('')}</tbody></table></div>
    <div class="totals"><div><span>${T('Без НДС')}</span>${money(c.net)}</div>${Object.entries(c.by).map(([r,x])=>`<div><span>${T('НДС')} ${r}%</span>${money(x.vat)}</div>`).join('')}<div class="grand"><span>${T('К оплате')}</span>${money(c.total)}</div></div>
    ${t==='eu_b2b'||t==='export'?`<small>${esc(treatNote(d,'en'))}</small>`:''}
    ${d.note?`<small>${esc(d.note)}</small>`:''}
    ${co.iban?`<small>IBAN ${esc(co.iban)}${co.bank?' ('+esc(co.bank)+')':''} · ${T('виитенумбер')} ${esc(d.refNo||refNo(d.number))}</small>`:`<small>${T('Добавьте IBAN во вкладке «Настройки», и он появится в счёте.')}</small>`}
   </div>
   <div class="actions">${paidPill(d,'in')}${d.paidDate?` <span class="note">${dfmt(d.paidDate)}</span>`:''}<span class="sp"></span>
     <button class="btn" data-act="dlInvoice" data-id="${d.id}">${T('Скачать счёт')}</button>
     ${d.paid?'':`<button class="btn" data-act="markPaid" data-id="${d.id}">${T('Отметить оплату')}</button>`}
     <button class="btn pri" data-act="editInvoice" data-id="${d.id}">${T('Изменить')}</button></div>`,{kind:'view'});
}
function fPurchase(id,pre){
  const d=id?S.purchases.find(x=>x.id===id):Object.assign({date:today(),treatment:'ee',lines:[{qty:1,vat:defRate()}]},pre||{});
  openSheet(`${head(id?T('Закупка'):T('Новая закупка'))}${plist()}${extractedNote(pre)}
   <div class="grid">
    <label class="f">${T('Поставщик')}<input id="pname" list="plist" autocomplete="off" value="${esc(d.supplier)}"></label>
    <label class="f">${T('Номер счёта поставщика')}<input id="p_doc" value="${esc(d.docNo)}"></label>
    <label class="f">${T('Дата счёта')}<input id="p_date" type="date" value="${esc(d.date)}"></label>
    <label class="f">${T('Срок оплаты')}<input id="p_due" type="date" value="${esc(d.dueDate)}"></label>
    <label class="f full">${T('НДС')}<select id="treat">${tOpts(BUY_T,d.treatment||'ee')}</select></label>
   </div>
   <div class="info" id="treatHint" hidden></div>
   <div class="lines" id="lines">${(d.lines||[]).map(l=>lineRow(l,'buy')).join('')}</div>
   <div class="actions"><button class="btn sm" data-act="addLine" data-mode="buy">+ ${T('Строка')}</button><span class="sp"></span><label class="chk"><input type="checkbox" id="p_paid" ${d.paid?'checked':''}> ${T('Оплачено')}</label></div>
   <div class="totals" id="totals"></div>
   ${filesBlock()}
   ${foot('purchases',id)}`,{kind:'purchase',id:id||'',files:[...(d.files||[])],partnerInfo:pre&&pre._partner,bankTx:pre&&pre._bankTx},()=>{drawFiles();zeroVat()});
}
function fExpense(id,pre){
  const d=id?S.expenses.find(x=>x.id===id):Object.assign({date:today(),category:'other',treatment:'ee',vatRate:defRate(),vatDeductible:true,paid:true},pre||{});
  openSheet(`${head(id?T('Расход'):T('Новый расход'))}${plist()}${extractedNote(pre)}
   <div class="grid">
    <label class="f">${T('Категория')}<select id="e_cat">${EXP_CATS.map(([k,n])=>`<option value="${k}" ${d.category===k?'selected':''}>${T(n)}</option>`).join('')}</select></label>
    <label class="f">${T('Дата')}<input id="e_date" type="date" value="${esc(d.date)}"></label>
    <label class="f">${T('Кому заплатили')}<input id="pname" list="plist" autocomplete="off" value="${esc(d.supplier)}"></label>
    <label class="f">${T('Номер документа')}<input id="e_doc" value="${esc(d.docNo)}"></label>
    <label class="f full">${T('НДС')}<select id="treat">${tOpts(BUY_T,d.treatment||'ee')}</select></label>
    <label class="f">${T('Сумма без НДС')}<input id="e_net" type="number" step="0.01" inputmode="decimal" value="${esc(d.net)}"></label>
    <label class="f">${T('Ставка НДС')}<select id="e_vat">${rateOpts(d.vatRate)}</select></label>
    <label class="f full">${T('Комментарий')}<input id="e_note" value="${esc(d.note)}"></label>
   </div>
   <div class="info" id="treatHint" hidden></div>
   <div class="actions"><label class="chk"><input type="checkbox" id="e_ded" ${d.vatDeductible!==false?'checked':''}> ${T('НДС к вычету')}</label><label class="chk"><input type="checkbox" id="e_paid" ${d.paid?'checked':''}> ${T('Оплачено')}</label></div>
   <div class="totals" id="totals"></div>
   ${filesBlock()}
   ${foot('expenses',id)}`,{kind:'expense',id:id||'',files:[...(d.files||[])],partnerInfo:pre&&pre._partner,bankTx:pre&&pre._bankTx},()=>{drawFiles();zeroVat()});
}
function fProduct(id){
  const d=id?S.products.find(x=>x.id===id):{unit:unitDefault(),vatRate:defRate(),minStock:0};
  const x=id?stock()[id]:null;
  const cats=[...new Set([...PROD_CATS.map(c=>T(c)),...S.products.map(p=>p.category).filter(Boolean)])];
  openSheet(`${head(id?esc(d.name):T('Новый товар'))}
   <datalist id="catlist">${cats.map(c=>`<option value="${esc(c)}">`).join('')}</datalist>${plist()}
   <div class="pcell"><div id="photoBox">${thumb(d.photo?d:null,88)}</div>
    <div class="stack sm-gap"><label class="btn sm filebtn">${T('Загрузить фото')}<input type="file" id="photoIn" accept="image/jpeg,image/png,image/webp" hidden></label>
    ${x?`<div class="kv"><span>${T('Остаток')}</span><b class="num">${qfmt(x.qty)} ${esc(d.unit||'')}</b><span>${T('Себестоимость')}</span><span class="num">${eur(x.avg)}</span><span>${T('Закуплено / продано')}</span><span class="num">${qfmt(x.bought)} / ${qfmt(x.sold)}</span></div>`:''}</div></div>
   <div class="grid">
    <label class="f full">${T('Название')}<input id="pr_name" value="${esc(d.name)}"></label>
    <label class="f">${T('Категория')}<input id="pr_cat" list="catlist" value="${esc(d.category)}"></label>
    <label class="f">${T('Бренд')}<input id="pr_brand" value="${esc(d.brand)}"></label>
    <label class="f">${T('Размер')}<input id="pr_size" value="${esc(d.size)}" placeholder="S, M, L, XL"></label>
    <label class="f">${T('Цвет')}<input id="pr_color" value="${esc(d.color)}"></label>
    <label class="f">${T('Материал / состав')}<input id="pr_mat" value="${esc(d.material)}"></label>
    <label class="f">${T('Страна производства')}<input id="pr_origin" value="${esc(d.origin)}"></label>
    <label class="f">${T('Код (SKU)')}<input id="pr_sku" value="${esc(d.sku)}"></label>
    <label class="f">${T('Штрихкод EAN')}<input id="pr_ean" value="${esc(d.ean)}" inputmode="numeric"></label>
    <label class="f">${T('Поставщик')}<input id="pr_sup" list="plist" value="${esc(d.supplierName)}"></label>
    <label class="f">${T('Код у поставщика')}<input id="pr_supsku" value="${esc(d.supplierSku)}"></label>
    <label class="f">${T('Цена продажи без НДС')}<input id="pr_price" type="number" step="0.01" inputmode="decimal" value="${esc(d.price)}"></label>
    <label class="f">${T('НДС')}<select id="pr_vat">${rateOpts(d.vatRate)}</select></label>
    <label class="f">${T('Единица')}<input id="pr_unit" value="${esc(d.unit)}"></label>
    <label class="f">${T('Вес, г')}<input id="pr_w" type="number" step="any" value="${esc(d.weight)}"></label>
    <label class="f">${T('Минимальный остаток')}<input id="pr_min" type="number" step="any" value="${esc(d.minStock)}"></label>
    <label class="f">${T('Место на складе')}<input id="pr_loc" value="${esc(d.location)}"></label>
    <label class="f full">${T('Описание')}<textarea id="pr_desc" rows="3">${esc(d.description)}</textarea></label>
   </div>
   <div class="info">${d.price?T('С НДС {0}',eur(d.price*(1+(+d.vatRate||0)/100)))+'. ':''}${T('Цену можно ввести с НДС: напишите её и нажмите «Из цены с НДС».')} <button class="btn sm" type="button" data-act="fromGross">${T('Из цены с НДС')}</button></div>
   ${foot('products',id)}`,{kind:'product',id:id||'',photo:d.photo||null});
}
function fPartner(id){
  const d=id?S.partners.find(x=>x.id===id):{country:'EE'};
  openSheet(`${head(id?esc(d.name):T('Новый партнёр'))}
   <div class="grid">
    <label class="f full">${T('Название фирмы или имя')}<input id="pa_name" value="${esc(d.name)}"></label>
    <label class="f">${T('Страна')}<select id="pa_country">${countryOpts(d.country)}</select></label>
    <label class="f">${T('Рег. код')}<input id="pa_reg" value="${esc(d.regCode)}"></label>
    <label class="f">${T('VAT-номер')}<input id="pa_vat" value="${esc(d.vatNo)}" placeholder="EE…, FI…, DE…"></label>
    <label class="f">IBAN<input id="pa_iban" value="${esc(d.iban)}"></label>
    <label class="f full">${T('Адрес')}<input id="pa_addr" value="${esc(d.address)}"></label>
    <label class="f">${T('E-mail')}<input id="pa_email" value="${esc(d.email)}"></label>
    <label class="f">${T('Телефон')}<input id="pa_phone" value="${esc(d.phone)}"></label>
    <label class="f full">${T('Заметка')}<input id="pa_note" value="${esc(d.note)}"></label>
   </div>
   ${foot('partners',id)}`,{kind:'partner',id:id||''});
}
function fAdjust(id){
  const d=id?S.adjustments.find(x=>x.id===id):{date:today(),qty:-1,reason:''};
  const opts=S.products.map(p=>`<option value="${p.id}" ${d.productId===p.id?'selected':''}>${esc(p.name)}</option>`).join('');
  openSheet(`${head(T('Корректировка склада'))}
   ${S.products.length?'':`<div class="info warn">${T('Сначала добавьте товар.')}</div>`}
   <div class="grid">
    <label class="f full">${T('Товар')}<select id="a_prod">${opts}</select></label>
    <label class="f">${T('Дата')}<input id="a_date" type="date" value="${esc(d.date)}"></label>
    <label class="f">${T('Количество (минус = списание)')}<input id="a_qty" type="number" step="any" value="${esc(d.qty)}"></label>
    <label class="f full">${T('Причина')}<input id="a_reason" value="${esc(d.reason)}" placeholder="${T('Брак, подарок, начальный остаток, пересчёт')}"></label>
   </div>
   ${foot('adjustments',id)}`,{kind:'adjust',id:id||''});
}
function fIgnore(txId){
  openSheet(`${head(T('Не учитывать платёж'))}
   <p class="note">${T('Платёж не будет считаться ни продажей, ни расходом. Например, оплата налогов или перевод между своими счетами.')}</p>
   <label class="f">${T('Причина')}<select id="ig_r">${IGNORE.map(([k,n])=>`<option value="${k}">${T(n)}</option>`).join('')}</select></label>
   <div class="actions"><span class="sp"></span><button class="btn" data-act="close">${T('Отмена')}</button><button class="btn pri" data-act="doIgnore" data-id="${txId}">${T('Готово')}</button></div>`,{kind:'ignore'});
}

/* ---------- upload & recognition ---------- */
function fUpload(){
  const ready=S.companyInfo&&S.companyInfo.aiReady;
  openSheet(`${head(T('Загрузить счёт'))}
   <p class="note">${T('PDF или фото счёта от поставщика. Программа прочитает поставщика, номер, даты и строки, а вы проверите и сохраните. Файл прикрепится к документу.')}</p>
   <div class="seg" role="radiogroup">
     <label><input type="radio" name="upk" value="auto" checked> ${T('Определить само')}</label>
     <label><input type="radio" name="upk" value="purchase"> ${T('Закупка товара')}</label>
     <label><input type="radio" name="upk" value="expense"> ${T('Расход')}</label>
   </div>
   <div class="drop" id="drop"><b>${T('Перетащите файл сюда')}</b><span class="note">${T('или выберите')}</span><input type="file" id="upFile" accept="application/pdf,image/jpeg,image/png,image/webp"></div>
   <div class="status" id="upStatus" hidden></div>
   ${ready?'':`<div class="info warn">${T('Распознавание выключено: в настройках не указан ключ. Файл всё равно прикрепится, а документ заполните вручную.')}</div>`}`,{kind:'upload'});
}
function upStatus(t,busy){const el=sheet&&sheet.querySelector('#upStatus');if(!el)return;el.hidden=!t;el.innerHTML=(busy?'<span class="spin"></span>':'')+esc(t)}
function extractPrompt(kindHint){
  const co=S.company;const langName={et:'Estonian',en:'English',ru:'Russian'}[LANG];
  const prods=S.products.slice(0,200).map(p=>`${p.id} | ${p.name} | ${p.sku||''} | ${p.supplierSku||''} | ${p.ean||''} | ${p.size||''} | ${p.color||''}`).join('\n');
  return `You extract data from the attached supplier invoice or receipt for the bookkeeping of an Estonian VAT-registered company.
Our company (the BUYER): ${co.name||'unknown'}, reg code ${co.regCode||'?'}, VAT ${co.vatNo||'?'}. The other party is the supplier.
${kindHint==='auto'?'Decide "kind": "purchase" if it is goods bought for resale (clothes, merchandise, stock), otherwise "expense" (services, software, advertising, shipping, fees, rent, office supplies).':'The user says this document is a '+kindHint+'. Use kind "'+kindHint+'".'}
Reply with ONLY one JSON object, no prose:
{"kind":"purchase"|"expense",
 "supplier":{"name":"","regCode":"","vatNo":"","country":"2-letter ISO code","address":"","email":"","iban":""},
 "docNo":"","date":"YYYY-MM-DD","dueDate":"YYYY-MM-DD or empty","currency":"EUR",
 "treatment":"ee"|"eu_goods"|"eu_services"|"foreign_services"|"none",
 "category":one of ${JSON.stringify(EXP_CATS.map(c=>c[0]))},
 "lines":[{"name":"","sku":"","ean":"","size":"","color":"","qty":1,"unitPriceNet":0,"vatRate":24,"matchProductId":null}],
 "totalNet":0,"totalVat":0,"total":0,"notes":""}
Rules:
- unitPriceNet is the price per unit WITHOUT VAT, after line discounts. If the document shows prices including VAT, divide by (1+rate/100). Round to 2 decimals (4 if needed for small unit prices).
- vatRate is the VAT percent shown for the line (0 when the invoice charges no VAT).
- treatment: "ee" if the supplier is Estonian and charges Estonian VAT; "eu_goods" if goods come from a company in another EU country without VAT (reverse charge / intra-community supply); "eu_services" for services from another EU country without VAT; "foreign_services" for services from outside the EU; "none" for anything else without VAT.
- Shipping or fees on a goods invoice go as their own line with matchProductId null.
- For each goods line, set matchProductId to the id of the matching product from our list below if it is clearly the same item (same code, or same name AND size/color); otherwise null. Put size and color in their own fields when the line states them.
- Keep line names in the language of the document. Use empty strings for unknown text fields. Dates as YYYY-MM-DD. Numbers as plain JSON numbers.
- notes: one short sentence in ${langName} only if something is unclear (for example a missing date or a currency other than EUR); otherwise empty.
Our products (id | name | sku | supplier sku | ean | size | color):
${prods||'(none yet)'}`;
}
const AI_ERR={no_ai_key:'Распознавание выключено: в настройках не указан ключ.',ai_bad_key:'Ключ распознавания неверный. Проверьте его в настройках.',ai_busy:'Сервис распознавания занят. Попробуйте через минуту.',ai_bad_answer:'Не получилось разобрать счёт. Попробуйте ещё раз или заполните вручную.',ai_refused:'Сервис отказался читать этот файл. Заполните документ вручную.',bad_type:'Нужен PDF или фото (JPG, PNG).',too_large:'Файл больше 20 МБ.',offline:'Нет связи с сервером.'};
async function recognize(file){
  const kindHint=(sheet.querySelector('input[name=upk]:checked')||{}).value||'auto';
  let fileRef=null;
  upStatus(T('Сохраняю файл…'),true);
  try{const r=await uploadFile(file);fileRef={id:r.id,name:r.name,type:r.contentType}}
  catch(e){upStatus(T(AI_ERR[e.code]||'Не удалось загрузить файл.'),false);return}
  if(!(S.companyInfo&&S.companyInfo.aiReady)){closeSheet();(kindHint==='expense'?fExpense:fPurchase)(null,{files:[fileRef]});return}
  upStatus(T('Распознаю счёт… обычно 10–40 секунд'),true);
  try{
    const {result:x}=await api('POST','/api/ai/extract',{fileId:fileRef.id,prompt:extractPrompt(kindHint)});
    if(!x||typeof x!=='object')throw{code:'ai_bad_answer'};
    applyExtracted(x,kindHint,fileRef);
  }catch(e){
    upStatus(T(AI_ERR[e.code]||'Не получилось распознать файл. Попробуйте ещё раз или заполните вручную.'),false);
    SH.fileRef=fileRef;sheet.querySelector('#upStatus').insertAdjacentHTML('beforeend',` <button class="btn sm" data-act="manualFromUpload">${T('Заполнить вручную')}</button>`);
  }
}
function applyExtracted(x,kindHint,fileRef){
  const kind=kindHint!=='auto'?kindHint:(x.kind==='expense'?'expense':'purchase');
  const sup=x.supplier||{};
  const existing=findPartner(sup.name,sup.vatNo,sup.regCode);
  const partnerInfo=existing?null:{name:sup.name||'',regCode:sup.regCode||'',vatNo:sup.vatNo||'',country:(sup.country||'').toUpperCase().slice(0,2),address:sup.address||'',email:sup.email||'',iban:sup.iban||''};
  const treat=Object.keys(BUY_T).includes(x.treatment)?x.treatment:buyT(existing||partnerInfo,kind);
  const iso=s=>/^\d{4}-\d{2}-\d{2}$/.test(s||'');
  const base={supplier:existing?existing.name:(sup.name||''),docNo:x.docNo||'',date:iso(x.date)?x.date:today(),dueDate:iso(x.dueDate)?x.dueDate:'',treatment:treat,files:fileRef?[fileRef]:[],_partner:partnerInfo,_extracted:x};
  const lines=(Array.isArray(x.lines)?x.lines:[]).map(l=>{
    let pid=l.matchProductId&&S.products.some(p=>p.id===l.matchProductId)?l.matchProductId:null;
    if(!pid){const k=[l.sku,l.ean].filter(Boolean).map(norm);const p=S.products.find(p=>k.length&&k.some(v=>[p.sku,p.supplierSku,p.ean].map(norm).includes(v)));if(p)pid=p.id}
    const goods=kind==='purchase'&&!/shipping|delivery|transport|доставк|tarne|transpordi|saatmis/i.test(l.name||'');
    return{productId:pid||(goods?'__new':null),name:l.name||'',sku:l.sku||'',ean:l.ean||'',size:l.size||'',color:l.color||'',qty:+l.qty||1,price:Math.round((+l.unitPriceNet||0)*10000)/10000,vat:RC.includes(treat)||treat==='none'?0:(l.vatRate??defRate())};
  });
  closeSheet();
  if(kind==='expense'){
    const net=r2(lines.reduce((a,l)=>a+l.qty*l.price,0))||r2(x.totalNet);
    const rate=lines.length?lines[0].vat:(x.totalNet?r2(x.totalVat/x.totalNet*100):defRate());
    fExpense(null,{...base,category:EXP_CATS.some(c=>c[0]===x.category)?x.category:'other',net,vatRate:Math.round(rate),vatDeductible:true,paid:false,note:lines.map(l=>l.name).filter(Boolean).slice(0,3).join(', ')});
  }else fPurchase(null,{...base,paid:false,lines:lines.length?lines:[{qty:1,vat:defRate()}]});
}

/* ---------- bank import ---------- */
function fBankImport(){
  openSheet(`${head(T('Выписка из банка'))}
   <p class="note">${T('В интернет-банке выберите выписку за месяц и скачайте её в формате CSV или XML (camt.053). Подходят Swedbank, SEB, LHV, Coop и другие банки. Повторная загрузка той же выписки не создаст дубликатов.')}</p>
   <div class="drop" id="drop"><b>${T('Перетащите файл выписки сюда')}</b><span class="note">${T('или выберите')}</span><input type="file" id="bankFile" accept=".csv,.xml,.txt,text/csv,text/xml,application/xml"></div>
   <div class="status" id="upStatus" hidden></div>
   <div id="bankPreview"></div>`,{kind:'bankImport'});
}
async function readText(file){
  const buf=await file.arrayBuffer();let t=new TextDecoder('utf-8').decode(buf);
  if(t.includes('�')){try{t=new TextDecoder('windows-1257').decode(buf)}catch(_){}}
  return t.replace(/^﻿/,'');
}
function parseCSV(text){
  const first=text.split(/\r?\n/).slice(0,5).join('\n');
  const cnt=ch=>first.split(ch).length;const delim=[';',',','\t'].sort((a,b)=>cnt(b)-cnt(a))[0];
  const rows=[];let row=[],f='',q=false;
  for(let i=0;i<text.length;i++){const c=text[i];
    if(q){if(c==='"'){if(text[i+1]==='"'){f+='"';i++}else q=false}else f+=c}
    else if(c==='"')q=true;else if(c===delim){row.push(f);f=''}else if(c==='\n'||c==='\r'){if(c==='\r'&&text[i+1]==='\n')i++;row.push(f);f='';if(row.some(x=>x.trim()))rows.push(row);row=[]}else f+=c}
  row.push(f);if(row.some(x=>x.trim()))rows.push(row);
  return rows;
}
function parseAmount(s){s=String(s||'').replace(/\s| /g,'').replace(/[^\d,.\-+]/g,'');if(!s)return NaN;
  const lc=s.lastIndexOf(','),ld=s.lastIndexOf('.');
  if(lc>-1&&ld>-1)s=lc>ld?s.replace(/\./g,'').replace(',','.'):s.replace(/,/g,'');else if(lc>-1)s=s.replace(',','.');
  return parseFloat(s)}
function parseDate(s){s=String(s||'').trim();let m=s.match(/(\d{4})-(\d{2})-(\d{2})/);if(m)return`${m[1]}-${m[2]}-${m[3]}`;
  m=s.match(/(\d{1,2})[./-](\d{1,2})[./-](\d{4})/);if(m)return`${m[3]}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`;return''}
function mapColumns(h){
  const H=h.map(norm);
  const find=(keys,not=[])=>H.findIndex(x=>keys.some(k=>x.includes(k))&&!not.some(n=>x.includes(n)));
  return{date:find(['kuupäev','kuupaev','booking date','date','дата']),amount:find(['summa','amount','сумма'],['fee','teenustasu']),dc:find(['deebet/kreedit','debit/credit','d/c','d/k','дебет']),
    name:find(['saaja/maksja','sender/receiver name','beneficiary','counterparty','payer','получатель','name','nimi'],['konto','account','iban','bank','ultimate']),
    account:find(['saaja/maksja konto','sender/receiver account','counterparty account','iban'],['customer','kliendi']),
    desc:find(['selgitus','description','details','пояснение','назначение','message']),ref:find(['viitenumber','reference number','viide']),
    arch:find(['arhiveerimistunnus','archiving code','archive','account servicer reference','transaction id']),cur:find(['valuuta','currency','валюта']),rowType:find(['reatüüp','reatuup'])};
}
async function parseBankFile(file){
  const text=await readText(file);
  const txs=[];
  if(/<\s*(\w+:)?Document[\s>]/.test(text)&&/Ntry/.test(text)){
    const x=new DOMParser().parseFromString(text,'application/xml');
    const all=(el,n)=>[...el.getElementsByTagNameNS('*',n)];const one=(el,n)=>all(el,n)[0];const tx=(el,n)=>{const e=el&&one(el,n);return e?e.textContent.trim():''};
    for(const e of all(x,'Ntry')){
      const amtEl=one(e,'Amt');let amt=parseFloat(amtEl?amtEl.textContent:'0');const ind=tx(e,'CdtDbtInd');if(ind==='DBIT')amt=-amt;
      const bd=one(e,'BookgDt');const date=parseDate(bd?bd.textContent:'');
      const party=ind==='CRDT'?one(e,'Dbtr'):one(e,'Cdtr');
      const acc=ind==='CRDT'?one(e,'DbtrAcct'):one(e,'CdtrAcct');
      txs.push({date,amount:r2(amt),currency:amtEl&&amtEl.getAttribute('Ccy')||'EUR',name:tx(party,'Nm'),account:tx(acc,'IBAN'),desc:all(e,'Ustrd').map(u=>u.textContent.trim()).join(' '),ref:tx(one(e,'CdtrRefInf'),'Ref'),arch:tx(e,'AcctSvcrRef')});
    }
    return{txs,format:'XML camt.053'};
  }
  const rows=parseCSV(text);if(rows.length<2)throw new Error('empty');
  let hi=rows.findIndex(r=>{const m=mapColumns(r);return m.date>-1&&m.amount>-1});
  let M;
  if(hi===-1){
    if(!(S.companyInfo&&S.companyInfo.aiReady))throw new Error('columns');
    const {result:g}=await api('POST','/api/ai/extract',{prompt:`These are the first rows of a bank statement CSV. Reply with ONLY JSON {"headerRow":index of the header row or -1,"date":col,"amount":col,"dc":col or -1,"name":col,"account":col or -1,"desc":col,"ref":col or -1,"arch":col or -1,"cur":col or -1,"rowType":-1}. Columns are 0-based indexes.\n\n`+rows.slice(0,6).map((r,i)=>i+': '+JSON.stringify(r)).join('\n')});
    hi=+g.headerRow;M={...mapColumns([]),...g};
  }else M=mapColumns(rows[hi]);
  for(const r of rows.slice(hi+1)){
    if(M.rowType>-1&&r[M.rowType]&&r[M.rowType].trim()!=='20')continue;
    const date=parseDate(r[M.date]);let amt=parseAmount(r[M.amount]);if(!date||isNaN(amt))continue;
    const dc=M.dc>-1?String(r[M.dc]||'').trim().toUpperCase():'';
    if(dc){if(/^(D|Д)/.test(dc))amt=-Math.abs(amt);else if(/^(K|C|К)/.test(dc))amt=Math.abs(amt)}
    const g=k=>M[k]>-1?String(r[M[k]]||'').trim():'';
    txs.push({date,amount:r2(amt),currency:g('cur')||'EUR',name:g('name'),account:g('account'),desc:g('desc'),ref:g('ref'),arch:g('arch')});
  }
  return{txs,format:'CSV'};
}
function txId(t){const s=[t.date,t.amount,t.arch,t.name,t.desc,t.ref].join('|');let h=5381,h2=0;for(let i=0;i<s.length;i++){h=((h*33)^s.charCodeAt(i))>>>0;h2=(h2*31+s.charCodeAt(i))>>>0}return'tx-'+t.date+'-'+h.toString(36)+h2.toString(36)}
async function handleBankFile(file){
  upStatus(T('Читаю выписку…'),true);
  try{
    const{txs,format}=await parseBankFile(file);
    if(!txs.length){upStatus(T('В файле не найдено операций. Проверьте, что это выписка в CSV или XML.'),false);return}
    const have=new Set(S.bank.map(t=>t.id));const fresh=[];const seen=new Set();
    for(const t of txs){const id=txId(t);if(have.has(id)||seen.has(id))continue;seen.add(id);fresh.push({...t,id})}
    const foreign=txs.filter(t=>t.currency&&t.currency!=='EUR').length;
    const dates=txs.map(t=>t.date).sort();
    upStatus('',false);
    sheet.querySelector('#bankPreview').innerHTML=`<div class="info">${T('Формат {0}. Операций: {1}, период {2}–{3}. Новых: {4}.',format,txs.length,dfmt(dates[0]),dfmt(dates[dates.length-1]),fresh.length)}${foreign?' '+T('Операций не в евро: {0}, их суммы пересчитайте вручную.',foreign):''}</div>
      <div class="actions mt"><span class="sp"></span><button class="btn" data-act="close">${T('Отмена')}</button><button class="btn pri" data-act="bankSave" ${fresh.length?'':'disabled'}>${T('Загрузить {0}',fresh.length)}</button></div>`;
    SH.fresh=fresh;SH.fileName=file.name;SH.lastMonth=dates[dates.length-1].slice(0,7);
  }catch(e){upStatus(e&&e.message==='columns'?T('Не удалось понять столбцы выписки. Скачайте её в формате XML camt.053.'):T('Не удалось прочитать файл. Нужна выписка в CSV или XML camt.053.'),false)}
}
async function bankSave(btn){
  const list=SH.fresh||[];btn.disabled=true;let done=0,err=0;
  for(const t of list){const{id,...body}=t;try{await store.set('bank',id,{...body,status:'new',source:SH.fileName||'',importedAt:Date.now()});done++}catch(e){err++}
    if(done%10===0)btn.textContent=T('Загружаю… {0}/{1}',done,list.length)}
  const m=SH.lastMonth;closeSheet();if(m)S.month=m;S.tab='bank';render();
  toast(err?T('Загружено {0}, с ошибкой {1}.',done,err):T('Загружено операций: {0}. Ниже предложены пары со счетами.',done));
}
async function linkTx(txId,col,docId){
  const t=S.bank.find(x=>x.id===txId),d=S[col].find(x=>x.id===docId);if(!t||!d)return;
  const label=col==='invoices'?d.number:(d.docNo||d.supplier||'');
  try{await store.update(col,docId,{paid:true,paidDate:t.date,bankTxId:t.id});await store.update('bank',txId,{status:'matched',match:{col,id:docId,label}});toast(T('Связано, документ отмечен оплаченным'))}catch(_){toast(T('Не удалось сохранить.'))}
}

/* ---------- save ---------- */
async function ensurePartner(name,info,role){
  name=(name||'').trim();if(!name)return null;
  const ex=findPartner(name,info&&info.vatNo,info&&info.regCode)||S.partners.find(p=>norm(p.name)===norm(name));if(ex)return ex.id;
  const body={...(info&&norm(info.name)===norm(name)?info:{}),name,role,createdAt:Date.now()};body.country=body.country||'EE';
  return store.add('partners',body);
}
async function save(){
  const kind=SH.kind,id=SH.id,col={invoice:'invoices',purchase:'purchases',expense:'expenses',product:'products',adjust:'adjustments',partner:'partners'}[kind];
  let data,err;
  if(kind==='invoice'){
    const t=val('treat');let lines=readLines().map(l=>({...l,name:l.name||(l.productId?prodName(l.productId):'')}));
    if(t==='eu_b2b'||t==='export')lines=lines.map(l=>({...l,vat:0}));
    const number=val('i_no').trim();
    data={number,refNo:refNo(number),date:val('i_date'),dueDate:val('i_due'),customer:val('pname').trim(),customerReg:val('i_creg').trim(),customerVat:val('i_cvat').trim(),customerCountry:val('i_country'),customerAddr:val('i_caddr').trim(),treatment:t,lang:val('i_lang'),note:val('i_note').trim(),lines,paid:chk('i_paid')};
    if(!data.number)err='Укажите номер счёта.';else if(!lines.length)err='Добавьте хотя бы одну строку.';
    else if(S.invoices.some(x=>x.number===data.number&&x.id!==id))err='Счёт с таким номером уже есть.';
    else if(t==='eu_b2b'&&!data.customerVat)err='Для 0% фирме из ЕС нужен её VAT-номер.';
    else if(t==='eu_b2b'&&!isEU(data.customerCountry))err='Выберите страну ЕС клиента.';
  }else if(kind==='purchase'){
    const t=val('treat');let lines=readLines();if(t!=='ee')lines=lines.map(l=>({...l,vat:0}));
    data={supplier:val('pname').trim(),date:val('p_date'),dueDate:val('p_due'),docNo:val('p_doc').trim(),treatment:t,lines,paid:chk('p_paid'),files:SH.files||[]};
    if(RC.includes(t))data.rcRate=(id&&S.purchases.find(x=>x.id===id)||{}).rcRate??defRate();
    if(!lines.length)err='Добавьте хотя бы одну строку.';else if(lines.some(l=>!l.productId))err='В каждой строке выберите товар или «+ новый товар».';
    else if(lines.some(l=>l.productId==='__new'&&!l.name))err='У нового товара должно быть описание.';
  }else if(kind==='expense'){
    const t=val('treat');
    data={category:val('e_cat'),date:val('e_date'),supplier:val('pname').trim(),docNo:val('e_doc').trim(),treatment:t,net:r2(val('e_net')),vatRate:t==='ee'?+val('e_vat'):0,vatDeductible:chk('e_ded'),paid:chk('e_paid'),note:val('e_note').trim(),files:SH.files||[]};
    if(RC.includes(t))data.rcRate=(id&&S.expenses.find(x=>x.id===id)||{}).rcRate??defRate();
    if(!data.net)err='Укажите сумму.';
  }else if(kind==='product'){
    data={name:val('pr_name').trim(),category:val('pr_cat').trim(),brand:val('pr_brand').trim(),size:val('pr_size').trim(),color:val('pr_color').trim(),material:val('pr_mat').trim(),origin:val('pr_origin').trim(),sku:val('pr_sku').trim(),ean:val('pr_ean').trim(),supplierName:val('pr_sup').trim(),supplierSku:val('pr_supsku').trim(),
      price:r2(val('pr_price')),vatRate:+val('pr_vat'),unit:val('pr_unit').trim()||unitDefault(),weight:+val('pr_w')||null,minStock:+val('pr_min')||0,location:val('pr_loc').trim(),description:val('pr_desc').trim(),photo:SH.photo||null};
    if(!data.name)err='Укажите название.';
  }else if(kind==='partner'){
    data={name:val('pa_name').trim(),country:val('pa_country'),regCode:val('pa_reg').trim(),vatNo:val('pa_vat').trim().toUpperCase(),iban:val('pa_iban').trim(),address:val('pa_addr').trim(),email:val('pa_email').trim(),phone:val('pa_phone').trim(),note:val('pa_note').trim()};
    if(!data.name)err='Укажите название.';
  }else if(kind==='adjust'){
    data={productId:val('a_prod'),date:val('a_date'),qty:+val('a_qty')||0,reason:val('a_reason').trim()};
    if(!data.productId)err='Выберите товар.';else if(!data.qty)err='Количество не может быть 0.';
  }
  if(!err&&!data.date&&!['product','partner'].includes(kind))err='Укажите дату.';
  if(err)return toast(T(err));
  const btn=sheet.querySelector('[data-act=save]');if(btn)btn.disabled=true;
  try{
    if(kind==='invoice')data.partnerId=await ensurePartner(data.customer,{name:data.customer,regCode:data.customerReg,vatNo:data.customerVat,country:data.customerCountry,address:data.customerAddr},'customer');
    if(kind==='purchase'||kind==='expense')data.partnerId=await ensurePartner(data.supplier,SH.partnerInfo,'supplier');
    let created=0;
    if(kind==='purchase'){
      for(const l of data.lines){if(l.productId!=='__new')continue;
        l.productId=await store.add('products',{name:l.name,sku:l.sku||'',ean:l.ean||'',size:l.size||'',color:l.color||'',supplierName:data.supplier,supplierSku:l.sku||'',unit:unitDefault(),price:0,vatRate:defRate(),minStock:0,createdAt:Date.now()});created++}
    }
    if(SH.bankTx){const t=S.bank.find(x=>x.id===SH.bankTx);if(t){data.paid=true;data.paidDate=t.date;data.bankTxId=t.id}}
    let docId=id;
    if(id){const old=S[col].find(x=>x.id===id)||{};const keep={};for(const k of['createdAt','paidDate','bankTxId'])if(old[k]!=null&&data[k]==null)keep[k]=old[k];
      await store.set(col,id,{...keep,...data,createdAt:old.createdAt||Date.now()})}
    else docId=await store.add(col,{...data,createdAt:Date.now()});
    if(SH.bankTx)await store.update('bank',SH.bankTx,{status:'matched',match:{col,id:docId,label:kind==='invoice'?data.number:(data.docNo||data.supplier||'')}});
    closeSheet();toast(created?T('Сохранено. Новые товары добавлены на склад: откройте их и допишите цену продажи.'):T('Сохранено'));
  }catch(e){if(btn)btn.disabled=false;toast(e.code==='offline'?T('Нет связи с сервером.'):T('Не удалось сохранить.'))}
}

/* ---------- invoice file, exports ---------- */
function treatNote(d,lang){
  const goods=(d.lines||[]).some(l=>l.productId),t=d.treatment;
  if(t==='eu_b2b'){
    if(lang==='et')return goods?'Ühendusesisene kaubatarne, käibemaks 0% (KMS § 15 lg 3 p 1). Pöördmaksustamine.':'Pöördmaksustamine, käibemaks 0%. Käibemaksu arvestab ostja.';
    return goods?'Intra-Community supply of goods, VAT 0% (Article 138, Directive 2006/112/EC).':'Reverse charge: VAT to be accounted for by the recipient (Article 196, Directive 2006/112/EC).';
  }
  if(t==='export')return lang==='et'?'Kauba eksport, käibemaks 0%.':'Export of goods, VAT 0%.';
  return'';
}
function invoiceFile(d){
  const c=calc(d.lines),co=S.company,en=d.lang==='en',ref=d.refNo||refNo(d.number);
  const F=new Intl.NumberFormat(en?'en-IE':'et-EE',{style:'currency',currency:'EUR'});const m=x=>F.format(r2(x));
  const L=en?{t:'INVOICE',date:'Date',due:'Due date',ref:'Reference no',to:'Bill to',reg:'Reg. no',vat:'VAT no',desc:'Description',qty:'Qty',price:'Unit price',vr:'VAT',sum:'Amount',sub:'Subtotal',vatl:'VAT',tot:'Total due',pay:`Please pay by ${dfmt(d.dueDate)} to ${co.iban||''}${co.bank?' ('+co.bank+')':''}, reference ${ref}.`,pen:co.penalty?`Late payment interest ${co.penalty}% per day.`:''}
    :{t:'ARVE',date:'Kuupäev',due:'Maksetähtaeg',ref:'Viitenumber',to:'Maksja',reg:'Reg. kood',vat:'KMKR',desc:'Kirjeldus',qty:'Kogus',price:'Hind',vr:'KM',sum:'Summa',sub:'Summa km-ta',vatl:'Käibemaks',tot:'Tasuda',pay:`Palume tasuda hiljemalt ${dfmt(d.dueDate)} kontole ${co.iban||''}${co.bank?' ('+co.bank+')':''}, viitenumber ${ref}.`,pen:co.penalty?`Viivis ${co.penalty}% päevas.`:''};
  const note=treatNote(d,en?'en':'et');
  return `<!doctype html><html lang="${en?'en':'et'}"><head><meta charset="utf-8"><title>${L.t} ${esc(d.number)}</title><style>body{font:14px/1.5 Arial,Helvetica,sans-serif;color:#111;max-width:760px;margin:40px auto;padding:0 20px}table{width:100%;border-collapse:collapse;margin:20px 0}th,td{padding:8px;border-bottom:1px solid #ccc;text-align:left}th{font-size:12px;text-transform:uppercase;color:#555}.n{text-align:right}.top{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}h1{margin:0 0 4px;font-size:22px}small{color:#555;display:block}.tot{margin-left:auto;width:300px}.tot div{display:flex;justify-content:space-between;padding:3px 0}.g{font-weight:bold;font-size:16px;border-top:2px solid #111;margin-top:4px}.box{margin:18px 0}.note{border:1px solid #ccc;padding:8px 10px;margin:14px 0}@media print{body{margin:0}}</style></head><body>
  <div class="top"><div><h1>${esc(co.name||'')}</h1><small>${esc(co.address||'')}</small><small>${L.reg} ${esc(co.regCode||'')}${co.vatNo?' · '+L.vat+' '+esc(co.vatNo):''}</small><small>${esc([co.email,co.phone,co.web].filter(Boolean).join(' · '))}</small></div>
  <div><h1>${L.t} ${esc(d.number)}</h1><small>${L.date}: ${dfmt(d.date)}</small><small>${L.due}: ${dfmt(d.dueDate)}</small><small>${L.ref}: ${esc(ref)}</small></div></div>
  <div class="box"><small>${L.to}</small><b>${esc(d.customer||'')}</b>${d.customerReg?`<small>${L.reg} ${esc(d.customerReg)}</small>`:''}${d.customerVat?`<small>${en?'VAT no':'KMKR'} ${esc(d.customerVat)}</small>`:''}${d.customerAddr?`<small>${esc(d.customerAddr)}</small>`:''}${d.customerCountry&&d.customerCountry!=='EE'?`<small>${esc(d.customerCountry)}</small>`:''}</div>
  <table><tr><th>${L.desc}</th><th class="n">${L.qty}</th><th class="n">${L.price}</th><th class="n">${L.vr}</th><th class="n">${L.sum}</th></tr>
  ${(d.lines||[]).map(l=>`<tr><td>${esc(l.name||prodName(l.productId))}</td><td class="n">${qfmt(l.qty)}</td><td class="n">${m(l.price)}</td><td class="n">${l.vat}%</td><td class="n">${m(l.qty*l.price)}</td></tr>`).join('')}</table>
  <div class="tot"><div><span>${L.sub}</span><span>${m(c.net)}</span></div>${Object.entries(c.by).map(([r,x])=>`<div><span>${L.vatl} ${r}%</span><span>${m(x.vat)}</span></div>`).join('')}<div class="g"><span>${L.tot}</span><span>${m(c.total)}</span></div></div>
  ${note?`<div class="note">${esc(note)}</div>`:''}${d.note?`<p>${esc(d.note)}</p>`:''}
  <p>${esc(L.pay)}${L.pen?'<br>'+esc(L.pen):''}</p></body></html>`;
}
function csvMonth(){
  const q=v=>{v=String(v??'');return /[;"\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
  const n=x=>r2(x).toFixed(2).replace('.',',');
  const head=['Дата','Тип','Номер','Партнёр','Страна','VAT партнёра','Категория','НДС-режим','Без НДС','НДС','Обратный НДС','К оплате','Оплачено','Дата оплаты'].map(x=>T(x));
  const yes=T('да'),no=T('нет');const rows=[];
  for(const d of S.invoices.filter(x=>inMonth(x.date))){const c=calc(d.lines);rows.push([d.date,T('Продажа'),d.number,d.customer,d.customerCountry,d.customerVat,'',T(SALE_T[d.treatment||'ee']),n(c.net),n(c.vat),'',n(c.total),d.paid?yes:no,d.paidDate||''])}
  for(const d of S.purchases.filter(x=>inMonth(x.date))){const c=docCalc(d,'purchase'),p=partnerOf(d)||{};rows.push([d.date,T('Закупка'),d.docNo,d.supplier,p.country,p.vatNo,T('Товар'),T(BUY_T[d.treatment||'ee']),n(c.net),n(c.vat),n(c.rc),n(c.total),d.paid?yes:no,d.paidDate||''])}
  for(const d of S.expenses.filter(x=>inMonth(x.date))){const c=docCalc(d,'expense'),p=partnerOf(d)||{};rows.push([d.date,T('Расход'),d.docNo,d.supplier,p.country,p.vatNo,catName(d.category),T(BUY_T[d.treatment||'ee']),n(c.net),n(c.deductible),n(c.rc),n(c.total),d.paid?yes:no,d.paidDate||''])}
  rows.sort((a,b)=>String(a[0]).localeCompare(String(b[0])));
  return '﻿'+[head,...rows].map(r=>r.map(q).join(';')).join('\r\n');
}

/* ---------- events ---------- */
const FORMS={invoice:fInvoice,purchase:fPurchase,expense:fExpense,product:fProduct,adjust:fAdjust,partner:fPartner,upload:fUpload,bankImport:fBankImport};
document.addEventListener('click',async e=>{
  const t=e.target.closest('[data-tab],[data-new],[data-open],[data-act],[data-del]');if(!t)return;
  if(t.dataset.tab){S.tab=t.dataset.tab;S.fab=false;S.q='';try{localStorage.setItem('kontor.tab',S.tab)}catch(_){}closeSheet();render();window.scrollTo({top:0});return}
  if(t.dataset.new){S.fab=false;render();FORMS[t.dataset.new]();return}
  if(t.dataset.open){const[k,id]=t.dataset.open.split(':');k==='invoice'?vInvoiceDoc(id):FORMS[k](id);return}
  if(t.dataset.del){
    if(t.dataset.armed!=='1'){t.dataset.armed='1';t.textContent=T('Точно удалить?');return}
    const[col,id]=t.dataset.del.split(':');
    const used=l=>l.productId===id;
    if(col==='products'&&(S.purchases.some(d=>(d.lines||[]).some(used))||S.invoices.some(d=>(d.lines||[]).some(used))||S.adjustments.some(used)))return toast(T('Товар есть в закупках или счетах, его нельзя удалить.'));
    if(col==='partners'&&[...S.invoices,...S.purchases,...S.expenses].some(d=>d.partnerId===id))return toast(T('У партнёра есть документы, его нельзя удалить.'));
    try{const d=S[col].find(x=>x.id===id);await store.del(col,id);
      if(d&&d.bankTxId&&S.bank.some(x=>x.id===d.bankTxId))await store.update('bank',d.bankTxId,{status:'new',match:null});
      closeSheet();toast(T('Удалено'))}catch(_){toast(T('Не удалось удалить.'))}
    return;
  }
  const a=t.dataset.act;
  if(a==='fab'){S.fab=!S.fab;render()}
  else if(a==='veil'){if(e.target===t)closeSheet()}
  else if(a==='close')closeSheet();
  else if(a==='save')save();
  else if(a==='addLine'){sheet.querySelector('#lines').insertAdjacentHTML('beforeend',lineRow({qty:1,vat:defRate()},t.dataset.mode));zeroVat()}
  else if(a==='delLine'){t.closest('.line').remove();refreshTotals()}
  else if(a==='editInvoice')fInvoice(t.dataset.id);
  else if(a==='markPaid'){try{await store.update('invoices',t.dataset.id,{paid:true,paidDate:today()});closeSheet();toast(T('Счёт отмечен оплаченным'))}catch(_){toast(T('Не удалось сохранить.'))}}
  else if(a==='dlInvoice'){const d=S.invoices.find(x=>x.id===t.dataset.id);saveFile((d.lang==='en'?'invoice-':'arve-')+String(d.number).replace(/[^\w.-]+/g,'_')+'.html',invoiceFile(d),'text/html')}
  else if(a==='csv')saveFile('kontor-'+S.month+'.csv',csvMonth(),'text/csv');
  else if(a==='json'){const o={exportedAt:new Date().toISOString(),company:S.company};for(const c of COLS)o[c]=S[c];saveFile('kontor-backup-'+today()+'.json',JSON.stringify(o,null,1),'application/json')}
  else if(a==='addRate'){$('#rates').insertAdjacentHTML('beforeend',rateRow({rate:'',label:''},false))}
  else if(a==='delRate'){if($('#rates').children.length>1)t.closest('.rrow').remove()}
  else if(a==='fromGross'){const p=sheet.querySelector('#pr_price'),r=+val('pr_vat');if(+p.value){p.value=r2(+p.value/(1+r/100));toast(T('Цена пересчитана без НДС'))}}
  else if(a==='manualFromUpload'){const f=SH.fileRef;const k=(sheet.querySelector('input[name=upk]:checked')||{}).value;closeSheet();(k==='expense'?fExpense:fPurchase)(null,{files:[f]})}
  else if(a==='bankSave')bankSave(t);
  else if(a==='link')linkTx(t.dataset.id,t.dataset.col,t.dataset.doc);
  else if(a==='unlink'){const x=S.bank.find(b=>b.id===t.dataset.id);try{if(x.match&&S[x.match.col]&&S[x.match.col].some(d=>d.id===x.match.id))await store.update(x.match.col,x.match.id,{paid:false,paidDate:null,bankTxId:null});await store.update('bank',x.id,{status:'new',match:null});toast(T('Связь убрана'))}catch(_){toast(T('Не удалось сохранить.'))}}
  else if(a==='txIgnore')fIgnore(t.dataset.id);
  else if(a==='doIgnore'){try{await store.update('bank',t.dataset.id,{status:'ignored',ignoreReason:val('ig_r')});closeSheet()}catch(_){toast(T('Не удалось сохранить.'))}}
  else if(a==='unignore'){try{await store.update('bank',t.dataset.id,{status:'new'})}catch(_){toast(T('Не удалось сохранить.'))}}
  else if(a==='txExpense'){const x=S.bank.find(b=>b.id===t.dataset.id),p=findPartner(x.name);const tr=buyT(p,'expense'),rate=tr==='ee'?defRate():0;
    fExpense(null,{date:x.date,supplier:p?p.name:x.name,net:r2(Math.abs(x.amount)/(1+rate/100)),vatRate:rate,treatment:tr,paid:true,note:x.desc,_bankTx:x.id})}
  else if(a==='txSale'){const x=S.bank.find(b=>b.id===t.dataset.id),p=findPartner(x.name);const tr=saleT(p),rate=tr==='ee'||tr==='eu_b2c'?defRate():0;
    fInvoice(null,{date:x.date,dueDate:x.date,customer:p?p.name:x.name,customerReg:p&&p.regCode||'',customerVat:p&&p.vatNo||'',customerCountry:p&&p.country||'EE',treatment:tr,paid:true,note:x.desc,lines:[{qty:1,name:'',price:r2(x.amount/(1+rate/100)),vat:rate}],_bankTx:x.id})}
  else if(a==='logout'){try{await api('POST','/api/logout')}catch(_){}if(es)es.close();S.me=null;renderLogin()}
  else if(a==='aiRemove'){try{await api('POST','/api/company/aikey',{key:''});S.companyInfo.aiReady=false;render();toast(T('Ключ удалён'))}catch(_){toast(T('Не удалось сохранить.'))}}
  else if(a==='delUser'){if(t.dataset.armed!=='1'){t.dataset.armed='1';t.textContent=T('Точно удалить?');return}try{await api('DELETE','/api/company/users/'+t.dataset.id);loadUsers()}catch(_){toast(T('Не удалось удалить.'))}}
  else if(a==='resetOwner'){const pw=Math.random().toString(36).slice(2,8)+Math.random().toString(36).slice(2,8);try{await api('POST',`/api/admin/companies/${t.dataset.id}/password`,{password:pw});t.outerHTML=`<span class="pill mute">${T('Новый пароль')}: <b class="num">${pw}</b></span>`}catch(_){toast(T('Не удалось сохранить.'))}}
});
document.addEventListener('input',e=>{
  if(e.target.id==='q'){S.q=e.target.value;S._focusQ=true;render();S._focusQ=false;return}
  if(sheet&&sheet.contains(e.target))refreshTotals();
});
document.addEventListener('change',async e=>{
  const el=e.target;
  if(el.id==='loginLang'||el.id==='appLang'){setLang(el.value);return}
  if(!sheet||!sheet.contains(el))return;
  if(el.id==='treat'){zeroVat();return}
  if(el.id==='pname'){const p=findPartner(el.value);if(!p)return;
    if(SH.kind==='invoice'){const set=(id,v)=>{const x=sheet.querySelector('#'+id);if(x&&!x.value)x.value=v||''};set('i_creg',p.regCode);set('i_cvat',p.vatNo);set('i_caddr',p.address||p.email);sheet.querySelector('#i_country').value=p.country||'EE';sheet.querySelector('#treat').value=saleT(p);sheet.querySelector('#i_lang').value=p.country&&p.country!=='EE'?'en':'et'}
    else sheet.querySelector('#treat').value=buyT(p,SH.kind);
    zeroVat();return}
  if(el.classList.contains('l-prod')){
    const row=el.closest('.line'),p=S.products.find(x=>x.id===el.value);if(!p)return;
    row.querySelector('.l-name').value=p.name+(p.size&&!p.name.includes(p.size)?', '+p.size:'');
    if(!row.querySelector('.l-vat').disabled)row.querySelector('.l-vat').value=String(+p.vatRate||0);
    if(SH.kind==='invoice')row.querySelector('.l-price').value=p.price??'';
    else{const x=stock()[p.id];if(x&&x.avg&&!row.querySelector('.l-price').value)row.querySelector('.l-price').value=r2(x.avg)}
    refreshTotals();return}
  if(el.id==='upFile'&&el.files[0]){recognize(el.files[0]);return}
  if(el.id==='bankFile'&&el.files[0]){handleBankFile(el.files[0]);return}
  if(el.id==='attach'&&el.files[0]){const f=el.files[0];try{toast(T('Загружаю файл…'));const r=await uploadFile(f);SH.files=[...(SH.files||[]),{id:r.id,name:r.name,type:r.contentType}];drawFiles();toast(T('Файл прикреплён'))}catch(_){toast(T('Не удалось загрузить файл (PDF или фото до 20 МБ).'))}return}
  if(el.id==='photoIn'&&el.files[0]){try{const r=await uploadFile(el.files[0]);SH.photo=r.id;sheet.querySelector('#photoBox').innerHTML=thumb({photo:r.id},88)}catch(_){toast(T('Не удалось загрузить фото.'))}return}
});
document.addEventListener('dragover',e=>{const d=e.target.closest&&e.target.closest('#drop');if(d){e.preventDefault();d.classList.add('over')}});
document.addEventListener('dragleave',e=>{const d=e.target.closest&&e.target.closest('#drop');if(d)d.classList.remove('over')});
document.addEventListener('drop',e=>{const d=e.target.closest&&e.target.closest('#drop');if(!d)return;e.preventDefault();d.classList.remove('over');const f=e.dataTransfer.files[0];if(!f)return;
  if(SH.kind==='upload')recognize(f);else if(SH.kind==='bankImport')handleBankFile(f)});
document.addEventListener('submit',async e=>{
  const id=e.target.id;e.preventDefault();
  const g=x=>(document.getElementById(x)||{}).value?.trim()??'';
  if(id==='loginForm'){
    const btn=e.target.querySelector('button[type=submit]');btn.disabled=true;
    try{await api('POST','/api/login',{email:g('lg_email'),password:$('#lg_pass').value});await boot()}
    catch(err){renderLogin(err.code==='too_many_attempts'?T('Слишком много попыток. Подождите 15 минут.'):err.code==='offline'?T('Нет связи с сервером.'):T('Неверный e-mail или пароль.'))}
    return;
  }
  if(id==='coForm'){
    const rows=[...document.querySelectorAll('#rates .rrow')];
    const vr=rows.map(r=>({rate:parseFloat(r.querySelector('.rt-rate').value.replace(',','.')),label:r.querySelector('.rt-label').value.trim(),def:r.querySelector('input[type=radio]').checked})).filter(r=>!isNaN(r.rate)&&r.rate>=0&&r.rate<100);
    if(!vr.length)return toast(T('Нужна хотя бы одна ставка НДС.'));
    const defR=(vr.find(r=>r.def)||vr[0]).rate;
    const data={...S.company,name:g('co_name'),regCode:g('co_reg'),vatNo:g('co_vat'),address:g('co_addr'),iban:g('co_iban'),bank:g('co_bank'),email:g('co_email'),phone:g('co_phone'),web:g('co_web'),invoicePrefix:g('co_pre'),invoiceStart:+g('co_start')||1,dueDays:+g('co_due')||0,penalty:g('co_pen').replace(',','.'),vatRates:vr.map(({rate,label})=>({rate,label})),defaultRate:defR};
    delete data.id;delete data._v;
    try{document.activeElement&&document.activeElement.blur();await store.set('settings','company',data);toast(T('Настройки сохранены'));render()}catch(_){toast(T('Не удалось сохранить.'))}
    return;
  }
  if(id==='aiForm'){const key=g('ai_key');if(!key)return;try{await api('POST','/api/company/aikey',{key});S.companyInfo.aiReady=true;toast(T('Ключ сохранён'));render()}catch(_){toast(T('Не удалось сохранить.'))}return}
  if(id==='pwForm'){try{await api('POST','/api/me/password',{current:$('#pw_cur').value,next:$('#pw_new').value});toast(T('Пароль изменён'));$('#pw_cur').value='';$('#pw_new').value=''}catch(err){toast(err.code==='short_password'?T('Пароль должен быть не короче 8 символов.'):err.code==='wrong_password'?T('Текущий пароль неверный.'):T('Не удалось сохранить.'))}return}
  if(id==='userForm'){try{await api('POST','/api/company/users',{name:g('nu_name'),email:g('nu_email'),password:g('nu_pass'),lang:g('nu_lang')});toast(T('Пользователь добавлен'));loadUsers()}catch(err){toast(ERR_USER[err.code]?T(ERR_USER[err.code]):T('Не удалось сохранить.'))}return}
  if(id==='companyForm'){try{await api('POST','/api/admin/companies',{name:g('nc_name'),ownerName:g('nc_owner'),email:g('nc_email'),password:g('nc_pass'),lang:g('nc_lang')});toast(T('Фирма создана. Передайте владельцу адрес программы, e-mail и пароль.'));loadAdmin()}catch(err){toast(ERR_USER[err.code]?T(ERR_USER[err.code]):T('Не удалось сохранить.'))}return}
});
const ERR_USER={bad_email:'Проверьте e-mail.',short_password:'Пароль должен быть не короче 8 символов.',email_taken:'Этот e-mail уже используется.',bad_name:'Укажите название фирмы.'};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&sheet)closeSheet()});
function shiftMonth(n){const[y,m]=S.month.split('-').map(Number);const d=new Date(y,m-1+n,1);S.month=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');render()}
$('#mPrev').onclick=()=>shiftMonth(-1);$('#mNext').onclick=()=>shiftMonth(1);

if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
boot();
})();
