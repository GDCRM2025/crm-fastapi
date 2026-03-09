const $ = (q, c=document)=>c.querySelector(q);
const TOKEN = localStorage.getItem('token') || sessionStorage.getItem('token');
const AUTH  = TOKEN ? {'Authorization':'Bearer '+TOKEN} : {};
const sendThemeToChild = (iframe)=>{
  const theme = document.documentElement.classList.contains('light') ? 'light' : 'dark';
  iframe?.contentWindow?.postMessage({cmd:'theme', theme}, '*');
};

/* estado inicial tema + menú */
(function initShell(){
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.documentElement.classList.toggle('light', savedTheme==='light');
  document.body.classList.toggle('collapsed', (localStorage.getItem('menu_collapsed')||'0')==='1');
})();

/* Menú (acordeón) */
const MENU = [
  {icon:'📋', title:'Leads', items:[
    {label:'Ver leads', href:'/web/views/leads.html'},
  ]},
  {icon:'🧾', title:'Cotizador', items:[{label:'Generar cotización', href:'/web/views/leads.html#cotizador'}]},
  {icon:'📈', title:'Reportes', items:[
    {label:'Funnel de venta', href:'/web/views/leads.html#funnel'},
    {label:'% de cierre', href:'/web/views/leads.html#cierre'},
    {label:'Venta del mes', href:'/web/views/leads.html#mes'},
  ]},
  {icon:'🏗️', title:'Operaciones', items:[
    {label:'Mice and Place', href:'/web/views/leads.html#mice'},
    {label:'Inventario maquinaria', href:'/web/views/leads.html#inventario'},
    {sep:true},
    {label:'Kardex — Ingreso MP', href:'/web/views/leads.html#kardex-in'},
    {label:'Kardex — Egreso MP', href:'/web/views/leads.html#kardex-out'},
    {label:'Kardex — Costeo', href:'/web/views/leads.html#kardex-cost'},
    {sep:true},
    {label:'Recetas', href:'/web/views/leads.html#recetas'},
    {sep:true},
    {label:'Productos', href:'/web/settings/productos.html'},
    {label:'Proveedores', href:'/web/views/leads.html#proveedores'},
  ]},
  {icon:'🧰', title:'Tools', items:[
    {label:'Correo', href:'/web/views/leads.html#correo'},
    {label:'Instagram', href:'/web/views/leads.html#ig'},
    {label:'WhatsApp', href:'/web/views/leads.html#wa'},
    {sep:true},
    {label:'Calendario', href:'/web/views/leads.html#calendar'},
    {label:'Calculadora', href:'/web/views/leads.html#calc'},
    {label:'Clima', href:'/web/views/leads.html#clima'},
  ]},
  {icon:'🚚', title:'Conductores', items:[
    {label:'Logística', href:'/web/views/leads.html#logistica'},
    {label:'App/GPS', href:'/web/views/leads.html#gps'},
  ]},
  {icon:'💰', title:'Finanzas', items:[
    {label:'Registro de gastos', href:'/web/views/leads.html#gastos'},
    {label:'Registro de ingresos', href:'/web/views/leads.html#ingresos'},
    {label:'P&L', href:'/web/views/leads.html#pl'},
    {label:'Plan de cuentas', href:'/web/views/leads.html#pdc'},
  ]},
  {icon:'⚙️', title:'Settings', items:[
    {label:'Usuarios', href:'/web/settings/usuarios.html'},
    {label:'Comunas', href:'/web/settings/comunas.html'},
    {label:'Estados', href:'/web/settings/estados.html'},
    {label:'Segmentación', href:'/web/settings/segmentacion.html'},
    {label:'Marcas', href:'/web/settings/marcas.html'},
    {label:'Productos', href:'/web/settings/productos.html'},
    {label:'Categorías', href:'/web/settings/categorias.html'},
  ]}
];

function buildAccordion(){
  const acc = $('#acc'); acc.innerHTML='';
  MENU.forEach(group=>{
    const det = document.createElement('details'); det.className='acc'; det.open = true;
    const sum = document.createElement('summary');
    sum.innerHTML = `<span class="icon">${group.icon}</span> <b>${group.title}</b>`;
    det.appendChild(sum);
    const body = document.createElement('div'); body.className='acc-body';
    group.items.forEach(it=>{
      if(it.sep){ const hr=document.createElement('div'); hr.style.cssText='height:1px;background:var(--sep);margin:6px 8px'; body.appendChild(hr); return; }
      const a = document.createElement('a'); a.href='#'; a.innerHTML=`<span class="icon">•</span> <span>${it.label}</span>`;
      a.onclick=(e)=>{ e.preventDefault(); openView(it.href); };
      body.appendChild(a);
    });
    det.appendChild(body); acc.appendChild(det);
  });
}

function openView(href){
  $('#homeCard').style.display='none';
  $('#viewWrap').style.display='block';
  const iframe = $('#viewFrame');
  iframe.src = href;
  iframe.onload = ()=> sendThemeToChild(iframe);
  sessionStorage.setItem('last_view', href);
}

$('#btnMenu').addEventListener('click', ()=>{
  document.body.classList.toggle('collapsed');
  localStorage.setItem('menu_collapsed', document.body.classList.contains('collapsed')?'1':'0');
});
$('#goHome').addEventListener('click', ()=>{
  $('#viewWrap').style.display='none';
  $('#homeCard').style.display='block';
});
$('#toggleTheme').addEventListener('click', ()=>{
  const light = !document.documentElement.classList.contains('light');
  document.documentElement.classList.toggle('light', light);
  localStorage.setItem('theme', light?'light':'dark');
  sendThemeToChild($('#viewFrame'));
});
$('#logout').addEventListener('click', async ()=>{
  const who = $('#who').textContent||'usuario';
  const res = await Swal.fire({title:`Cerrar sesión de ${who}?`, icon:'question', showCancelButton:true, confirmButtonText:'Sí, salir'});
  if(res.isConfirmed){ localStorage.removeItem('token'); sessionStorage.removeItem('token'); location.href='/web/login.html'; }
});

(function init(){
  buildAccordion();
  const last = sessionStorage.getItem('last_view'); if(last) openView(last);
  // nombre arriba
  fetch('/me', {headers:AUTH}).then(r=>r.ok?r.json():{}).then(u=>{$('#who').textContent = u?.nombre || '';}).catch(()=>{});
})();
window.addEventListener('message', (e)=>{
  if(e.data?.cmd==='open-create-lead'){ openView('/web/views/leads.html#open-create'); }
});
