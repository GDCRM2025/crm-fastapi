// Dock estilo macOS: centrado, magnificación, autohide/pin, submenús, día/noche y logout.
(()=>{
  const MENU={
    leads:[{label:'Ver leads',href:'/web/leads.html'},{label:'Crear lead',href:'/web/leads.html#create'}],
    settings:[
      {label:'Usuarios',href:'/web/settings/usuarios.html'},
      {label:'Marcas',href:'/web/settings/marcas.html'},
      {label:'Estados',href:'/web/settings/estados.html'},
      {label:'Comunas',href:'/web/settings/comunas.html'},
      {label:'Categorías',href:'/web/settings/categorias.html'},
      {label:'Segmentación',href:'/web/settings/segmentacion.html'},
      {label:'Productos',href:'/web/settings/productos.html'},
    ],
  };
  const $=s=>document.querySelector(s);
  const el=(t,c,h)=>{const x=document.createElement(t); if(c)x.className=c; if(h!=null)x.innerHTML=h; return x;}

  // Contenedores dock + área sensible
  const wrap=el('div'); wrap.id='dock-wrap';
  const hit=el('div'); hit.id='dock-hit';
  const dock=el('nav','dock autohide'); dock.setAttribute('aria-label','Dock');
  document.body.appendChild(hit); document.body.appendChild(wrap); wrap.appendChild(dock);

  // Items (ordenados)
  add('📋','Leads',()=>showSub('leads'));
  add('⚙️','Settings',()=>showSub('settings'));
  add('🌓','Día/Noche',toggleTheme);
  add('📌','Pin',togglePin);
  add('⎋','Salir',logout);

  function add(icon,title,fn){
    const b=el('button','dock-item',`<span>${icon}</span>`);
    b.title=title;
    b.onclick=fn;
    b.onmouseenter=fn;
    dock.appendChild(b);
  }

  function showSub(key){
    hideSub();
    const sub=el('div','dock-sub'); sub.id='dock-sub';
    if(key==='leads'){ sub.appendChild(buildCol('Leads',MENU.leads)); }
    if(key==='settings'){ sub.appendChild(buildCol('Settings',MENU.settings)); }

    const utils=el('div','col'); utils.appendChild(el('h4','', 'Acciones'));
    utils.appendChild(btn('Día / Noche',toggleTheme));
    utils.appendChild(btn('Fijar/Auto-ocultar',togglePin));
    utils.appendChild(btn('Salir',logout));
    sub.appendChild(utils);

    document.body.appendChild(sub);
    centerUnderDock(sub);
    setTimeout(()=>document.addEventListener('click',onDocClick));
  }
  function buildCol(title,items){
    const col=el('div','col'); col.appendChild(el('h4','',title));
    items.forEach(m=>col.appendChild(btn(m.label,()=>open(m.href))));
    return col;
  }
  function btn(label,fn){ const b=el('button','menu-btn',label); b.onclick=fn; return b; }
  function centerUnderDock(sub){ const r=dock.getBoundingClientRect(); sub.style.left=(r.left+(r.width-sub.offsetWidth)/2)+'px'; sub.style.top=(r.bottom+8)+'px'; }
  function hideSub(){ const s=$('#dock-sub'); if(s){ s.remove(); document.removeEventListener('click',onDocClick);} }
  function onDocClick(e){ const s=$('#dock-sub'); if(s && !s.contains(e.target)) hideSub(); }

  // Abrir href en iframe; si no existe, navegar
  function open(href){
    hideSub();
    const f=$('#viewFrame'); const card=$('#homeCard'); const vw=$('#viewWrap');
    if(f && vw){
      f.src=href;
      card?.classList.add('hidden');
      vw?.classList.remove('hidden');
      sessionStorage.setItem('last_view', href);
    }else{
      location.href=href;
    }
  }

  // Pin / Autohide
  function togglePin(){
    dock.classList.toggle('autohide');
    localStorage.setItem('dock_pin', dock.classList.contains('autohide')?'0':'1');
  }

  // Logout con confirm + nombre
  async function logout(){
    const who = (document.getElementById('who')?.textContent || 'usuario').trim();
    const ok = confirm(`Cerrar sesión de ${who}?`);
    if(!ok) return;
    try{ localStorage.removeItem('token'); sessionStorage.removeItem('token'); }catch(_){}
    location.href='/web/login.html';
  }

  // Día / Noche
  function toggleTheme(){
    const html=document.documentElement;
    const cur=html.getAttribute('data-theme')||'night';
    const next=(cur==='night')?'day':'night';
    html.setAttribute('data-theme',next);
    localStorage.setItem('theme',next);
  }

  // Autohide hover
  const savedPin=localStorage.getItem('dock_pin');
  if(savedPin==='1') dock.classList.remove('autohide');
  hit.onmouseenter=()=>dock.classList.add('show');
  dock.onmouseleave=()=>{dock.classList.remove('show'); hideSub();}

  // Restaurar última vista en iframe si existe
  const last = sessionStorage.getItem('last_view');
  if(last && $('#viewFrame') && $('#viewWrap')){
    $('#homeCard')?.classList.add('hidden');
    $('#viewWrap')?.classList.remove('hidden');
    $('#viewFrame').src = last;
  }
})();
