export function toast(msg, type="ok") {
  let el = document.getElementById("gd_toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "gd_toast";
    el.style.cssText = `
      position:fixed; top:12px; right:12px; z-index:9999;
      padding:10px 12px; border-radius:14px;
      border:1px solid rgba(148,163,184,.25);
      background: rgba(2,6,23,.85);
      color: white; font-weight:900; font-size:12px;
      box-shadow: 0 18px 60px rgba(0,0,0,.35);
      display:none; max-width: 260px;
    `;
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = "block";
  el.style.borderColor = type === "err" ? "rgba(239,68,68,.55)" : "rgba(34,197,94,.55)";
  el.style.background = type === "err" ? "rgba(127,29,29,.85)" : "rgba(2,6,23,.85)";
  clearTimeout(window.__gd_toast_t);
  window.__gd_toast_t = setTimeout(() => { el.style.display="none"; }, 2200);
}

export async function modalConfirm({title="Confirmar", text="", okText="OK", cancelText="Cancelar"} = {}) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.style.cssText = `
      position:fixed; inset:0; z-index:9998;
      background: rgba(2,6,23,.55); backdrop-filter: blur(10px);
      display:flex; align-items:center; justify-content:center; padding:14px;
    `;
    wrap.innerHTML = `
      <div style="
        width:min(380px,100%); border-radius:18px; overflow:hidden;
        border:1px solid rgba(148,163,184,.22);
        background: rgba(15,23,42,.92);
        color: white;
        box-shadow: 0 20px 70px rgba(0,0,0,.40);
      ">
        <div style="padding:14px 14px 10px 14px; font-weight:900;">${escapeHtml(title)}</div>
        <div style="padding:0 14px 14px 14px; opacity:.9; font-size:13px;">${escapeHtml(text)}</div>
        <div style="display:flex; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid rgba(148,163,184,.14);">
          <button id="gd_cancel" style="border:none;border-radius:12px;padding:10px 12px; background:rgba(148,163,184,.16); color:white; font-weight:800; cursor:pointer;">${escapeHtml(cancelText)}</button>
          <button id="gd_ok" style="border:none;border-radius:12px;padding:10px 12px; background:rgba(34,197,94,.22); color:white; font-weight:900; cursor:pointer;">${escapeHtml(okText)}</button>
        </div>
      </div>
    `;
    document.body.appendChild(wrap);
    wrap.querySelector("#gd_cancel").addEventListener("click", () => { wrap.remove(); resolve(false); });
    wrap.querySelector("#gd_ok").addEventListener("click", () => { wrap.remove(); resolve(true); });
    wrap.addEventListener("click", (e) => { if (e.target === wrap) { wrap.remove(); resolve(false); } });
    document.addEventListener("keydown", function onKey(ev){
      if (ev.key === "Escape") { document.removeEventListener("keydown", onKey); wrap.remove(); resolve(false); }
    });
  });
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}
