(()=>{
  'use strict';

  const BUILD='20260812-WABA-CONTEXT-V1';
  const $=(selector,root=document)=>root.querySelector(selector);
  const API_BASE=String(location.pathname||'').startsWith('/crm/')?'/crm':'';
  let targetBubble=null,longPressTimer=null;

  function authHeaders(extra={}){
    try{if(window.parent?.GD?.authHeaders)return window.parent.GD.authHeaders(extra)}catch(_){ }
    const token=localStorage.getItem('token')||sessionStorage.getItem('token')||'';
    return token?{...extra,Authorization:`Bearer ${token}`}:{...extra};
  }

  async function api(path,options={}){
    const method=String(options.method||'GET').toUpperCase();
    const response=await fetch(API_BASE+path,{...options,headers:authHeaders({
      Accept:'application/json',
      ...(method!=='GET'?{'Content-Type':'application/json'}:{}),
      ...(options.headers||{}),
    })});
    const raw=await response.text();
    let payload={};
    try{payload=raw?JSON.parse(raw):{}}catch(_){payload={detail:raw||response.statusText}}
    if(!response.ok)throw new Error(payload.detail||payload.error||`HTTP ${response.status}`);
    return payload;
  }

  function conversationId(){return Number($('#app')?.dataset?.conversationId||0)}
  function messageId(){return Number(targetBubble?.dataset?.id||targetBubble?.dataset?.messageId||0)}
  function messageText(){
    if(!targetBubble)return'';
    const clone=targetBubble.cloneNode(true);
    clone.querySelectorAll('.actions,.messageActions,.msgMeta,.reactions,.messageReactions').forEach(node=>node.remove());
    return String(clone.innerText||'').replace(/\n{3,}/g,'\n\n').trim();
  }

  function ensureUi(){
    if($('#greenieContextMenu'))return;
    const style=document.createElement('style');
    style.id='greenieContextStyle';
    style.textContent=`
      .greenieContextMenu{position:fixed;z-index:9000;width:260px;max-height:min(620px,86vh);overflow:auto;padding:6px;background:#fff;color:#111827;border:1px solid #d7dee8;border-radius:12px;box-shadow:0 18px 55px rgba(15,23,42,.28);display:none}
      .greenieContextMenu.show{display:block}.greenieContextGroup{padding:4px 0;border-top:1px solid #edf1f5}.greenieContextGroup:first-child{border-top:0}
      .greenieContextItem{width:100%;border:0;background:transparent;color:inherit;border-radius:8px;padding:9px 10px;display:flex;align-items:center;gap:10px;text-align:left;font:600 12px/1.2 system-ui;cursor:pointer}
      .greenieContextItem:hover,.greenieContextItem:focus-visible{background:#eef6f3;outline:none}.greenieContextItem[disabled]{opacity:.45;cursor:not-allowed;background:transparent}
      .greenieContextIcon{width:20px;text-align:center;font-size:15px}.greenieContextHint{margin-left:auto;color:#64748b;font-size:9px;font-weight:700}
      .greenieContextReact{display:grid;grid-template-columns:repeat(6,1fr);gap:2px;padding:5px}.greenieContextReact button{border:0;background:transparent;border-radius:8px;padding:7px 2px;font-size:18px;cursor:pointer}.greenieContextReact button:hover{background:#edf2f7}
      .bubble.greenieSelectedMessage{outline:2px solid #2563eb;outline-offset:2px}
      @media(max-width:720px){.greenieContextMenu{width:min(300px,calc(100vw - 20px))}}
    `;
    document.head.appendChild(style);
    const menu=document.createElement('div');
    menu.id='greenieContextMenu';menu.className='greenieContextMenu';menu.setAttribute('role','menu');
    menu.innerHTML=`
      <div class="greenieContextGroup">
        <button class="greenieContextItem" data-gctx="reply"><span class="greenieContextIcon">↩</span>Responder</button>
        <div class="greenieContextReact" aria-label="Reaccionar">${['👍','❤️','😂','😮','😢','🙏'].map(emoji=>`<button type="button" data-gctx-react="${emoji}" aria-label="Reaccionar ${emoji}">${emoji}</button>`).join('')}</div>
        <button class="greenieContextItem" data-gctx="forward"><span class="greenieContextIcon">➜</span>Preparar reenvío<span class="greenieContextHint">no envía</span></button>
        <button class="greenieContextItem" data-gctx="copy"><span class="greenieContextIcon">⧉</span>Copiar</button>
        <button class="greenieContextItem" data-gctx="info"><span class="greenieContextIcon">ⓘ</span>Información</button>
        <button class="greenieContextItem" data-gctx="select"><span class="greenieContextIcon">☑</span>Seleccionar mensaje</button>
      </div>
      <div class="greenieContextGroup">
        <button class="greenieContextItem" data-gctx="create-lead"><span class="greenieContextIcon">＋</span>Crear lead desde mensaje</button>
        <button class="greenieContextItem" data-gctx="link-lead"><span class="greenieContextIcon">🔗</span>Vincular a lead existente</button>
        <button class="greenieContextItem" data-gctx="assign"><span class="greenieContextIcon">👤</span>Asignar ejecutivo</button>
        <button class="greenieContextItem" data-gctx="followup"><span class="greenieContextIcon">✓</span>Crear seguimiento</button>
        <button class="greenieContextItem" data-gctx="pending"><span class="greenieContextIcon">◷</span>Marcar como pendiente</button>
        <button class="greenieContextItem" data-gctx="quote"><span class="greenieContextIcon">🧾</span>Crear cotización</button>
        <button class="greenieContextItem" data-gctx="crm360"><span class="greenieContextIcon">◉</span>Abrir Comercial 360</button>
        <button class="greenieContextItem" data-gctx="history"><span class="greenieContextIcon">☷</span>Ver historial del cliente</button>
        <button class="greenieContextItem" data-gctx="close"><span class="greenieContextIcon">✓</span>Cerrar conversación</button>
      </div>
      <div class="greenieContextGroup">
        <button class="greenieContextItem" disabled title="Meta no permite borrar remotamente un mensaje ya enviado"><span class="greenieContextIcon">⌫</span>Eliminar<span class="greenieContextHint">no disponible</span></button>
      </div>`;
    document.body.appendChild(menu);
    menu.addEventListener('click',event=>{
      const reaction=event.target.closest('[data-gctx-react]');
      if(reaction){runReaction(reaction.dataset.gctxReact);return}
      const item=event.target.closest('[data-gctx]');
      if(item&&!item.disabled)runAction(item.dataset.gctx);
    });
  }

  function hide(){const menu=$('#greenieContextMenu');if(menu)menu.classList.remove('show')}
  function show(bubble,x,y){
    ensureUi();targetBubble=bubble;
    const menu=$('#greenieContextMenu');menu.classList.add('show');
    const rect=menu.getBoundingClientRect();
    menu.style.left=`${Math.max(8,Math.min(x,innerWidth-rect.width-8))}px`;
    menu.style.top=`${Math.max(8,Math.min(y,innerHeight-rect.height-8))}px`;
  }

  async function notify(icon,title,text=''){
    try{const Swal=await window.ensureGreenieSweetAlert?.();if(Swal)return Swal.fire({toast:true,position:'top-end',icon,title,text,timer:1500,showConfirmButton:false})}catch(_){ }
    if(icon==='error')alert(text||title);
  }
  async function commercial(){const id=conversationId();if(!id)throw new Error('Selecciona una conversación.');return api(`/gia/whatsapp/conversations/${id}/commercial-360`)}
  function openCommercial(){const button=$('#commercialToggle');if(button&&button.getAttribute('aria-expanded')!=='true')button.click()}
  async function waitFor(selector,timeout=1800){const start=Date.now();while(Date.now()-start<timeout){const node=$(selector);if(node)return node;await new Promise(resolve=>setTimeout(resolve,60))}return null}

  async function runReaction(emoji){
    const id=conversationId(),mid=messageId();hide();
    if(!id||!mid)return;
    try{await api(`/gia/whatsapp/conversations/${id}/send-reaction`,{method:'POST',body:JSON.stringify({target_message_id:mid,emoji})});$('#refresh')?.click();await notify('success','Reacción enviada')}
    catch(error){await notify('error','No se pudo reaccionar',error.message)}
  }

  async function runAction(action){
    const bubble=targetBubble,text=messageText(),mid=messageId(),id=conversationId();hide();
    try{
      if(action==='reply'){bubble?.querySelector('[data-reply]')?.click();return}
      if(action==='copy'){await navigator.clipboard.writeText(text);await notify('success','Mensaje copiado');return}
      if(action==='forward'){const field=$('#message');if(!field)throw new Error('No encontramos el redactor.');field.value=text;field.focus();field.setSelectionRange(field.value.length,field.value.length);await notify('info','Reenvío preparado','Revisa el contenido y pulsa Enviar cuando corresponda.');return}
      if(action==='info'){
        const direction=bubble?.classList.contains('out')?'Saliente':'Entrante';
        const stamp=String($('.msgMeta',bubble)?.textContent||'Sin fecha visible').trim();
        const Swal=await window.ensureGreenieSweetAlert?.();
        if(Swal)await Swal.fire({title:'Información del mensaje',html:`<div style="text-align:left;display:grid;gap:8px"><div><b>ID local:</b> ${mid||'—'}</div><div><b>Dirección:</b> ${direction}</div><div><b>Estado/fecha:</b> ${String(stamp).replace(/[&<>]/g,'')}</div></div>`,confirmButtonText:'Cerrar'});
        else alert(`Mensaje ${mid||'—'} · ${direction} · ${stamp}`);return;
      }
      if(action==='select'){bubble?.classList.toggle('greenieSelectedMessage');return}
      if(action==='crm360'){openCommercial();return}
      if(action==='create-lead'){openCommercial();const button=await waitFor('.greenieClientCreate');if(!button)throw new Error('La conversación ya tiene contexto comercial; abre Comercial 360 para revisar sus leads.');button.click();return}
      if(action==='link-lead'){openCommercial();const data=await commercial();if(!(data.leads||[]).length)throw new Error('No existen leads del mismo teléfono para vincular. Puedes crear uno desde este menú.');await notify('info','Selecciona el lead','En Comercial 360, pulsa la oportunidad que deseas vincular.');return}
      if(action==='quote'){openCommercial();await waitFor('#createQuoteBtn');const button=$('#createQuoteBtn');if(!button)throw new Error('Primero crea o vincula un lead.');button.click();return}
      if(action==='history'){openCommercial();await waitFor('#openLeadBtn');const button=$('#openLeadBtn');if(!button)throw new Error('Primero crea o vincula un lead.');button.click();return}
      if(action==='followup'||action==='pending'){
        openCommercial();await waitFor('#followText');const field=$('#followText');
        if(!field)throw new Error('Primero crea o vincula un lead.');
        field.value=action==='pending'?'Pendiente de respuesta del cliente.':`Seguimiento originado desde mensaje #${mid}.`;
        field.focus();if(action==='pending')$('#followBtn')?.click();return;
      }
      if(action==='assign'){
        const data=await commercial(),rows=data.executives||[];
        if(!rows.length)throw new Error('Este canal usa asignación directa por marca o no tiene ejecutivos habilitados.');
        const options=Object.fromEntries(rows.map((row,index)=>[String(index),row.name||row.nombre||row.username||`Ejecutivo ${index+1}`]));
        const Swal=await window.ensureGreenieSweetAlert?.();if(!Swal)throw new Error('No se pudo abrir el selector de ejecutivo.');
        const answer=await Swal.fire({title:'Asignar ejecutivo',input:'select',inputOptions:options,showCancelButton:true,confirmButtonText:'Asignar',cancelButtonText:'Cancelar'});if(!answer.isConfirmed)return;
        const row=rows[Number(answer.value)||0],leadId=Number(data.selected_lead_id||0)||null;
        await api(`/gia/whatsapp/conversations/${id}/assign-executive`,{method:'POST',body:JSON.stringify({executive_id:row.id||row.id_usuario||null,executive_name:row.name||row.nombre||row.username,lead_id:leadId})});
        $('#refresh')?.click();await notify('success','Ejecutivo asignado');return;
      }
      if(action==='close'){$('#closeBtn')?.click();return}
    }catch(error){await notify('error','No se pudo completar la acción',error.message)}
  }

  document.addEventListener('contextmenu',event=>{const bubble=event.target.closest('#messages .bubble');if(!bubble)return;event.preventDefault();show(bubble,event.clientX,event.clientY)});
  document.addEventListener('pointerdown',event=>{const bubble=event.target.closest('#messages .bubble');if(!bubble||event.pointerType==='mouse')return;longPressTimer=setTimeout(()=>show(bubble,event.clientX,event.clientY),550)});
  ['pointerup','pointercancel','pointermove'].forEach(name=>document.addEventListener(name,()=>{clearTimeout(longPressTimer);longPressTimer=null},{passive:true}));
  document.addEventListener('click',event=>{if(!event.target.closest('#greenieContextMenu'))hide()});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hide()});
  addEventListener('resize',hide);addEventListener('scroll',hide,true);
  ensureUi();
  console.info('[Greenie context]',BUILD);
})();
