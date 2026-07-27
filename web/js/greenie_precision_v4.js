(()=>{
  'use strict';

  const BUILD='20260727-PRECISION4-NO-AI';
  const $=(selector,root=document)=>root.querySelector(selector);
  const API_BASE=(()=>{
    const host=String(location.hostname||'').toLowerCase();
    return host==='localhost'||host==='127.0.0.1'?'':'/crm';
  })();

  function stabilizeInnerHTML(element){
    if(!element||element.dataset.greenieStable==='1')return;
    const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML');
    if(!descriptor?.get||!descriptor?.set)return;
    Object.defineProperty(element,'innerHTML',{
      configurable:true,
      get(){return descriptor.get.call(this)},
      set(value){
        const next=String(value??'');
        const current=descriptor.get.call(this);
        if(current===next)return;
        descriptor.set.call(this,next);
      },
    });
    element.dataset.greenieStable='1';
  }

  stabilizeInnerHTML($('#list'));
  stabilizeInnerHTML($('#side'));

  const style=document.createElement('style');
  style.id='greeniePrecision4Style';
  style.textContent=`
    .greenieLeadModalOnly{position:fixed;inset:0;z-index:2600;background:rgba(15,23,42,.52);display:none}
    .greenieLeadModalOnly.show{display:block}
    .greenieLeadModalOnly iframe{width:100%;height:100%;border:0;background:transparent}
  `;
  document.head.appendChild(style);

  const overlay=document.createElement('div');
  overlay.id='greenieLeadModalOnly';
  overlay.className='greenieLeadModalOnly';
  overlay.innerHTML='<iframe id="greenieLeadModalFrame" title="Ficha del lead"></iframe>';
  document.body.appendChild(overlay);

  const frame=$('#greenieLeadModalFrame');

  function selectedLeadId(){
    return Number($('.leadCard.active')?.dataset?.lead||$('.leadCard')?.dataset?.lead||0);
  }

  function hideLeadPageChrome(){
    try{
      const doc=frame.contentDocument;
      if(!doc||doc.getElementById('greenieLeadModalOnlyCss'))return;
      const css=doc.createElement('style');
      css.id='greenieLeadModalOnlyCss';
      css.textContent=`
        html,body{background:transparent!important}
        body>*:not(.swal2-container):not(script):not(style){display:none!important}
        .swal2-container{background:transparent!important}
      `;
      doc.head.appendChild(css);
    }catch(_){ }
  }

  frame.addEventListener('load',()=>{
    hideLeadPageChrome();
    setTimeout(hideLeadPageChrome,150);
    setTimeout(hideLeadPageChrome,500);
  });

  function openLeadModalOnly(){
    const leadId=selectedLeadId();
    if(!leadId)return;
    frame.src=`${API_BASE}/web/views/leads.html?embed=1&lead_id=${leadId}&v=${Date.now()}`;
    overlay.classList.add('show');
  }

  function closeLeadModalOnly(){
    overlay.classList.remove('show');
    frame.src='about:blank';
    $('#refresh')?.click();
  }

  overlay.addEventListener('click',event=>{
    if(event.target===overlay)closeLeadModalOnly();
  });

  window.addEventListener('message',event=>{
    if(event.origin!==location.origin)return;
    if(event.data?.type==='embed:close')closeLeadModalOnly();
  });

  function installLeadButton(){
    const old=$('#openLeadBtn');
    if(!old||old.dataset.precision4==='1')return;
    const button=old.cloneNode(true);
    button.id='greenieOpenLeadModalOnly';
    button.dataset.precision4='1';
    button.textContent='👁 Ver ficha del lead';
    old.replaceWith(button);
    button.onclick=openLeadModalOnly;
  }

  function markCallingPending(){
    const button=$('#callBtn');
    if(!button||button.dataset.callingPrepared==='1')return;
    button.dataset.callingPrepared='1';
    button.textContent='📞 Llamada WhatsApp';
    button.title='Se habilitará mediante WhatsApp Business Calling API en los números reales';
    button.onclick=event=>{
      event.preventDefault();
      event.stopImmediatePropagation();
      alert('La mensajería ya está operativa. La llamada de voz requiere habilitar WhatsApp Business Calling API en el número real de la marca. No se abrirá wa.me como sustituto.');
    };
  }

  function enhance(){
    installLeadButton();
    markCallingPending();
  }

  const side=$('#side');
  if(side){
    let timer=null;
    new MutationObserver(()=>{
      clearTimeout(timer);
      timer=setTimeout(enhance,25);
    }).observe(side,{subtree:true,childList:true});
  }

  setTimeout(enhance,100);
  console.info('[Greenie precision]',BUILD);
})();
