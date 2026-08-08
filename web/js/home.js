// /web/js/home.js
(() => {
  'use strict';
  const $ = (q, c = document) => c.querySelector(q);

  function authHeaders(extra = {}) {
    const GD = window.parent && window.parent.GD;
    return GD && GD.authHeaders ? GD.authHeaders(extra) : extra;
  }

  function up(s) { return String(s || '').toUpperCase(); }

  function fmtDate(s) {
    if (!s) return '';
    return String(s).split('T')[0];
  }
  function fmtTime(s) {
    if (!s) return '';
    const m = String(s).match(/T(\d{2}:\d{2})/);
    return m ? m[1] : '';
  }

  async function apiToolsHome() {
    const res = await fetch('/tools/home', { headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status} /tools/home`);
    return await res.json();
  }

  function groupByDay(events) {
    const map = new Map();
    (events || []).forEach((e) => {
      const d = fmtDate(e.calendar_start || '');
      if (!map.has(d)) map.set(d, []);
      map.get(d).push(e);
    });
    // orden por fecha
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }

  function leadLine(e) {
    const codigo = up(e.codigo_cliente || '');
    const nombre = up(e.nombre_cliente || '');
    const marca = up(e.marca || '');
    const comuna = up(e.comuna || '');
    const d = up(fmtDate(e.calendar_start || ''));
    const h1 = up(fmtTime(e.calendar_start || ''));
    const h2 = up(fmtTime(e.calendar_end || ''));
    return `${codigo} · ${nombre} · ${marca} · ${comuna} · ${d} ${h1}-${h2}`;
  }

  async function renderHome() {
    const root = $('#app');
    if (!root) return;

    root.innerHTML = `
      <div style="display:grid; gap:14px;">
        <div id="remBlock" style="padding:14px; border:1px solid rgba(148,163,184,.2); border-radius:14px;"></div>
        <div id="weekBlock" style="padding:14px; border:1px solid rgba(148,163,184,.2); border-radius:14px;"></div>
      </div>
    `;

    const rem = $('#remBlock');
    const wk = $('#weekBlock');

    try {
      const j = await apiToolsHome();

      const pend = j.pendientes || {};
      const total = Number(pend.total || 0);
      const porAprobar = pend.por_aprobar || [];
      const sinPre = pend.confirmados_sin_preagenda || [];

      rem.innerHTML = `
        <div style="font-weight:900; letter-spacing:.08em;">RECORDATORIO · PENDIENTES POR AGENDAR (${total})</div>
        <div style="margin-top:10px; font-size:12px; color:#6b7280;">SEMANA: ${up(j.week?.start)} A ${up(j.week?.end)}</div>

        <div style="margin-top:12px;">
          <div style="font-weight:800; font-size:12px;">POR APROBAR (${porAprobar.length})</div>
          ${(porAprobar || []).slice(0, 10).map(x => `
            <div style="padding:8px 10px; border:1px solid rgba(148,163,184,.18); border-radius:12px; margin-top:8px; font-size:12px;">
              ${up(x.codigo_cliente || '')} · ${up(x.nombre_cliente || '')} · ${up(x.marca || '')}
            </div>
          `).join('') || `<div style="margin-top:8px; color:#6b7280; font-size:12px;">SIN ITEMS</div>`}
        </div>

        <div style="margin-top:14px;">
          <div style="font-weight:800; font-size:12px;">CONFIRMADO SIN PRE-AGENDA (${sinPre.length})</div>
          ${(sinPre || []).slice(0, 10).map(x => `
            <div style="padding:8px 10px; border:1px solid rgba(148,163,184,.18); border-radius:12px; margin-top:8px; font-size:12px;">
              ${up(x.codigo_cliente || '')} · ${up(x.nombre_cliente || '')} · ${up(x.marca || '')}
            </div>
          `).join('') || `<div style="margin-top:8px; color:#6b7280; font-size:12px;">SIN ITEMS</div>`}
        </div>
      `;

      const events = j.eventos_semana || [];
      const grouped = groupByDay(events);

      wk.innerHTML = `
        <div style="font-weight:900; letter-spacing:.08em;">EVENTOS DE LA SEMANA (${events.length})</div>
        <div style="margin-top:12px;">
          ${
            grouped.length
              ? grouped.map(([day, list]) => `
                <div style="margin-top:12px;">
                  <div style="font-weight:800; font-size:12px;">${up(day)}</div>
                  ${(list || []).map(e => {
                    const line = up(leadLine(e));
                    const link = e.calendar_html_link || '';
                    return `
                      <div style="display:flex; gap:10px; justify-content:space-between; align-items:center; padding:10px 12px; border:1px solid rgba(148,163,184,.18); border-radius:12px; margin-top:8px; font-size:12px;">
                        <div style="flex:1;">${line}</div>
                        ${link ? `<a href="${link}" target="_blank" rel="noopener" style="font-weight:800;">ABRIR</a>` : ''}
                      </div>
                    `;
                  }).join('')}
                </div>
              `).join('')
              : `<div style="margin-top:10px; color:#6b7280; font-size:12px;">SIN EVENTOS EN LA SEMANA.</div>`
          }
        </div>
      `;

    } catch (e) {
      console.error(e);
      rem.innerHTML = `<div style="color:#ef4444; font-weight:800;">ERROR CARGANDO HOME.</div>`;
      wk.innerHTML = '';
    }
  }

  // expón si tu router llama funciones
  window.GD_HOME = { renderHome };

})();
