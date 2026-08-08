from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/main.py"
TOOLS = ROOT / "web/views/tools.html"
PANEL = ROOT / "web/js/panel.js"
GREENIE = ROOT / "web/views/tools_whatsapp_greenie.html"
CANONICAL = ROOT / "web/views/tools_whatsapp.html"

if not GREENIE.exists():
    raise SystemExit("ERROR: falta web/views/tools_whatsapp_greenie.html")

# 1) Router comercial.
main = MAIN.read_text(encoding="utf-8")
anchor = 'include_router_safe(app, "backend.routers.whatsapp_webhook")\n'
line = 'include_router_safe(app, "backend.routers.whatsapp_commercial")\n'
if line not in main:
    if anchor not in main:
        raise SystemExit("ERROR: no se encontro ancla de routers en main.py")
    main = main.replace(anchor, anchor + line, 1)
main = main.replace("microphone=(), camera=()", "microphone=(self), camera=()")
MAIN.write_text(main, encoding="utf-8")
print("ACTUALIZADO: backend/main.py")

# 2) El wrapper siempre abre la UI Greenie física, nunca la vista antigua.
tools = TOOLS.read_text(encoding="utf-8")
tools = tools.replace("Correo (GIA)", "Correo (Greenie)")
tools = tools.replace("Instagram (GIA)", "Instagram (Greenie)")
tools = re.sub(
    r'\{ id:"whatsapp", label:"WhatsApp", ico:"💬", url:"[^"]+" \}',
    '{ id:"whatsapp", label:"WhatsApp Greenie", ico:"💬", url:"/web/views/tools_whatsapp_greenie.html?v=20260727-crm360-1" }',
    tools,
)
tools = tools.replace(
    '<iframe id="toolFrame" title="Tools"></iframe>',
    '<iframe id="toolFrame" title="Tools" allow="microphone"></iframe>',
)
TOOLS.write_text(tools, encoding="utf-8")
print("ACTUALIZADO: web/views/tools.html")

# 3) El menú principal rompe cache del wrapper.
panel = PANEL.read_text(encoding="utf-8")
panel = re.sub(
    r'/web/views/tools(?:_greenie)?\.html\?v=[^"#]*#whatsapp',
    '/web/views/tools.html?v=20260727-crm360-1#whatsapp',
    panel,
)
PANEL.write_text(panel, encoding="utf-8")
print("ACTUALIZADO: web/js/panel.js")

# 4) Ampliar la vista Greenie existente sin reescribir la mensajería estable.
view = GREENIE.read_text(encoding="utf-8")
view = view.replace("WhatsApp GIA", "WhatsApp Greenie")

css = r'''
    /* GREENIE_CRM360_V1 */
    .crm360{display:grid;gap:10px}.crm360Summary{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.crm360Stat{background:#f8fafc;border:1px solid var(--bd);border-radius:10px;padding:8px}.crm360Stat b{display:block;font-size:17px}.crm360Tabs{display:flex;gap:4px;overflow:auto}.crm360Tab{border:1px solid var(--bd);background:#fff;border-radius:999px;padding:6px 9px;font-weight:800;cursor:pointer;font-size:11px}.crm360Tab.active{background:#dbeafe;border-color:#93c5fd}.crm360Pane{display:none}.crm360Pane.active{display:grid;gap:8px}.crmLead{border:1px solid var(--bd);border-radius:11px;padding:9px;background:#fff;cursor:pointer}.crmLead.active{border-color:#2563eb;background:#eff6ff}.crmLeadHead{display:flex;justify-content:space-between;gap:8px;font-weight:900}.crmMeta{font-size:11px;color:var(--muted);margin-top:4px}.crmActions{display:grid;grid-template-columns:1fr 1fr;gap:6px}.crmAction{border:1px solid var(--bd);border-radius:10px;background:#fff;padding:8px;font-weight:800;cursor:pointer}.crmAction.primary{background:#dcfce7}.crmAction:disabled{opacity:.45;cursor:not-allowed}.crmField{width:100%;box-sizing:border-box;border:1px solid var(--bd);border-radius:9px;padding:8px;background:#fff}.crmQuote{border:1px solid var(--bd);border-radius:10px;padding:8px;background:#fff}.avatarCircle{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;font-weight:1000;background:#dbeafe;color:#1e3a8a;flex:0 0 auto}.contactHead{display:flex;align-items:center;gap:9px}.unreadDot{display:inline-grid;place-items:center;min-width:18px;height:18px;padding:0 4px;border-radius:999px;background:#16a34a;color:#fff;font-size:10px;font-weight:900}.toastSoundHint{font-size:10px;color:var(--muted)}
'''
if "GREENIE_CRM360_V1" not in view:
    view = view.replace("</style>", css + "\n  </style>", 1)

js = r'''
  /* GREENIE_CRM360_V1 */
  let __greenie360=null;
  let __greenie360Tab='leads';
  let __greenieLastInboundId=Number(localStorage.getItem('greenieLastInboundId')||0);
  let __greenieAudioUnlocked=false;

  function __greenieInitials(name){return String(name||'C').trim().split(/\s+/).map(x=>x[0]||'').join('').slice(0,2).toUpperCase()||'C'}
  function __greenieMoney(value){try{return Number(value||0).toLocaleString('es-CL',{style:'currency',currency:'CLP',maximumFractionDigits:0})}catch(_){return '$0'}}
  function __greenieDate(value){if(!value)return 'Sin fecha';const s=String(value).slice(0,10);const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(s);return m?`${m[3]}/${m[2]}/${m[1]}`:s}
  function __greenieCall(){if(current?.wa_id)location.href='tel:+'+current.wa_id}
  function __greenieOpenLead(id){window.open('/crm/web/views/leads.html?open='+encodeURIComponent(id),'_blank','noopener')}
  function __greenieOpenCotizador(id){window.open('/crm/web/views/cotizador.html?lead_id='+encodeURIComponent(id),'_blank','noopener')}

  function __greenieBeep(){
    try{
      const C=window.AudioContext||window.webkitAudioContext;if(!C)return;
      const ctx=new C();const osc=ctx.createOscillator();const gain=ctx.createGain();
      osc.frequency.value=880;gain.gain.setValueAtTime(.0001,ctx.currentTime);gain.gain.exponentialRampToValueAtTime(.12,ctx.currentTime+.01);gain.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+.18);osc.connect(gain);gain.connect(ctx.destination);osc.start();osc.stop(ctx.currentTime+.2);
    }catch(_){}
  }
  document.addEventListener('click',()=>{__greenieAudioUnlocked=true},{once:true});

  async function __greenieLoad360(){
    if(!current)return;
    try{
      __greenie360=await api('/gia/whatsapp/conversations/'+current.id+'/commercial-360');
      __greenieRender360();
    }catch(error){console.warn('[GREENIE 360]',error)}
  }

  function __greenieRender360(){
    if(!current||!__greenie360)return;
    const d=__greenie360;const selected=Number(d.selected_lead_id||0);const leads=d.leads||[];const quotes=d.cotizaciones||[];
    const manual=d.routing?.mode==='manual';
    $('#side').innerHTML=`<div class="crm360">
      <div class="card contactHead"><div class="avatarCircle">${__greenieInitials(current.profile_name)}</div><div><div class="label">Cliente WhatsApp</div><div class="value">${esc(current.profile_name||'Sin nombre')}</div><div class="crmMeta">+${esc(current.wa_id||'')}</div></div></div>
      <div class="crm360Summary"><div class="crm360Stat"><span>Leads</span><b>${leads.length}</b></div><div class="crm360Stat"><span>Cotizaciones</span><b>${quotes.length}</b></div><div class="crm360Stat"><span>Total</span><b>${__greenieMoney(d.summary?.total_cotizado)}</b></div></div>
      ${manual?`<div class="card"><div class="label">Asignar ejecutivo</div><select id="crmExec" class="crmField"><option value="">Selecciona</option>${(d.executives||[]).map(x=>`<option value="${esc(x.id)}" data-name="${esc(x.nombre)}">${esc(x.nombre)}</option>`).join('')}</select><button id="crmAssign" class="crmAction primary" style="margin-top:6px;width:100%">Asignar</button></div>`:''}
      <div class="crm360Tabs"><button class="crm360Tab ${__greenie360Tab==='leads'?'active':''}" data-c360="leads">Leads</button><button class="crm360Tab ${__greenie360Tab==='quotes'?'active':''}" data-c360="quotes">Cotizaciones</button><button class="crm360Tab ${__greenie360Tab==='events'?'active':''}" data-c360="events">Eventos</button><button class="crm360Tab ${__greenie360Tab==='actions'?'active':''}" data-c360="actions">Acciones</button></div>
      <div class="crm360Pane ${__greenie360Tab==='leads'?'active':''}" id="c360leads">${leads.length?leads.map(l=>`<div class="crmLead ${Number(l.id_lead)===selected?'active':''}" data-lead="${l.id_lead}"><div class="crmLeadHead"><span>Lead #${l.id_lead}</span><span>${esc(l.estado||'')}</span></div><div>${esc(l.cliente||'')}</div><div class="crmMeta">${esc(l.marca||'')} · ${__greenieDate(l.fecha_evento)} · ${__greenieMoney(l.monto_cotizado)}</div></div>`).join(''):'<div class="card">Este teléfono aún no tiene lead. Créalo para habilitar cotización, seguimiento y gestión comercial.</div>'}</div>
      <div class="crm360Pane ${__greenie360Tab==='quotes'?'active':''}" id="c360quotes">${quotes.length?quotes.map(q=>`<div class="crmQuote"><b>Cotización ${esc(q.numero||('#'+q.id_cotizacion))}</b><div class="crmMeta">Lead #${q.id_lead} · ${__greenieMoney(q.total)}</div>${q.pdf_url?`<a href="${esc(q.pdf_url)}" target="_blank">Ver PDF</a>`:''}</div>`).join(''):'<div class="card">Sin cotizaciones asociadas.</div>'}</div>
      <div class="crm360Pane ${__greenie360Tab==='events'?'active':''}" id="c360events">${(d.eventos||[]).length?(d.eventos||[]).map(e=>`<div class="crmQuote"><b>${__greenieDate(e.fecha_evento||e.calendar_start)}</b><div>${esc(e.cliente||'')}</div><div class="crmMeta">${esc(e.estado||'')} · ${esc(e.comuna||'')}</div>${e.calendar_html_link?`<a href="${esc(e.calendar_html_link)}" target="_blank">Abrir Calendar</a>`:''}</div>`).join(''):'<div class="card">Sin eventos registrados.</div>'}</div>
      <div class="crm360Pane ${__greenie360Tab==='actions'?'active':''}" id="c360actions">
        <div class="crmActions"><button id="crmCall" class="crmAction">📞 Llamar</button><button id="crmViewLead" class="crmAction" ${selected?'':'disabled'}>👁 Ver lead</button><button id="crmQuote" class="crmAction primary" ${selected?'':'disabled'}>🧾 Crear cotización</button><button id="crmSendQuote" class="crmAction" ${selected?'':'disabled'}>📤 Enviar cotización</button></div>
        <select id="crmStatus" class="crmField" ${selected?'':'disabled'}><option value="">Cambiar estado</option>${(d.estados||[]).map(s=>`<option value="${s.id_estado}">${esc(s.nombre)}</option>`).join('')}</select>
        <textarea id="crmFollowText" class="crmField" rows="3" placeholder="Seguimiento" ${selected?'':'disabled'}></textarea><button id="crmFollow" class="crmAction primary" ${selected?'':'disabled'}>Guardar seguimiento</button>
      </div>
      ${!leads.length?`<div class="card"><div class="label">Crear lead primero</div><div class="toastSoundHint">Al crear el lead se habilitarán las acciones comerciales.</div></div>`:''}
    </div>`;
    document.querySelectorAll('[data-c360]').forEach(b=>b.onclick=()=>{__greenie360Tab=b.dataset.c360;__greenieRender360()});
    document.querySelectorAll('[data-lead]').forEach(b=>b.onclick=async()=>{await api('/gia/whatsapp/conversations/'+current.id+'/select-lead',{method:'POST',body:JSON.stringify({lead_id:Number(b.dataset.lead)})});await __greenieLoad360()});
    $('#crmCall')?.addEventListener('click',__greenieCall);$('#crmViewLead')?.addEventListener('click',()=>selected&&__greenieOpenLead(selected));$('#crmQuote')?.addEventListener('click',()=>selected&&__greenieOpenCotizador(selected));
    $('#crmSendQuote')?.addEventListener('click',async()=>{if(!selected)return;try{await api('/gia/whatsapp/conversations/'+current.id+'/leads/'+selected+'/send-quote',{method:'POST'});await openConversation(current.id)}catch(e){alert(e.message)}});
    $('#crmStatus')?.addEventListener('change',async e=>{if(!selected||!e.target.value)return;try{await api('/leads/'+selected+'/estado',{method:'PATCH',body:JSON.stringify({id_estado:Number(e.target.value)})});await __greenieLoad360()}catch(err){alert(err.message)}});
    $('#crmFollow')?.addEventListener('click',async()=>{const tx=$('#crmFollowText')?.value.trim();if(!selected||!tx)return;try{await api('/gia/whatsapp/conversations/'+current.id+'/leads/'+selected+'/followup',{method:'POST',body:JSON.stringify({text:tx,kind:'WSP',title:'Seguimiento desde Greenie'})});$('#crmFollowText').value='';await __greenieLoad360()}catch(e){alert(e.message)}});
    $('#crmAssign')?.addEventListener('click',async()=>{const sel=$('#crmExec');const op=sel?.selectedOptions?.[0];if(!op?.value)return;try{await api('/gia/whatsapp/conversations/'+current.id+'/assign-executive',{method:'POST',body:JSON.stringify({executive_id:op.value,executive_name:op.dataset.name,lead_id:selected||null})});await __greenieLoad360()}catch(e){alert(e.message)}});
  }

  const __greenieOpenConversation360=openConversation;
  openConversation=async function(id){await __greenieOpenConversation360(id);await __greenieLoad360()};

  const __greenieCreateLead360=createLead;
  createLead=async function(event){await __greenieCreateLead360(event);setTimeout(__greenieLoad360,500)};

  const __greenieLoadWithSound=load;
  load=async function(){
    const before=__greenieLastInboundId;
    const result=await __greenieLoadWithSound();
    try{
      if(current){const data=await api('/gia/whatsapp/conversations/'+current.id+'/messages-v2');const inbound=(data.items||[]).filter(x=>x.direction==='inbound');const newest=Math.max(0,...inbound.map(x=>Number(x.id||0)));if(newest>before&&before>0&&__greenieAudioUnlocked)__greenieBeep();if(newest>__greenieLastInboundId){__greenieLastInboundId=newest;localStorage.setItem('greenieLastInboundId',String(newest))}}
    }catch(_){}
    return result;
  };
'''
if "GREENIE_CRM360_V1" not in view:
    pos = view.rfind("\n})();")
    if pos < 0:
        raise SystemExit("ERROR: no se encontro cierre JS en Greenie")
    view = view[:pos] + "\n" + js + view[pos:]

# Cache-control y versión visual.
if 'http-equiv="Cache-Control"' not in view:
    view = view.replace('<meta name="viewport" content="width=device-width,initial-scale=1" />', '<meta name="viewport" content="width=device-width,initial-scale=1" />\n  <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate" />')
GREENIE.write_text(view, encoding="utf-8")
CANONICAL.write_text(view, encoding="utf-8")
print("ACTUALIZADO: Greenie y alias canónico")
print("GREENIE_CRM360_READY")
