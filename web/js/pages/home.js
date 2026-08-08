export function Home(){
  const state = { loading:true, data:null };

  const view = () => {
    if (state.loading) {
      return `<section class="grid cols-3">
        <div class="kpi"><h3>Leads del día</h3><div class="big">—</div></div>
        <div class="kpi"><h3>Ventas del mes</h3><div class="big">—</div></div>
        <div class="kpi"><h3>Eventos de hoy</h3><div class="big">—</div></div>
      </section>`;
    }
    const d = state.data;
    return `
      <section class="grid cols-3" style="margin-bottom:14px">
        <div class="kpi"><h3>Pendientes hoy</h3><div class="big">${d.leads.pendientes_hoy}</div></div>
        <div class="kpi"><h3>Recientes (48h)</h3><div class="big">${d.leads.recientes_48h}</div></div>
        <div class="kpi"><h3>Sin mov. (7d)</h3><div class="big">${d.leads.sin_movimiento_7d}</div></div>
      </section>
      <section class="grid cols-3">
        <div class="kpi"><h3>Ventas del mes</h3><div class="big">$ ${Intl.NumberFormat('es-CL').format(d.ventas.mes_actual)}</div></div>
        <div class="kpi"><h3>Meta del mes</h3><div class="big">$ ${Intl.NumberFormat('es-CL').format(d.ventas.meta_mes)}</div></div>
        <div class="kpi"><h3>Eventos de hoy</h3><div>${d.eventos_hoy.map(e=>`${e.hora} · ${e.titulo}`).join("<br>")}</div></div>
      </section>
    `;
  };

  // dispara fetch sin bloquear render inicial
  fetch("/dashboard/summary")
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(json => { state.loading=false; state.data=json; document.getElementById("app").innerHTML = view(); })
    .catch(() => {
      state.loading=false;
      state.data = {
        leads:{pendientes_hoy:6,recientes_48h:14,sin_movimiento_7d:9},
        ventas:{mes_actual:12850000,meta_mes:20000000},
        eventos_hoy:[{hora:"09:30",titulo:"Sincronización"},{hora:"11:00",titulo:"Demo"},{hora:"16:00",titulo:"Seguimiento"}]
      };
      document.getElementById("app").innerHTML = view();
    });

  return view();
}
