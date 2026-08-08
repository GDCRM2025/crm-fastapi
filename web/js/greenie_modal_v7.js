(()=>{
  'use strict';

  const BUILD='20260727-MODAL7';
  const $=(selector,root=document)=>root.querySelector(selector);
  const API_BASE=(()=>{
    const host=String(location.hostname||'').toLowerCase();
    return host==='localhost'||host==='127.0.0.1'?'':'/crm';
  })();

  const style=document.createElement('style');
  style.id='greenieModalV7Style';
  style.textContent=`
    .greenieLeadOverlayV7{position:fixed;inset:0;z-index:5000;display:none;background:rgba(15,23,42,.58)}
    .greenieLeadOverlayV7.show{display:block}
    .greenieLeadFrameV7{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent}
    .greenieLeadLoaderV7{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:2;background:#fff;color:#0f172a;border:1px solid #dbe5f0;border-radius:14px;padding:14px 18px;box-shadow:0 20px 60px rgba(0,0,0,.28);font-weight:900}
    .greenieLeadCloseV7{position:absolute;right:18px;top:18px;z-index:4;border:1px solid #cbd5e1;background:#fff;color:#0f172a;border-radius:999px;padding:8px 13px;font-weight:900;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.18)}
    .greenieLeadErrorV7{display:none;position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:3;max-width:520px;background:#fff;color:#991b1b;border:1px solid #fecaca;border-radius:14px;padding:16px;box-shadow:0 20px 60px rgba(0,0,0,.28);font-weight:800}
  `;
  document.head.appendChild(style);

  const overlay=document.createElement('div');
  overlay.id='greenieLeadOverlayV7';
  overlay.className='greenieLeadOverlayV7';
  overlay.innerHTML=`
    <button type="button" class="greenieLeadCloseV7" id="greenieLeadCloseV7">Cerrar</button>
    <div class="greenieLeadLoaderV7" id="greenieLeadLoaderV7">Cargando ficha del lead…</div>
    <div class="greenieLeadErrorV7" id="greenieLeadErrorV7"></div>
    <iframe class="greenieLeadFrameV7" id="greenieLeadFrameV7" title="Ficha del lead"></iframe>`;
  document.body.appendChild(overlay);

  const frame=$('#greenieLeadFrameV7');
  const loader=$('#greenieLeadLoaderV7');
  const errorBox=$('#greenieLeadErrorV7');
  let openLeadId=0;
  let detectTimer=null;

  function selectedLeadId(){
    return Number($('.leadCard.active')?.dataset?.lead||$('.leadCard')?.dataset?.lead||0);
  }

  function injectEmbedCss(){
    try{
      const doc=frame.contentDocument;
      if(!doc)return false;
      if(!doc.getElementById('greenieModalV7EmbedCss')){
        const css=doc.createElement('style');
        css.id='greenieModalV7EmbedCss';
        css.textContent=`
          html,body{background:transparent!important}
          body.embed .page{display:none!important}
          .swal2-container{z-index:99999!important}
        `;
        doc.head.appendChild(css);
      }
      return true;
    }catch(_){
      return false;
    }
  }

  function requestOpenInsideFrame(){
    if(!openLeadId)return;
    try{
      frame.contentWindow?.postMessage({type:'openLead',id:openLeadId},location.origin);
    }catch(_){ }
  }

  function detectModal(){
    clearInterval(detectTimer);
    const started=Date.now();
    detectTimer=setInterval(()=>{
      injectEmbedCss();
      requestOpenInsideFrame();
      try{
        const modal=frame.contentDocument?.querySelector('.swal2-container.swal2-shown,.swal2-popup');
        if(modal){
          loader.style.display='none';
          errorBox.style.display='none';
          clearInterval(detectTimer);
          detectTimer=null;
          return;
        }
      }catch(_){ }
      if(Date.now()-started>8000){
        clearInterval(detectTimer);
        detectTimer=null;
        loader.style.display='none';
        errorBox.textContent='La ficha no alcanzó a abrir. Revisa que el lead exista y que la sesión del CRM siga activa.';
        errorBox.style.display='block';
      }
    },180);
  }

  function openLeadModal(){
    const leadId=selectedLeadId();
    if(!leadId){
      alert('Selecciona un lead primero.');
      return;
    }
    openLeadId=leadId;
    loader.style.display='block';
    errorBox.style.display='none';
    overlay.classList.add('show');
    frame.src=`${API_BASE}/web/views/leads.html?embed=1&id_lead=${leadId}&v=${Date.now()}`;
    detectModal();
  }

  function closeLeadModal(){
    clearInterval(detectTimer);
    detectTimer=null;
    openLeadId=0;
    overlay.classList.remove('show');
    frame.src='about:blank';
    try{$('#refresh')?.click()}catch(_){ }
  }

  frame.addEventListener('load',()=>{
    injectEmbedCss();
    setTimeout(requestOpenInsideFrame,80);
    setTimeout(injectEmbedCss,150);
    setTimeout(requestOpenInsideFrame,300);
  });

  $('#greenieLeadCloseV7').onclick=closeLeadModal;
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin)return;
    if(event.data?.type==='embed:close')closeLeadModal();
  });

  function installButton(){
    const current=$('#openLeadBtn')||$('#greenieOpenLeadOnly')||$('#greenieOpenLeadModalOnly');
    if(!current||current.id==='greenieOpenLeadV7')return;
    const button=current.cloneNode(true);
    button.id='greenieOpenLeadV7';
    button.textContent='👁 Ver ficha del lead';
    current.replaceWith(button);
    button.onclick=event=>{
      event.preventDefault();
      event.stopImmediatePropagation();
      openLeadModal();
    };
  }

  const side=$('#side');
  if(side){
    let timer=null;
    new MutationObserver(()=>{
      clearTimeout(timer);
      timer=setTimeout(installButton,20);
    }).observe(side,{childList:true,subtree:true});
  }
  setTimeout(installButton,100);
  console.info('[Greenie modal]',BUILD);
})();
