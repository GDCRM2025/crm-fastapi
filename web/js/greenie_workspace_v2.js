(()=>{
  'use strict';

  const BUILD='20260806-VIDEO-READY';
  const API_BASE=(()=>{
    const host=String(location.hostname||'').toLowerCase();
    return host==='localhost'||host==='127.0.0.1'?'':'/crm';
  })();
  const $=(selector,root=document)=>root.querySelector(selector);
  const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));

  function authHeaders(extra={}){
    try{
      const gd=window.parent?.GD;
      if(gd?.authHeaders)return gd.authHeaders(extra);
    }catch(_){ }
    const token=
      localStorage.getItem('token')||localStorage.getItem('gd_token')||
      sessionStorage.getItem('token')||sessionStorage.getItem('gd_token')||'';
    return token?{...extra,Authorization:`Bearer ${token}`}:{...extra};
  }

  async function api(path,options={}){
    const method=String(options.method||'GET').toUpperCase();
    const headers=authHeaders({
      ...(method!=='GET'?{'Content-Type':'application/json'}:{}),
      ...(options.headers||{}),
    });
    const response=await fetch(API_BASE+path,{...options,headers});
    let payload=null;
    try{payload=await response.json()}catch(_){payload={detail:await response.text().catch(()=>response.statusText)}}
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

  function selectedLeadState(){
    const card=selectedLeadCard();
    if(!card)return'';
    const meta=$('.leadMeta',card)?.textContent||'';
    return String(meta.split('·')[0]||'').trim().toUpperCase();
  }

  function currentPhone(){
    const sub=$('#chatSub')?.textContent||'';
    const match=sub.match(/\+?\d[\d\s-]{7,}/);
    return String(match?.[0]||'').replace(/\D/g,'');
  }

  function ensureSweetAlert(){
    if(window.Swal)return Promise.resolve(window.Swal);
    if(window.__greenieSwalPromise)return window.__greenieSwalPromise;
    window.__greenieSwalPromise=new Promise((resolve,reject)=>{
      const script=document.createElement('script');
      script.src=`${API_BASE}/web/vendor/sweetalert2-11.22.2.all.min.js`;
      script.onload=()=>resolve(window.Swal);
      script.onerror=()=>{
        const fallback=document.createElement('script');
        fallback.src='https://cdn.jsdelivr.net/npm/sweetalert2@11';
        fallback.onload=()=>resolve(window.Swal);
        fallback.onerror=()=>reject(new Error('No se pudo cargar SweetAlert2'));
        document.head.appendChild(fallback);
      };
      document.head.appendChild(script);
    });
    return window.__greenieSwalPromise;
  }
  window.ensureGreenieSweetAlert=ensureSweetAlert;

  function ensureWorkspace(){
    if($('#greenieWorkspace'))return;
    const style=document.createElement('style');
    style.id='greenieWorkspaceStyle';
    style.textContent=`
      .leadStatusGroup{border:1px solid #dbe5f0;border-radius:12px;margin-top:8px;overflow:hidden}
      .leadStatusHead{display:flex;align-items:center;justify-content:space-between;padding:7px 9px;background:#f1f5f9;font-size:10px;font-weight:950;text-transform:uppercase;color:#334155;letter-spacing:.04em}
      .leadStatusCount{background:#fff;border:1px solid #cbd5e1;border-radius:999px;padding:1px 7px}
      .leadStatusBody{padding:0 7px 7px}.leadStatusBody .leadCard{margin-top:7px}
      .greenieQuick{border:1px solid #cbd5e1;background:#f8fafc;border-radius:999px;padding:6px 9px;font-weight:850;cursor:pointer;font-size:10px}
      .greenieQuick:hover{background:#eaf3ff;border-color:#93c5fd}
      .greenieWorkspace{position:fixed;inset:0;background:rgba(15,23,42,.58);z-index:1000;display:none;padding:18px}
      .greenieWorkspace.show{display:grid;place-items:center}
      .greenieWorkspaceShell{width:min(1280px,98vw);height:min(900px,96vh);background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 28px 80px rgba(0,0,0,.35);display:flex;flex-direction:column}
      .greenieWorkspaceHead{height:52px;display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-bottom:1px solid #dbe5f0;background:#f8fafc}
      .greenieWorkspaceTitle{font-weight:950}.greenieWorkspaceClose{border:1px solid #cbd5e1;background:#fff;border-radius:999px;padding:7px 12px;font-weight:900;cursor:pointer}
      .greenieWorkspaceFrame{border:0;width:100%;height:100%;flex:1;background:#eef3f8}
      .greenieEventButton{margin-top:6px;width:100%}
    `;
    document.head.appendChild(style);

    const modal=document.createElement('div');
    modal.id='greenieWorkspace';
    modal.className='greenieWorkspace';
    modal.innerHTML=`
      <section class="greenieWorkspaceShell">
        <header class="greenieWorkspaceHead">
          <div class="greenieWorkspaceTitle" id="greenieWorkspaceTitle">Greenie</div>
          <button class="greenieWorkspaceClose" id="greenieWorkspaceClose" type="button">Cerrar</button>
        </header>
        <iframe class="greenieWorkspaceFrame" id="greenieWorkspaceFrame" title="Workspace Greenie"></iframe>
      </section>`;
    document.body.appendChild(modal);
    $('#greenieWorkspaceClose').onclick=closeWorkspace;
    modal.addEventListener('click',event=>{if(event.target===modal)closeWorkspace()});
  }

  function openWorkspace(title,url){
    ensureWorkspace();
    $('#greenieWorkspaceTitle').textContent=title;
    $('#greenieWorkspaceFrame').src=url;
    $('#greenieWorkspace').classList.add('show');
  }

  function closeWorkspace(){
    const modal=$('#greenieWorkspace');
    if(!modal)return;
    modal.classList.remove('show');
    const frame=$('#greenieWorkspaceFrame');
    if(frame)frame.src='about:blank';
  }

  function refreshGreenie(){
    $('#refresh')?.click();
  }

  function stateRank(name){
    const value=String(name||'').toUpperCase();
    if(value.includes('NUEVO'))return 10;
    if(value.includes('ATEND'))return 20;
    if(value.includes('CONTACT'))return 30;
    if(value.includes('COTIZ'))return 40;
    if(value.includes('NEGOCI'))return 50;
    if(value.includes('CONFIRM'))return 60;
    if(value.includes('DECLIN'))return 90;
    return 70;
  }

  function groupLeadsByStatus(){
    const section=$$('#side .card').find(card=>
      String($('.sectionTitle',card)?.textContent||'').includes('Oportunidades del cliente')
    );
    if(!section||section.dataset.grouped==='1')return;
    const cards=$$('.leadCard',section);
    if(cards.length<2){section.dataset.grouped='1';return}

    const groups=new Map();
    for(const card of cards){
      const meta=String($('.leadMeta',card)?.textContent||'');
      const status=String(meta.split('·')[0]||'Sin estado').trim()||'Sin estado';
      if(!groups.has(status))groups.set(status,[]);
      groups.get(status).push(card);
    }
    const title=$('.sectionTitle',section);
    section.innerHTML='';
    if(title)section.appendChild(title);
    [...groups.entries()]
      .sort((a,b)=>stateRank(a[0])-stateRank(b[0])||a[0].localeCompare(b[0]))
      .forEach(([status,rows])=>{
        const group=document.createElement('div');
        group.className='leadStatusGroup';
        group.innerHTML=`<div class="leadStatusHead"><span>${esc(status)}</span><span class="leadStatusCount">${rows.length}</span></div><div class="leadStatusBody"></div>`;
        const body=$('.leadStatusBody',group);
        rows.forEach(row=>body.appendChild(row));
        section.appendChild(group);
      });
    section.dataset.grouped='1';
  }

  function enhanceActions(){
    const actionCard=$$('#side .card').find(card=>
      String($('.sectionTitle',card)?.textContent||'').includes('Acciones del lead')
    );
    if(!actionCard)return;

    actionCard.dataset.professionalActions='1';
    const call=$('#callBtn',actionCard);
    if(call)call.title='Abre WhatsApp para iniciar una llamada con el cliente';
    const openLead=$('#openLeadBtn',actionCard);
    if(openLead)openLead.title='Edita y completa la ficha del lead sin salir de WhatsApp';
    const createQuote=$('#createQuoteBtn',actionCard);
    if(createQuote)createQuote.title='Crea una cotización sin salir de WhatsApp';

    const state=selectedLeadState();
    if(state.includes('CONFIRM')&&!$('#openEventBtn',actionCard)){
      const button=document.createElement('button');
      button.id='openEventBtn';
      button.type='button';
      button.className='btn greenieEventButton';
      button.textContent='📅 Abrir evento confirmado';
      actionCard.appendChild(button);
    }
  }

  function enhanceEvents(){
    $$('#side .eventRow a').forEach(link=>{
      link.textContent='Abrir evento';
      link.target='_blank';
      link.rel='noopener noreferrer';
    });
  }

  function enhanceSide(){
    groupLeadsByStatus();
    enhanceActions();
    enhanceEvents();
  }

  async function registerFollowup(kind,title,defaultText){
    const conversationId=activeConversationId();
    const leadId=selectedLeadId();
    if(!conversationId||!leadId)return;
    const Swal=await ensureSweetAlert();
    const result=await Swal.fire({
      title,
      input:'textarea',
      inputValue:defaultText,
      inputPlaceholder:'Detalle adicional (opcional)',
      showCancelButton:true,
      confirmButtonText:'Registrar',
      cancelButtonText:'Cancelar',
    });
    if(!result.isConfirmed)return;
    const text=String(result.value||defaultText||'').trim();
    if(!text)return;
    await api(`/gia/whatsapp/conversations/${conversationId}/leads/${leadId}/followup`,{
      method:'POST',
      body:JSON.stringify({kind,title,text}),
    });
    await Swal.fire({icon:'success',title:'Seguimiento registrado',timer:850,showConfirmButton:false});
    refreshGreenie();
  }

  async function noAnswerFollowup(){
    const conversationId=activeConversationId();
    const leadId=selectedLeadId();
    if(!conversationId||!leadId)return;
    const Swal=await ensureSweetAlert();
    const result=await Swal.fire({
      title:'Cliente no contesta',
      html:`<div style="display:grid;gap:10px;text-align:left"><label>Canal</label><select id="gf_no_channel" class="swal2-select"><option value="CALL">📞 Llamada</option><option value="WSP">🟢 WhatsApp</option><option value="EMAIL">✉️ Email</option></select><label>Detalle</label><textarea id="gf_no_text" class="swal2-textarea" placeholder="Detalle opcional"></textarea></div>`,
      showCancelButton:true,
      confirmButtonText:'Registrar',
      preConfirm:()=>({
        kind:String(document.getElementById('gf_no_channel')?.value||'CALL'),
        detail:String(document.getElementById('gf_no_text')?.value||'').trim(),
      }),
    });
    if(!result.isConfirmed)return;
    const kind=result.value.kind;
    const title=kind==='WSP'?'Cliente no responde WhatsApp':kind==='EMAIL'?'Cliente no responde correo':'Cliente no contesta llamada';
    const base='Cliente no contesta. Se solicita respuesta para avanzar.';
    const text=result.value.detail?`${base}\n${result.value.detail}`:base;
    await api(`/gia/whatsapp/conversations/${conversationId}/leads/${leadId}/followup`,{
      method:'POST',body:JSON.stringify({kind,title,text}),
    });
    await Swal.fire({icon:'success',title:'Seguimiento registrado',timer:850,showConfirmButton:false});
    refreshGreenie();
  }

  async function presetFollowup(){
    const state=selectedLeadState();
    const presets=state.includes('COTIZ')?[
      ['Cotización enviada','WSP','Cotización enviada por WhatsApp','Se comparte cotización al cliente vía WhatsApp.'],
      ['Cliente revisando propuesta','NOTE','Cliente revisando propuesta','Cliente indica encontrarse revisando internamente la cotización/propuesta.'],
      ['A la espera de respuesta','NOTE','A la espera de respuesta','Se mantiene seguimiento de cotización enviada pendiente de respuesta del cliente.'],
      ['Solicita ajuste','NOTE','Cliente solicita ajuste','Cliente solicita revisión de precio, alcance o condiciones.'],
    ]:state.includes('CONFIRM')?[
      ['Cliente acepta cotización','NOTE','Cliente acepta cotización','Cliente acepta condiciones comerciales de la cotización enviada.'],
      ['Pendiente pago anticipado','NOTE','Pendiente pago anticipado','Cliente confirma avance, quedando pendiente pago inicial o anticipo.'],
      ['Fecha confirmada','NOTE','Fecha de inicio confirmada','Se confirma fecha del evento con el cliente.'],
      ['Confirmado por WhatsApp','WSP','Confirmado por WhatsApp','Cliente confirma avance comercial vía WhatsApp.'],
    ]:[
      ['Contactado por WhatsApp','WSP','Contactado por WhatsApp','Se realiza contacto inicial con el cliente vía WhatsApp.'],
      ['Contactado por llamada','CALL','Contactado por llamada','Se realiza contacto telefónico con el cliente.'],
      ['Cliente solicita información','NOTE','Cliente solicita información','Cliente solicita antecedentes adicionales para evaluar la propuesta comercial.'],
      ['Seguimiento activo','NOTE','Seguimiento activo','Se mantiene gestión comercial activa a la espera de interacción o definición del cliente.'],
    ];
    const Swal=await ensureSweetAlert();
    const options=Object.fromEntries(presets.map((row,index)=>[String(index),row[0]]));
    const selected=await Swal.fire({title:'Seguimiento rápido',input:'select',inputOptions:options,showCancelButton:true,confirmButtonText:'Continuar'});
    if(!selected.isConfirmed)return;
    const row=presets[Number(selected.value)||0];
    await registerFollowup(row[1],row[2],row[3]);
  }

  async function updateStatus(select){
    const leadId=selectedLeadId();
    if(!leadId||!select.value)return;
    const label=String(select.options[select.selectedIndex]?.textContent||'').toUpperCase();
    if(label.includes('CONFIRM')){
      const Swal=await ensureSweetAlert();
      await Swal.fire({icon:'info',title:'Confirmación con agenda',text:'Para confirmar un lead debes usar el modal completo, seleccionar cotización y completar la agenda.'});
      openLeadModal();
      return;
    }
    try{
      await api(`/leads/${leadId}`,{method:'PUT',body:JSON.stringify({id_estado:Number(select.value)})});
      const Swal=await ensureSweetAlert();
      await Swal.fire({icon:'success',title:'Estado actualizado',timer:750,showConfirmButton:false});
      refreshGreenie();
    }catch(error){
      const Swal=await ensureSweetAlert();
      await Swal.fire({icon:'error',title:'No se pudo actualizar',text:error.message});
    }
  }

  function openLeadModal(){
    const leadId=selectedLeadId();
    if(!leadId)return;
    openWorkspace(
      `Lead #${leadId}`,
      `${API_BASE}/web/views/leads.html?embed=1&lead_id=${leadId}&v=${Date.now()}`
    );
  }

  function openQuoteModal(){
    const leadId=selectedLeadId();
    const conversationId=activeConversationId();
    if(!leadId||!conversationId)return;
    openWorkspace(
      `Cotización · Lead #${leadId}`,
      `${API_BASE}/web/views/cotizador.html?embed=1&source=greenie&lead_id=${leadId}&conversation_id=${conversationId}&v=${Date.now()}`
    );
  }

  function openWhatsAppCall(){
    const phone=currentPhone();
    if(!phone)return;
    window.open(`https://wa.me/${phone}`,'_blank','noopener,noreferrer');
  }

  function openConfirmedEvent(){
    const eventLink=$('#side .eventRow a[href]');
    if(eventLink?.href){window.open(eventLink.href,'_blank','noopener,noreferrer');return}
    openLeadModal();
  }

  document.addEventListener('click',event=>{
    const sticker=event.target.closest('.stickerBtn');
    if(sticker){$('#tray')?.classList.remove('show');return}

    const openLead=event.target.closest('#openLeadBtn');
    if(openLead){event.preventDefault();event.stopImmediatePropagation();openLeadModal();return}

    const quote=event.target.closest('#createQuoteBtn');
    if(quote){event.preventDefault();event.stopImmediatePropagation();openQuoteModal();return}

    const call=event.target.closest('#callBtn');
    if(call){event.preventDefault();event.stopImmediatePropagation();openWhatsAppCall();return}

    const eventButton=event.target.closest('#openEventBtn');
    if(eventButton){event.preventDefault();event.stopImmediatePropagation();openConfirmedEvent();return}

    const quick=event.target.closest('[data-gf]');
    if(!quick)return;
    event.preventDefault();event.stopImmediatePropagation();
    const kind=quick.dataset.gf;
    if(kind==='NO'){noAnswerFollowup().catch(error=>alert(error.message));return}
    if(kind==='PICK'){presetFollowup().catch(error=>alert(error.message));return}
    const definitions={
      WSP:['Cliente contactado por WhatsApp','Cliente contactado por WhatsApp.'],
      CALL:['Cliente contactado por llamada','Cliente contactado por llamada.'],
      EMAIL:['Cliente contactado por correo','Cliente contactado por correo.'],
    };
    const data=definitions[kind]||['Seguimiento','Seguimiento comercial.'];
    registerFollowup(kind,data[0],data[1]).catch(error=>alert(error.message));
  },true);

  document.addEventListener('change',event=>{
    const select=event.target.closest('#statusSelect');
    if(!select)return;
    event.preventDefault();event.stopImmediatePropagation();
    updateStatus(select);
  },true);

  window.addEventListener('message',event=>{
    if(event.origin!==location.origin)return;
    const type=String(event.data?.type||'');
    if(type==='embed:close'||type==='greenie:close-workspace'||type==='greenie:quote-sent'){
      closeWorkspace();
      setTimeout(refreshGreenie,120);
    }
  });

  async function openFromQuery(){
    const params=new URLSearchParams(location.search);
    const conversationId=Number(params.get('conversation_id')||0);
    const phone=String(params.get('phone')||'').replace(/\D/g,'');
    if(!conversationId&&!phone)return;

    for(let attempt=0;attempt<20;attempt++){
      if(conversationId){
        const item=$(`.item[data-id="${conversationId}"]`);
        if(item){item.click();return}
      }
      if(phone){
        try{
          const data=await api(`/gia/whatsapp/conversations?status=all&q=${encodeURIComponent(phone)}`);
          const match=(data.items||[]).find(row=>String(row.wa_id||'').replace(/\D/g,'').endsWith(phone.slice(-9)));
          if(match){
            const all=$('[data-status="all"]');
            if(all&&!all.classList.contains('active'))all.click();
            await new Promise(resolve=>setTimeout(resolve,180));
            const item=$(`.item[data-id="${match.id}"]`);
            if(item){item.click();return}
          }
        }catch(_){ }
      }
      await new Promise(resolve=>setTimeout(resolve,250));
    }
  }

  ensureWorkspace();
  const side=$('#side');
  if(side){
    let timer=null;
    new MutationObserver(()=>{
      clearTimeout(timer);
      timer=setTimeout(enhanceSide,30);
    }).observe(side,{childList:true,subtree:true});
  }
  setTimeout(enhanceSide,100);
  setTimeout(openFromQuery,250);
  console.info('[Greenie workspace]',BUILD);
})();
