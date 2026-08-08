import {page} from "../common.js";
export const Correo=()=>page('Correo',`<div class="card">Conectar correo — WIP.</div>`);
export const Instagram=()=>page('Instagram',`<div class="card">Conectar cuenta — WIP.</div>`);
export const Whatsapp=()=>page('WhatsApp',`<div class="card">Integración — WIP.</div>`);
export const Calendario=()=>page('Calendario',`<div class="card">Calendario mensual — WIP.</div>`);
export const Calculadora=()=>page('Calculadora',`
  <div class="card">
    <div>Monto: <input id="calcMonto" type="number" step="0.01" /></div>
    <div style="margin-top:8px;display:flex;gap:8px;">
      <button id="btnIVA">+ IVA (19%)</button>
      <button id="btnWP">+ Webpay (4%)</button>
    </div>
    <div id="calcOut" class="badge" style="margin-top:10px;">—</div>
  </div>
  <script type="module">
    const $=s=>document.querySelector(s); const fmt=n=>new Intl.NumberFormat('es-CL',{style:'currency',currency:'CLP'}).format(n);
    $('#btnIVA').onclick=()=>{const v=parseFloat($('#calcMonto').value||'0'); const iva=v*0.19,total=v+iva; $('#calcOut').textContent=\`Neto: \${fmt(v)} | IVA: \${fmt(iva)} | Total: \${fmt(total)}\`;};
    $('#btnWP').onclick=()=>{const v=parseFloat($('#calcMonto').value||'0'); const fee=v*0.04,total=v+fee; $('#calcOut').textContent=\`Base: \${fmt(v)} | Webpay(4%): \${fmt(fee)} | Total: \${fmt(total)}\`;};
  </script>
`);
export const Clima=()=>page('Clima — Santiago',`<div class="card">Forecast semanal — WIP.</div>`);
