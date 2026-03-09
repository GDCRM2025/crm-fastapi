// web/js/reportes.js
(() => {
  'use strict';

  const $ = (q, c = document) => c.querySelector(q);

  function money(n) {
    const v = Number(n || 0);
    return '$' + v.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }

  function fmtFecha(iso) {
    if (!iso) return '-';
    // iso "YYYY-MM-DD"
    const [y, m, d] = iso.split('-');
    if (!y || !m || !d) return iso;
    return `${d}/${m}/${y}`;
  }

  async function loadMarcas() {
    try {
      const headers = (window.parent.GD && window.parent.GD.authHeaders()) || {};
      const resp = await fetch('/leads/catalogs', { headers });
      if (!resp.ok) return;
      const data = await resp.json();
      const select = $('#f_marca');
      if (!select || !data.marcas) return;
      data.marcas.forEach((m) => {
        const opt = document.createElement('option');
        opt.value = m.id_marca;
        opt.textContent = m.nombre;
        select.appendChild(opt);
      });
    } catch {
      // si falla, seguimos con "Todas"
    }
  }

  function renderFunnel(items) {
    const cont = $('#funnel-list');
    cont.innerHTML = '';
    if (!items || !items.length) {
      cont.innerHTML = '<p class="app-empty">Sin datos en el rango seleccionado.</p>';
      return;
    }
    items.forEach((row) => {
      const el = document.createElement('div');
      el.className = 'app-list-row';
      el.innerHTML = `<strong>${row.estado}</strong> — ${row.cantidad} leads · ${money(row.monto_total)}`;
      cont.appendChild(el);
    });
  }

  function renderEventos(items) {
    const cont = $('#eventos-list');
    cont.innerHTML = '';
    if (!items || !items.length) {
      cont.innerHTML = '<p class="app-empty">No hay eventos en el rango seleccionado.</p>';
      return;
    }
    items.forEach((row) => {
      const el = document.createElement('div');
      el.className = 'app-list-row';
      el.innerHTML = `<strong>${fmtFecha(row.fecha)}</strong> — ${row.cantidad} eventos · ${money(row.monto_total)}`;
      cont.appendChild(el);
    });
  }

  function renderTopProductos(items) {
    const cont = $('#topprod-list');
    cont.innerHTML = '';
    if (!items || !items.length) {
      cont.innerHTML = '<p class="app-empty">Sin productos en el rango seleccionado.</p>';
      return;
    }
    items.forEach((row, idx) => {
      const el = document.createElement('div');
      el.className = 'app-list-row';
      el.innerHTML =
        `<strong>#${idx + 1}</strong> ` +
        `${row.producto} — ${row.marca || 'Sin marca'} · ` +
        `${row.cantidad} uds · ${money(row.monto)}`;
      cont.appendChild(el);
    });
  }

  function renderClientes(items) {
    const cont = $('#clientes-list');
    cont.innerHTML = '';
    if (!items || !items.length) {
      cont.innerHTML = '<p class="app-empty">Sin clientes frecuentes en el rango.</p>';
      return;
    }
    items.forEach((row) => {
      const el = document.createElement('div');
      el.className = 'app-list-row';
      el.innerHTML =
        `<strong>${row.cliente}</strong> — ${row.eventos} eventos · ${money(row.monto_total)}`;
      cont.appendChild(el);
    });
  }

  function renderComunas(items) {
    const cont = $('#comunas-list');
    cont.innerHTML = '';
    if (!items || !items.length) {
      cont.innerHTML = '<p class="app-empty">Sin comunas en el rango.</p>';
      return;
    }
    items.forEach((row) => {
      const el = document.createElement('div');
      el.className = 'app-list-row';
      el.innerHTML =
        `<strong>${row.comuna}</strong> — ${row.eventos} eventos · ${money(row.monto_total)}`;
      cont.appendChild(el);
    });
  }

  async function loadReportes() {
    const errBox = $('#reportes-error');
    errBox.style.display = 'none';
    errBox.textContent = '';

    const desde = $('#f_desde').value || '';
    const hasta = $('#f_hasta').value || '';
    const idMarca = $('#f_marca').value || '';

    const params = new URLSearchParams();
    if (desde) params.append('fecha_inicio', desde);
    if (hasta) params.append('fecha_termino', hasta);
    if (idMarca) params.append('id_marca', idMarca);

    try {
      const headers = (window.parent.GD && window.parent.GD.authHeaders()) || {};
      const resp = await fetch('/dashboard/reportes?' + params.toString(), { headers });
      if (!resp.ok) {
        throw new Error('HTTP ' + resp.status);
      }
      const data = await resp.json();

      renderFunnel(data.funnel || []);
      renderEventos(data.eventos_diarios || []);
      renderTopProductos(data.top_productos || []);
      renderClientes(data.clientes || []);
      renderComunas(data.comunas || []);
    } catch (e) {
      console.error('Error cargando reportes', e);
      errBox.textContent = 'Error cargando reportes. Revisa la consola.';
      errBox.style.display = '';
    }
  }

  function initFechas() {
    const hoy = new Date();
    const yyyy = hoy.getFullYear();
    const mm = String(hoy.getMonth() + 1).padStart(2, '0');
    const dd = String(hoy.getDate()).padStart(2, '0');

    const hasta = `${yyyy}-${mm}-${dd}`;
    const desde = `${yyyy}-${mm}-01`;

    $('#f_desde').value = desde;
    $('#f_hasta').value = hasta;
  }

  document.addEventListener('DOMContentLoaded', () => {
    initFechas();
    loadMarcas();
    loadReportes();

    const form = $('#reportes-filtros');
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      loadReportes();
    });
  });
})();
