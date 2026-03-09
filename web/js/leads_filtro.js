// web/js/leads_filtro.js
(() => {
  'use strict';

  const $ = (q, c = document) => c.querySelector(q);

  const state = {
    items: [],
    resumen: { cantidad: 0, monto_total: 0 },
    cacheByLead: {}, // id_lead -> { cotizacion, items }
  };

  function authHeaders(extra = {}) {
    const GD = window.parent && window.parent.GD;
    return GD && GD.authHeaders ? GD.authHeaders(extra) : extra;
  }

  function fmtMoney(v) {
    const n = Number(v || 0);
    return '$' + n.toLocaleString('es-CL');
  }

  function fmtFechaDMY(s) {
    if (!s) return '-';
    const str = String(s).split('T')[0];
    const parts = str.split('-');
    if (parts.length !== 3) return str;
    const [y, m, d] = parts;
    return `${d}/${m}/${y}`;
  }

  // ---------- catálogos ----------
  async function loadCatalogs() {
    try {
      const res = await fetch('/leads/catalogs', { headers: authHeaders() });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();

      const estados = json.states || json.estados || [];
      const marcas = json.brands || json.marcas || [];

      fillEstados(estados);
      fillMarcas(marcas);
    } catch (err) {
      console.error('Error cargando catálogos', err);
    }
  }

  function fillEstados(estados) {
    const sel = $('#fEstado');
    if (!sel) return;
    estados.forEach((e) => {
      const opt = document.createElement('option');
      opt.value = e.id_estado;
      opt.textContent = e.nombre;
      sel.appendChild(opt);
    });
  }

  function fillMarcas(marcas) {
    const sel = $('#fMarca');
    if (!sel) return;
    marcas.forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m.id_marca;
      opt.textContent = m.nombre;
      sel.appendChild(opt);
    });
  }

  // ---------- filtros ----------
  async function aplicarFiltros() {
    const idEstado = $('#fEstado').value || '';
    const idMarca = $('#fMarca').value || '';
    const fInicio = $('#fInicio').value || '';
    const fFin = $('#fFin').value || '';

    const qs = new URLSearchParams();
    if (idEstado) qs.append('id_estado', idEstado);
    if (idMarca) qs.append('id_marca', idMarca);
    if (fInicio) qs.append('fecha_inicio', fInicio);
    if (fFin) qs.append('fecha_termino', fFin);

    const url =
      '/quotes/leads-filter' + (qs.toString() ? `?${qs.toString()}` : '');

    const btn = $('#btnFiltrar');
    const tbody = $('#tbodyResultados');

    try {
      if (btn) btn.disabled = true;
      state.cacheByLead = {};
      const res = await fetch(url, { headers: authHeaders() });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();

      state.items = json.items || [];
      state.resumen = json.resumen || {
        cantidad: state.items.length,
        monto_total: state.items.reduce(
          (acc, it) => acc + Number(it.monto_cotizado || 0),
          0,
        ),
      };

      renderResultados();
    } catch (err) {
      console.error('Error filtrando leads', err);
      if (tbody) {
        tbody.innerHTML =
          '<tr class="empty-row"><td colspan="11">Error al cargar resultados.</td></tr>';
      }
      updateResumenUI();
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function resetFiltros() {
    $('#fEstado').value = '';
    $('#fMarca').value = '';
    $('#fInicio').value = '';
    $('#fFin').value = '';
    state.items = [];
    state.resumen = { cantidad: 0, monto_total: 0 };
    state.cacheByLead = {};

    const tbody = $('#tbodyResultados');
    if (tbody) {
      tbody.innerHTML =
        '<tr class="empty-row"><td colspan="11">Aplica filtros para ver resultados.</td></tr>';
    }
    updateResumenUI();
  }

  function updateResumenUI() {
    const box = $('#resumenFiltro');
    if (!box) return;
    const cant = state.resumen.cantidad || state.items.length || 0;
    const total = fmtMoney(state.resumen.monto_total || 0);
    box.textContent = `${cant} leads · ${total}`;
  }

  function renderResultados() {
    const tbody = $('#tbodyResultados');
    if (!tbody) return;

    const items = state.items || [];
    if (!items.length) {
      tbody.innerHTML =
        '<tr class="empty-row"><td colspan="11">Sin resultados.</td></tr>';
      updateResumenUI();
      return;
    }

    let html = '';
    items.forEach((it) => {
      const fechaIng = fmtFechaDMY(it.fecha_ingreso);
      const fechaEv = fmtFechaDMY(it.fecha_evento);
      const fechaC = fmtFechaDMY(it.fecha_cierre);
      const monto = fmtMoney(it.monto_cotizado);

      html += `
        <tr data-lead="${it.id_lead || ''}">
          <td>${it.codigo_cliente || ''}</td>
          <td>${fechaIng}</td>
          <td>${it.marca || '-'}</td>
          <td>${it.nombre_cliente || '-'}</td>
          <td>${it.plataforma || '-'}</td>
          <td>${fechaEv}</td>
          <td>${it.comuna || '-'}</td>
          <td class="money">${monto}</td>
          <td>${it.estado || '-'}</td>
          <td>${fechaC}</td>
          <td class="actions">
            <button
              type="button"
              class="icon-btn"
              title="Ver cotización (PDF)"
              data-action="pdf"
            >
              🧾
            </button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
    updateResumenUI();

    // listeners: hover + botón PDF
    tbody.querySelectorAll('tr[data-lead]').forEach((tr) => {
      tr.addEventListener('mouseenter', onRowEnter);
      tr.addEventListener('mouseleave', hideTooltip);
    });
    tbody.querySelectorAll('button[data-action="pdf"]').forEach((btn) => {
      btn.addEventListener('click', onVerCotizacion);
    });
  }

  // ---------- tooltip productos ----------
  const tooltipEl = $('#leadTooltip');

  function hideTooltip() {
    if (tooltipEl) {
      tooltipEl.style.display = 'none';
    }
  }

  function positionTooltip(ev) {
    if (!tooltipEl) return;
    const margin = 12;
    let x = ev.clientX + margin;
    let y = ev.clientY + margin;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const rect = tooltipEl.getBoundingClientRect();
    if (x + rect.width > vw) x = ev.clientX - rect.width - margin;
    if (y + rect.height > vh) y = ev.clientY - rect.height - margin;
    tooltipEl.style.left = x + 'px';
    tooltipEl.style.top = y + 'px';
  }

  async function onRowEnter(ev) {
    const tr = ev.currentTarget;
    const idLead = tr.dataset.lead;
    if (!idLead || !tooltipEl) return;

    tooltipEl.style.display = 'block';
    tooltipEl.innerHTML =
      '<div class="lead-tooltip-title">Cargando productos...</div>';
    positionTooltip(ev);

    try {
      let cached = state.cacheByLead[idLead];
      if (!cached) {
        const res = await fetch(`/quotes/lead/${idLead}`, {
          headers: authHeaders(),
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const json = await res.json();
        cached = {
          cotizacion: json.cotizacion || null,
          items: json.items || [],
        };
        state.cacheByLead[idLead] = cached;
      }

      const cot = cached.cotizacion;
      const items = cached.items || [];

      if (!cot) {
        tooltipEl.innerHTML =
          '<div class="lead-tooltip-title">Sin cotización</div><div>Este lead aún no tiene cotización guardada.</div>';
      } else if (!items.length) {
        tooltipEl.innerHTML = `
          <div class="lead-tooltip-title">
            Cotización ${cot.numero_str || cot.numero || ''}
          </div>
          <div>Sin ítems registrados.</div>
        `;
      } else {
        const title = `Cotización ${cot.numero_str || cot.numero || ''}`;
        const lines = items
          .map((it) => {
            const qty = (it.cantidad || 0).toString().replace('.0', '');
            const name = it.nombre_producto || '';
            const unit = fmtMoney(it.precio_unitario || 0);
            return `<li>${qty} × ${name} — ${unit}</li>`;
          })
          .join('');
        tooltipEl.innerHTML = `
          <div class="lead-tooltip-title">${title}</div>
          <ul>${lines}</ul>
        `;
      }
    } catch (err) {
      console.error('Error tooltip lead', err);
      tooltipEl.innerHTML =
        '<div class="lead-tooltip-title">Error</div><div>No se pudieron cargar los productos.</div>';
    }

    positionTooltip(ev);
  }

  async function onVerCotizacion(ev) {
    ev.stopPropagation();
    const tr = ev.currentTarget.closest('tr[data-lead]');
    if (!tr) return;
    const idLead = tr.dataset.lead;
    if (!idLead) return;

    try {
      let cached = state.cacheByLead[idLead];
      if (!cached) {
        const res = await fetch(`/quotes/lead/${idLead}`, {
          headers: authHeaders(),
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const json = await res.json();
        cached = {
          cotizacion: json.cotizacion || null,
          items: json.items || [],
        };
        state.cacheByLead[idLead] = cached;
      }
      const cot = cached.cotizacion;
      if (!cot) {
        alert('Este lead aún no tiene cotización.');
        return;
      }
      const idCot = cot.id_cotizacion;
      if (!idCot) {
        alert('No se encontró el ID de cotización.');
        return;
      }
      window.open(`/quotes/${idCot}/pdf`, '_blank');
    } catch (err) {
      console.error('Error abriendo cotización', err);
      alert('Error al abrir la cotización.');
    }
  }

  // ---------- WhatsApp resumen ----------
  function sendWhatsapp() {
    const items = state.items || [];
    if (!items.length) return;

    const telRaw = $('#waPhone').value || '';
    const tel = telRaw.replace(/[^0-9+]/g, '');

    const lines = [];
    lines.push('Resumen de leads filtrados:');
    lines.push('');

    items.forEach((it) => {
      const fechaEv = fmtFechaDMY(it.fecha_evento);
      const monto = fmtMoney(it.monto_cotizado);
      lines.push(
        `${it.codigo_cliente || ''} · ${fechaEv} · ${
          it.marca || '-'
        } · ${it.nombre_cliente || '-'} · ${
          it.comuna || '-'
        } · ${monto}`,
      );
    });

    const cant = state.resumen.cantidad || items.length;
    const total = fmtMoney(state.resumen.monto_total || 0);
    lines.push('');
    lines.push(`Total leads: ${cant}`);
    lines.push(`Monto total cotizado: ${total}`);

    const txt = encodeURIComponent(lines.join('\n'));
    const base = tel ? `https://wa.me/${tel}` : 'https://wa.me/';
    const url = `${base}?text=${txt}`;
    window.open(url, '_blank');
  }

  // ---------- init ----------
  document.addEventListener('DOMContentLoaded', () => {
    loadCatalogs();
    updateResumenUI();
    $('#btnFiltrar')?.addEventListener('click', aplicarFiltros);
    $('#btnLimpiar')?.addEventListener('click', resetFiltros);
    $('#btnWhats')?.addEventListener('click', sendWhatsapp);
  });
})();
