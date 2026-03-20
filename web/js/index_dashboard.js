// /web/js/index_dashboard.js
(() => {
  'use strict';
  const U = (s) => String(s || '').toUpperCase();
  const $ = (q, c=document) => c.querySelector(q);
  const API_BASE = (() => {
    try {
      const h = String(location.hostname || "").toLowerCase();
      const isLocal = h === "localhost" || h === "127.0.0.1";
      return isLocal ? "" : "/crm";
    } catch (_) {
      return "";
    }
  })();

  function authHeaders(extra={}) {
    const GD = window.parent && window.parent.GD;
    return GD && GD.authHeaders ? GD.authHeaders(extra) : extra;
  }

  function groupByDate(events) {
    const m = new Map();
    (events || []).forEach(e => {
      const d = String(e.calendar_start || '').slice(0,10) || 'SIN FECHA';
      if (!m.has(d)) m.set(d, []);
      m.get(d).push(e);
    });
    return m;
  }

  async function load() {
    const res = await fetch(`${API_BASE}/tools/dashboard`, { headers: authHeaders() });
    if (!res.ok) return;
    const j = await res.json();

    const weekEl = $('#weekEvents');
    const pendEl = $('#pendingAgenda');

    if (weekEl) {
      const g = groupByDate(j.week_events || []);
      let html = '';
      [...g.keys()].sort().forEach(d => {
        html += `<h4>${U(d)}</h4>`;
        html += `<ul>`;
        g.get(d).forEach(x => {
          const name = U(x.nombre_cliente || '');
          const loc = U(x.pre_location || '');
          const link = x.calendar_html_link ? `<a href="${x.calendar_html_link}" target="_blank" rel="noopener">ABRIR</a>` : '';
          html += `<li>${name} - ${loc} ${link}</li>`;
        });
        html += `</ul>`;
      });
      weekEl.innerHTML = html || `<div>SIN EVENTOS ESTA SEMANA.</div>`;
    }

    if (pendEl) {
      const arr = j.to_schedule || [];
      pendEl.innerHTML = arr.length
        ? `<ul>${arr.map(x => `<li>${U(x.pre_title || x.nombre_cliente || '')} - ${U(x.pre_location || '')}</li>`).join('')}</ul>`
        : `<div>NO HAY EVENTOS PENDIENTES POR AGENDAR.</div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', load);
})();
