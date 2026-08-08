(()=>{
  'use strict';
  const BUILD='20260727-PRECISION3';
  const $=(s,r=document)=>r.querySelector(s);
  const $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const API_BASE=(()=>{const h=String(location.hostname||'').toLowerCase();return h==='localhost'||h==='127.0.0.1'?'':'/crm'})();
  const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

  function authHeaders(extra={}){
    try{return window.parent?.GD?.authHeaders?window.parent.GD.authHeaders(extra):extra}catch(_){ }
    const t=localStorage.getItem('token')||localStorage.getItem('gd_token')||sessionStorage.getItem('token')||sessionStorage.getItem('gd_token')||'';
    return t?{...extra,Authorization:`Bearer ${t}`}:{...extra};
  }
  async function api(path,opt={}){
    const method=String(opt.method||'GET').toUpperCase();
    const r=await fetch(API_BASE+path,{...opt,headers:authHeaders({...(method!=='GET'?{'Content-Type':'application/json'}:{}),...(opt.headers||{})})});
    let j={};try{j=await r.json()}catch(_){j={detail:await r.text().catch(()=>r.statusText)}}
    if(!r.ok)throw new Error(j.detail||j.error||`HTTP ${r.status}`);
    return j;
  }

  function stabilizeInnerHTML(element){
    if(!element||element.dataset.greenieStable==='1')return;
    const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML');
    if(!descriptor?.get||!descriptor?.set)return;
    Object.defineProperty(element,'innerHTML',{
      configurable:true,
      get(){return descriptor.get.call(this)},
      set(value){
        const next=String(value??'');
        const previous=descriptor.get.call(this);
        if(previous===next)return;
        descriptor.set.call(this,next);
      }
    });
    element.dataset.greenieStable='1';
  }

  stabilizeInnerHTML($('#list'));
  stabilizeInnerHTML($('#side'));

  const style=document.createElement('style');
  style.textContent=`
    .greenieLeadOnly{position:fixed;inset:0;z-index:2500;background:rgba(15,23,42,.48);display:none}
    .greenieLeadOnly.show{display:block}
    .greenieLeadOnly iframe{width:100%;height:100%;border:0;background:transparent}
    .greenieAiCard{border-color:#c4b5fd;background:linear-gradient(180deg,#faf7ff,#fff)}
    .greenieAiHead{display:flex;align-items:center;justify-content:space-between;gap:8px}
    .greenieAiResult{display:grid;gap:8px;margin-top:8px}
    .greenieAiBox{border:1px solid #ddd6fe;background:#fff;border-radius:10px;padding:8px;font-size:11px;line-height:1.4}
    .greenieAiLabel{font-size:9px;text-transform:uppercase;font-weight:950;color:#6d28d9;margin-bottom:3px}
    .greenieAiReply{white-space:pre-wrap}
    .greenieAiButtons{display:flex;gap:6px;flex-wrap:wrap}
    .greenieAiButtons button{font-size:10px}
  `;
  document.head.appendChild(style);

  const overlay=document.createElement('div');
  overlay.className='greenieLeadOnly';
  overlay.id='greenieLeadOnly';
  overlay.innerHTML='<iframe id="greenieLeadFrame" title="Modal lead"></iframe>';
  document.body.appendChild(overlay);

  function selectedLeadId(){return Number($('.leadCard.active')?.dataset?.lead||$('.leadCard')?.dataset?.lead||0)}
  function activeConversationId(){return Number($('.item.active')?.dataset?.id||0)}

  function openLeadOnly(){
    const leadId=selectedLeadId();
    if(!leadId)return;
    const frame=$('#greenieLeadFrame');
    frame.src=`${API_BASE}/web/views/leads.html?embed=1&lead_id=${leadId}&v=${Date.now()}`;
    overlay.classList.add('show');
  }
  function closeLeadOnly(){overlay.classList.remove('show');$('#greenieLeadFrame').src='about:blank';$('#refresh')?.click()}
  overlay.addEventListener('click',e=>{if(e.target===overlay)closeLeadOnly()});
  window.addEventListener('message',e=>{if(e.origin===location.origin&&e.data?.type==='embed:close')closeLeadOnly()});

  function installLeadButton(){
    const old=$('#openLeadBtn');
    if(!old||old.dataset.precision==='1')return;
    const button=old.cloneNode(true);
    button.id='greenieOpenLeadOnly';
    button.dataset.precision='1';
    button.textContent='👁 Ver ficha del lead';
    old.replaceWith(button);
    button.onclick=openLeadOnly;
  }

  async function runAi(){
    const conversationId=activeConversationId();
    if(!conversationId)return;
    const result=$('#greenieAiResult');
    const button=$('#greenieAiBtn');
    button.disabled=true;
    result.innerHTML='<div class="hint">Greenie está analizando conversación, lead y cotizaciones…</div>';
    try{
      const data=await api(`/gia/whatsapp/conversations/${conversationId}/ai-assist`,{method:'POST',body:'{}'});
      const a=data.analysis||{};
      const missing=(a.missing_information||[]).map(x=>`<li>${esc(x)}</li>`).join('');
      const risks=(a.commercial_risks||[]).map(x=>`<li>${esc(x)}</li>`).join('');
      result.innerHTML=`
        <div class="greenieAiBox"><div class="greenieAiLabel">Intención · urgencia ${esc(a.urgency||'')}</div>${esc(a.intent||'')}</div>
        <div class="greenieAiBox"><div class="greenieAiLabel">Resumen</div>${esc(a.summary||'')}</div>
        <div class="greenieAiBox"><div class="greenieAiLabel">Acción recomendada</div>${esc(a.recommended_action||'')}</div>
        ${missing?`<div class="greenieAiBox"><div class="greenieAiLabel">Información faltante</div><ul>${missing}</ul></div>`:''}
        ${risks?`<div class="greenieAiBox"><div class="greenieAiLabel">Riesgos comerciales</div><ul>${risks}</ul></div>`:''}
        <div class="greenieAiBox"><div class="greenieAiLabel">Respuesta sugerida</div><div class="greenieAiReply" id="greenieAiReply">${esc(a.suggested_reply||'')}</div></div>
        <div class="greenieAiButtons"><button class="btn primary" id="greenieAiUse" type="button">Usar respuesta</button><button class="btn" id="greenieAiCopy" type="button">Copiar</button></div>`;
      $('#greenieAiUse').onclick=()=>{const text=$('#greenieAiReply')?.textContent||'';const composer=$('#message');if(composer){composer.value=text;composer.focus()}};
      $('#greenieAiCopy').onclick=async()=>{const text=$('#greenieAiReply')?.textContent||'';await navigator.clipboard.writeText(text)};
    }catch(error){
      result.innerHTML=`<div class="err">${esc(error.message)}</div>`;
    }finally{button.disabled=false}
  }

  function installAi(){
    const side=$('#side');
    if(!side||!$('.leadCard',side)||$('#greenieAiCard'))return;
    const card=document.createElement('div');
    card.className='card greenieAiCard';
    card.id='greenieAiCard';
    card.innerHTML=`<div class="greenieAiHead"><div><div class="sectionTitle">✨ Greenie IA</div><div class="hint">Copiloto comercial. No envía mensajes automáticamente.</div></div><button class="btn" id="greenieAiBtn" type="button">Analizar</button></div><div class="greenieAiResult" id="greenieAiResult"></div>`;
    const action=$$('.card',side).find(x=>String($('.sectionTitle',x)?.textContent||'').includes('Acciones del lead'));
    if(action)action.before(card);else side.appendChild(card);
    $('#greenieAiBtn').onclick=runAi;
  }

  function enhance(){installLeadButton();installAi()}
  const side=$('#side');
  if(side){let timer;new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(enhance,20)}).observe(side,{subtree:true,childList:true})}
  setTimeout(enhance,100);
  console.info('[Greenie precision]',BUILD);
})();
