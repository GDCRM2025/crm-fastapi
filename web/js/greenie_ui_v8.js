(()=>{
  'use strict';

  const BUILD='20260806-LEAD-BRAND-ACTIONS';
  const $=(selector,root=document)=>root.querySelector(selector);
  const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
  const API_BASE=(()=>{
    const host=String(location.hostname||'').toLowerCase();
    return host==='localhost'||host==='127.0.0.1'?'':'/crm';
  })();
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));

  function authHeaders(extra={}){
    try{
      const gd=window.parent?.GD;
      if(gd?.authHeaders)return gd.authHeaders(extra);
    }catch(_){ }
    const token=
      localStorage.getItem('token')||localStorage.getItem('access_token')||
      localStorage.getItem('gd_token')||sessionStorage.getItem('token')||
      sessionStorage.getItem('access_token')||sessionStorage.getItem('gd_token')||'';
    return token?{...extra,Authorization:`Bearer ${token}`}:{...extra};
  }

  async function api(path,options={}){
    const method=String(options.method||'GET').toUpperCase();
    const headers=authHeaders({
      Accept:'application/json',
      ...(method!=='GET'&&!(options.body instanceof FormData)?{'Content-Type':'application/json'}:{}),
      ...(options.headers||{}),
    });
    const response=await fetch(API_BASE+path,{...options,headers});
    const text=await response.text();
    let payload={};
    try{payload=text?JSON.parse(text):{}}catch(_){payload={detail:text||response.statusText}}
    if(!response.ok)throw new Error(payload?.detail||payload?.error||`HTTP ${response.status}`);
    return payload||{};
  }

  function activeConversationId(){
    return Number($('#app')?.dataset?.conversationId||$('.item.active')?.dataset?.id||0);
  }

  function selectedLeadCard(){
    return $('.leadCard.active')||$('.leadCard');
  }

  function selectedLeadId(){
    return Number(selectedLeadCard()?.dataset?.lead||0);
  }

  function ensureStyles(){
    if($('#greenieUi8Style'))return;
    const style=document.createElement('style');
    style.id='greenieUi8Style';
    style.textContent=`
      .greenieHeaderActions{display:flex;gap:6px;align-items:center;margin-left:auto}
      .greenieHeaderIcon,.greenieMiniAction{width:34px;height:34px;border:1px solid #cbd5e1;background:#fff;border-radius:999px;display:inline-grid;place-items:center;padding:0;cursor:pointer;font-size:15px;box-shadow:none}
      .greenieHeaderIcon:hover,.greenieMiniAction:hover{background:#eff6ff;border-color:#93c5fd}
      .greenieActionsCompact{display:flex!important;gap:6px!important;flex-wrap:wrap!important;grid-template-columns:none!important}
      .greenieActionsCompact .greenieMiniAction{font-size:15px!important;font-weight:800!important;color:#0f172a!important}
      .greenieLeadDetails{margin-top:8px;border:1px solid #dbe5f0;border-radius:11px;overflow:hidden;background:#fff}
      .greenieLeadSummary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 10px;background:#f8fafc;font-size:11px;font-weight:950;color:#334155}
      .greenieLeadSummary::-webkit-details-marker{display:none}
      .greenieLeadSummary:after{content:'▾';font-size:12px;color:#64748b}
      .greenieLeadDetails:not([open]) .greenieLeadSummary:after{content:'▸'}
      .greenieLeadDropdownBody{padding:7px}
      .greenieLeadGroup{margin-top:7px}.greenieLeadGroup:first-child{margin-top:0}
      .greenieLeadGroupTitle{font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:#64748b;font-weight:950;padding:3px 2px}
      .greenieLeadToolbar{display:flex;justify-content:flex-end;margin-top:8px}
      .greenieLeadToolbar button{font-size:11px}
      .greenieClientCreate{width:100%;margin-top:3px;border-radius:10px!important;display:flex;align-items:center;justify-content:center;gap:7px}
      .greenieClientCreateIcon{font-size:17px;line-height:1}
      .greenieOverlay{position:fixed;inset:0;z-index:6000;background:rgba(15,23,42,.58);display:none}
      .greenieOverlay.show{display:block}
      .greenieOverlay iframe{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent}
      .greenieOverlayClose{position:absolute;right:18px;top:18px;z-index:5;border:1px solid #cbd5e1;background:#fff;color:#0f172a;border-radius:999px;padding:8px 13px;font-weight:900;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.18)}
      .greenieOverlayLoader{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:4;background:#fff;border:1px solid #dbe5f0;border-radius:14px;padding:14px 18px;font-weight:900;box-shadow:0 20px 60px rgba(0,0,0,.28)}
      .greenieDialogBackdrop{position:fixed;inset:0;z-index:6100;background:rgba(15,23,42,.58);display:none;place-items:center;padding:18px}
      .greenieDialogBackdrop.show{display:grid}
      .greenieDialog{width:min(760px,96vw);max-height:92vh;overflow:auto;background:#fff;border-radius:16px;border:1px solid #dbe5f0;box-shadow:0 24px 80px rgba(0,0,0,.32)}
      .greenieDialogHead{display:flex;align-items:center;justify-content:space-between;padding:13px 15px;border-bottom:1px solid #e2e8f0}
      .greenieDialogTitle{font-weight:950}.greenieDialogBody{padding:14px}.greenieDialogGrid{display:grid;grid-template-columns:1fr 1fr;gap:11px}
      .greenieDialogGrid label{display:grid;gap:5px;font-size:11px;font-weight:900;color:#475569}.greenieDialogGrid .span2{grid-column:1/-1}
      .greenieDialogGrid input,.greenieDialogGrid select,.greenieDialogGrid textarea{width:100%;border:1px solid #cbd5e1;border-radius:10px;padding:9px 10px;background:#fff;color:#0f172a}
      .greenieDialogGrid textarea{min-height:82px;resize:vertical}
      .greenieDialogFoot{display:flex;justify-content:flex-end;gap:8px;padding:12px 15px;border-top:1px solid #e2e8f0}
      .greenieDialogError{display:none;margin-bottom:10px;padding:9px 10px;border-radius:9px;background:#fee2e2;color:#991b1b;font-size:11px;font-weight:800}
      @media(max-width:700px){.greenieDialogGrid{grid-template-columns:1fr}.greenieDialogGrid .span2{grid-column:auto}}
    `;
    document.head.appendChild(style);
  }

  function ensureLeadOverlay(){
    if($('#greenieLeadOverlay8'))return;
    const overlay=document.createElement('div');
    overlay.id='greenieLeadOverlay8';
    overlay.className='greenieOverlay';
    overlay.innerHTML=`
      <button class="greenieOverlayClose" id="greenieLeadClose8" type="button">Cerrar</button>
      <div class="greenieOverlayLoader" id="greenieLeadLoader8">Cargando ficha…</div>
      <iframe id="greenieLeadFrame8" title="Ficha del lead"></iframe>`;
    document.body.appendChild(overlay);
    $('#greenieLeadClose8').onclick=closeLeadModal;
    window.addEventListener('message',event=>{
      if(event.origin!==location.origin)return;
      if(event.data?.type==='embed:close')closeLeadModal();
    });
  }

  function openLeadModal(){
    const leadId=selectedLeadId();
    if(!leadId){alert('Selecciona un lead primero.');return}
    ensureLeadOverlay();
    const overlay=$('#greenieLeadOverlay8');
    const frame=$('#greenieLeadFrame8');
    const loader=$('#greenieLeadLoader8');
    loader.style.display='block';
    overlay.classList.add('show');
    frame.src=`${API_BASE}/web/views/leads.html?embed=1&stale_ids=${leadId}&id_lead=${leadId}&v=${Date.now()}`;
    const started=Date.now();
    const timer=setInterval(()=>{
      if(!overlay.classList.contains('show')){clearInterval(timer);return}
      try{
        frame.contentWindow?.postMessage({type:'openLead',id:leadId},location.origin);
        const doc=frame.contentDocument;
        if(doc&&!doc.getElementById('greenieEmbed8Css')){
          const css=doc.createElement('style');
          css.id='greenieEmbed8Css';
          css.textContent='html,body{background:transparent!important}.swal2-container{z-index:99999!important}';
          doc.head.appendChild(css);
        }
        if(doc?.querySelector('.swal2-container.swal2-shown,.swal2-popup')){
          loader.style.display='none';
          clearInterval(timer);
          return;
        }
      }catch(_){ }
      if(Date.now()-started>25000){
        clearInterval(timer);
        loader.textContent='No se pudo cargar la ficha. Cierra y vuelve a intentarlo.';
      }
    },250);
  }

  function closeLeadModal(){
    const overlay=$('#greenieLeadOverlay8');
    if(!overlay)return;
    overlay.classList.remove('show');
    const frame=$('#greenieLeadFrame8');
    if(frame)frame.src='about:blank';
    const loader=$('#greenieLeadLoader8');
    if(loader){loader.textContent='Cargando ficha…';loader.style.display='block'}
    setTimeout(()=>$('#refresh')?.click(),80);
  }

  function ensureCreateDialog(){
    if($('#greenieCreateLeadDialog'))return;
    const backdrop=document.createElement('div');
    backdrop.id='greenieCreateLeadDialog';
    backdrop.className='greenieDialogBackdrop';
    backdrop.innerHTML=`
      <section class="greenieDialog">
        <header class="greenieDialogHead"><div class="greenieDialogTitle">Nuevo lead desde WhatsApp</div><button class="greenieHeaderIcon" id="greenieCreateClose" type="button">✕</button></header>
        <div class="greenieDialogBody">
          <div class="greenieDialogError" id="greenieCreateError"></div>
          <div class="greenieDialogGrid">
            <label>Cliente<input id="gclName" autocomplete="name"></label>
            <label>Teléfono<input id="gclPhone" readonly></label>
            <label>Marca del canal<input id="gclBrand" readonly></label>
            <label>Comuna<select id="gclCommune"></select></label>
            <label>Fecha del evento<input id="gclDate" type="date"></label>
            <label class="span2">Notas<textarea id="gclNotes" placeholder="Requerimiento inicial del cliente"></textarea></label>
          </div>
        </div>
        <footer class="greenieDialogFoot"><button class="btn" id="greenieCreateCancel" type="button">Cancelar</button><button class="btn primary" id="greenieCreateSave" type="button">Crear lead</button></footer>
      </section>`;
    document.body.appendChild(backdrop);
    $('#greenieCreateClose').onclick=closeCreateLead;
    $('#greenieCreateCancel').onclick=closeCreateLead;
    backdrop.addEventListener('click',event=>{if(event.target===backdrop)closeCreateLead()});
    $('#greenieCreateSave').onclick=saveCreateLead;
  }

  async function conversationIdentity(){
    const conversationId=activeConversationId();
    if(!conversationId)throw new Error('Selecciona una conversación primero.');
    const result=await api(`/gia/whatsapp/conversations/${conversationId}/messages?limit=1`);
    return result.conversation||{};
  }

  async function openCreateLead(){
    try{
      ensureCreateDialog();
      const error=$('#greenieCreateError');
      error.style.display='none';
      const [communesPayload,conversation]=await Promise.all([
        api('/gia/whatsapp/metadata/comunas'),
        conversationIdentity(),
      ]);
      const communes=communesPayload.items||[];
      const brandNames={CAMALEON:'Camaleón',DEL_SABOR:'Del Sabor',GOURMET:'Gourmet',EXPRESS:'Express'};
      $('#gclBrand').value=brandNames[String(conversation.brand_code||'').toUpperCase()]||conversation.brand_code||'Sin configurar';
      $('#gclCommune').innerHTML='<option value="">Seleccionar</option>'+communes.map(row=>`<option value="${esc(row.nombre||row.comuna||'')}">${esc(row.nombre||row.comuna||'')}</option>`).join('');
      $('#gclName').value=conversation.profile_name||'';
      $('#gclPhone').value=conversation.wa_id||'';
      $('#gclDate').min=new Date().toISOString().slice(0,10);
      $('#gclDate').value=new Date().toISOString().slice(0,10);
      $('#gclNotes').value='';
      $('#greenieCreateLeadDialog').classList.add('show');
      $('#gclName').focus();
    }catch(error){
      alert(error.message);
    }
  }

  function closeCreateLead(){
    $('#greenieCreateLeadDialog')?.classList.remove('show');
  }

  async function saveCreateLead(){
    const save=$('#greenieCreateSave');
    const error=$('#greenieCreateError');
    const conversationId=activeConversationId();
    const payload={
      nombre:String($('#gclName').value||'').trim(),
      fecha_evento:$('#gclDate').value||null,
      comuna:String($('#gclCommune').value||'').trim()||null,
    };
    const notes=String($('#gclNotes').value||'').trim();
    if(!payload.nombre||!payload.fecha_evento){
      error.textContent='Cliente y fecha del evento son obligatorios.';
      error.style.display='block';
      return;
    }
    if(payload.fecha_evento&&payload.fecha_evento<new Date().toISOString().slice(0,10)){
      error.textContent='La fecha del evento no puede estar vencida.';
      error.style.display='block';
      return;
    }
    save.disabled=true;
    error.style.display='none';
    try{
      const result=await api(`/gia/whatsapp/conversations/${conversationId}/create-lead`,{method:'POST',body:JSON.stringify(payload)});
      const leadId=Number(result.id_lead||0);
      if(notes&&leadId)await api(`/gia/whatsapp/conversations/${conversationId}/leads/${leadId}/followup`,{method:'POST',body:JSON.stringify({text:notes,kind:'NOTE',title:'Nota inicial desde WhatsApp'})}).catch(()=>null);
      closeCreateLead();
      setTimeout(()=>$('#refresh')?.click(),100);
    }catch(err){
      error.textContent=err.message;
      error.style.display='block';
    }finally{
      save.disabled=false;
    }
  }

  function installClientCreate(){
    $('#greenieNewLeadHeader')?.remove();
    const card=$$('#side .card').find(node=>String(node.textContent||'').toLowerCase().includes('cliente whatsapp'));
    if(!card||$('.greenieClientCreate',card))return;
    const button=document.createElement('button');
    button.className='btn primary greenieClientCreate';
    button.type='button';
    button.title='Crear lead con la marca del canal de WhatsApp';
    button.innerHTML='<span class="greenieClientCreateIcon">＋</span><span>Nuevo lead de esta marca</span>';
    button.onclick=openCreateLead;
    card.appendChild(button);
  }

  function compactActions(){
    const actionCard=$$('#side .card').find(card=>String($('.sectionTitle',card)?.textContent||'').toLowerCase().includes('acciones del lead'));
    if(!actionCard)return;
    const grid=$('.actionsGrid',actionCard);
    if(!grid||grid.dataset.compactActions==='1')return;
    grid.dataset.compactActions='1';
    grid.classList.add('greenieActionsCompact');
    $$('button',grid).forEach(button=>{
      const text=String(button.textContent||'').trim();
      const lower=text.toLowerCase();
      let icon='•';
      if(lower.includes('llam'))icon='📞';
      else if(lower.includes('ficha')||lower.includes('ver lead')||lower==='ver')icon='👁';
      else if(lower.includes('crear')&&lower.includes('cot'))icon='🧾';
      else if(lower.includes('enviar')&&lower.includes('cot'))icon='📤';
      else if(lower.includes('pdf'))icon='📄';
      else if(lower.includes('agenda')||lower.includes('evento'))icon='📅';
      button.title=text;
      button.setAttribute('aria-label',text);
      button.textContent=icon;
      button.classList.add('greenieMiniAction');
      if(lower.includes('ficha')||lower.includes('ver lead')){
        const clone=button.cloneNode(true);
        clone.id='greenieOpenLead8';
        button.replaceWith(clone);
        clone.onclick=event=>{
          event.preventDefault();
          event.stopImmediatePropagation();
          openLeadModal();
        };
      }
    });
  }

  function dropdownLeads(){
    const card=$$('#side .card').find(node=>String($('.sectionTitle',node)?.textContent||'').toLowerCase().includes('oportunidades del cliente'));
    if(!card||card.dataset.dropdown8==='1')return;
    const leadCards=$$('.leadCard',card);
    const title=$('.sectionTitle',card);
    const details=document.createElement('details');
    details.className='greenieLeadDetails';
    details.open=true;
    const summary=document.createElement('summary');
    summary.className='greenieLeadSummary';
    summary.innerHTML=`<span>Leads del cliente</span><span>${leadCards.length}</span>`;
    const body=document.createElement('div');
    body.className='greenieLeadDropdownBody';
    const groups=new Map();
    leadCards.forEach(lead=>{
      const status=String($('.leadMeta',lead)?.textContent||'Sin estado').split('·')[0].trim()||'Sin estado';
      if(!groups.has(status))groups.set(status,[]);
      groups.get(status).push(lead);
    });
    [...groups.entries()].forEach(([status,rows])=>{
      const group=document.createElement('div');
      group.className='greenieLeadGroup';
      group.innerHTML=`<div class="greenieLeadGroupTitle">${esc(status)} · ${rows.length}</div>`;
      rows.forEach(row=>group.appendChild(row));
      body.appendChild(group);
    });
    details.append(summary,body);
    if(title)title.insertAdjacentElement('afterend',details);else card.prepend(details);
    $$('button',card).forEach(button=>{
      const text=String(button.textContent||'').toLowerCase();
      if(text.includes('crear lead')||text.includes('nuevo lead'))button.style.display='none';
    });
    card.dataset.dropdown8='1';
  }

  function enhanceSide(){
    installClientCreate();
    compactActions();
    dropdownLeads();
  }

  ensureStyles();
  ensureLeadOverlay();
  ensureCreateDialog();
  const side=$('#side');
  if(side){
    let timer=null;
    new MutationObserver(()=>{
      clearTimeout(timer);
      timer=setTimeout(enhanceSide,25);
    }).observe(side,{subtree:true,childList:true});
  }
  setTimeout(enhanceSide,120);
  console.info('[Greenie UI]',BUILD);
})();
