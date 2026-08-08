// web/js/calendar.js

const $ = (q, c = document) => c.querySelector(q);

const TOKEN =
  localStorage.getItem('token') || sessionStorage.getItem('token') || '';
const AUTH = TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {};
const JSONH = { ...AUTH, 'Content-Type': 'application/json' };

const Toast = Swal.mixin({
  toast: true,
  position: 'top-end',
  timer: 1700,
  showConfirmButton: false,
  timerProgressBar: true,
});

async function fetchJSON(url) {
  const r = await fetch(url, { headers: AUTH });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function fmtFechaHoraRange(inicio, termino) {
  if (!inicio) return { fecha: '-', rango: '-' };

  const norm = (s) => String(s).replace(' ', 'T');
  const si = norm(inicio);
  const st = termino ? norm(termino) : null;

  const di = new Date(si);
  const dt = st ? new Date(st) : null;

  const pad = (n) => String(n).padStart(2, '0');
  const fecha = `${pad(di.getDate())}/${pad(di.getMonth() + 1)}/${di.getFullYear()}`;
  let rango = '-';

  if (!isNaN(di.getTime())) {
    const hi = pad(di.getHours());
    const mi = pad(di.getMinutes());
    if (dt && !isNaN(dt.getTime())) {
      const ht = pad(dt.getHours());
      const mt = pad(dt.getMinutes());
      rango = `${hi}:${mi} - ${ht}:${mt}`;
    } else {
      rango = `${hi}:${mi}`;
    }
  }

  return { fecha, rango };
}

function renderEvents(items) {
  const tbody = $('#calBody');
  if (!tbody) return;

  tbody.innerHTML = '';

  if (!Array.isArray(items) || !items.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="7" style="text-align:center;padding:12px;color:#6b7280;">
      No hay eventos para mostrar.
    </td>`;
    tbody.appendChild(tr);
    return;
  }

  items.forEach((e) => {
    const { fecha, rango } = fmtFechaHoraRange(e.fecha_inicio, e.fecha_termino);
    const tr = document.createElement('tr');

    tr.innerHTML = `
      <td>${fecha}</td>
      <td>${rango}</td>
      <td>
        ${e.nombre_cliente || '-'}<br>
        <small>${e.marca || ''}</small>
      </td>
      <td>${e.comuna || '-'}</td>
      <td>${e.estado || '-'}</td>
      <td>${e.titulo || ''}</td>
      <td class="cal-actions"></td>
    `;

    const tdActions = tr.querySelector('.cal-actions');

    if ((e.estado || '').toLowerCase() === 'pendiente') {
      const btnOk = document.createElement('button');
      btnOk.className = 'btn ghost';
      btnOk.textContent = '✅ Aprobar';
      btnOk.addEventListener('click', () => approveEvent(e.id_evento));

      const btnKo = document.createElement('button');
      btnKo.className = 'btn ghost';
      btnKo.textContent = '❌ Rechazar';
      btnKo.addEventListener('click', () => rejectEvent(e.id_evento));

      tdActions.appendChild(btnOk);
      tdActions.appendChild(btnKo);
    } else {
      tdActions.textContent = '—';
    }

    tbody.appendChild(tr);
  });
}

async function loadEvents() {
  const selEstado = $('#calEstado');
  const estado = selEstado ? selEstado.value : '';
  let url = '/calendar/pre-agenda?limit=100';
  if (estado) {
    url += '&estado=' + encodeURIComponent(estado);
  }

  try {
    const data = await fetchJSON(url);
    renderEvents(data.items || []);
  } catch (err) {
    console.error(err);
    Toast.fire({ icon: 'error', title: 'No se pudo cargar calendario' });
  }
}

async function approveEvent(idEvento) {
  const resp = await Swal.fire({
    title: 'Aprobar evento',
    text: '¿Confirmas subir este evento como aprobado?',
    icon: 'question',
    showCancelButton: true,
    confirmButtonText: 'Sí, aprobar',
    cancelButtonText: 'Cancelar',
  });
  if (!resp.isConfirmed) return;

  try {
    await fetch(`/calendar/${idEvento}/approve`, {
      method: 'POST',
      headers: JSONH,
      body: JSON.stringify({}),
    });
    Toast.fire({ icon: 'success', title: 'Evento aprobado' });
    loadEvents();
  } catch (err) {
    console.error(err);
    Toast.fire({ icon: 'error', title: 'Error al aprobar' });
  }
}

async function rejectEvent(idEvento) {
  const resp = await Swal.fire({
    title: 'Rechazar evento',
    text: '¿Seguro que deseas marcar este evento como rechazado?',
    icon: 'warning',
    showCancelButton: true,
    confirmButtonText: 'Sí, rechazar',
    cancelButtonText: 'Cancelar',
  });
  if (!resp.isConfirmed) return;

  try {
    await fetch(`/calendar/${idEvento}/reject`, {
      method: 'POST',
      headers: JSONH,
      body: JSON.stringify({}),
    });
    Toast.fire({ icon: 'success', title: 'Evento rechazado' });
    loadEvents();
  } catch (err) {
    console.error(err);
    Toast.fire({ icon: 'error', title: 'Error al rechazar' });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('#calEstado')?.addEventListener('change', () => loadEvents());
  $('#btnCalReload')?.addEventListener('click', () => loadEvents());
  loadEvents();
});
