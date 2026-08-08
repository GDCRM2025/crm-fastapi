// ES5-compatible script (iOS/Safari old): no async/await, no arrows, no template literals.
try{var el=document.querySelector("#log"); if(el) el.textContent="JS OK · rrhh_sgjo_marcacion.js";}catch(_e){}

function _ensurePhonePrefix(v){
  v = String(v || "").trim();
  if (!v) return "+56";
  // Normaliza: si escribió sin '+', agrega.
  if (v[0] !== "+") {
    if (v.slice(0,2) === "56") return "+" + v;
    return "+56" + v;
  }
  // Si escribió solo '+', completa.
  if (v === "+") return "+56";
  return v;
}

    var API_BASE = (function () {
      try {
        var h = String(location.hostname || "").toLowerCase();
        var isLocal = h === "localhost" || h === "127.0.0.1";
        if (isLocal) return "";
        var p = String(location.pathname || "");
        return p.indexOf("/crm/") === 0 || p === "/crm" ? "/crm" : "";
      } catch (e) {
        return "";
      }
    })();

    function qs(sel, root) {
      return (root || document).querySelector(sel);
    }

    function log(s) {
      var el = qs("#log");
      if (!el) return;
      el.textContent = String(s || "").trim() || "—";
    }

    function openEnrollModal() {
      try {
        var m = qs("#enrollModal");
        if (!m) return;
        var suggested = getDeviceName();
        if (!suggested) {
          try {
            var ua = String(navigator.userAgent || "");
            if (/iphone|ipad|ipod/i.test(ua)) suggested = "iPhone";
            else if (/android/i.test(ua)) suggested = "Android";
            else suggested = "PC";
          } catch (e1) { suggested = "PC"; }
        }
        qs("#enrollDeviceName").value = suggested;
        // Prefill teléfono con +56 para reducir fricción.
        try { qs("#enrollPhone").value = "+56"; } catch (_eP0) { qs("#enrollPhone").value = ""; }
        // Preselección: si es móvil => teléfono, si no => PC.
        var kind = "PC";
        try {
          var ua2 = String(navigator.userAgent || "");
          if (/iphone|ipad|ipod|android/i.test(ua2)) kind = "PHONE";
        } catch (e2) {}
        var radios = document.querySelectorAll('input[name="enrollKind"]');
        for (var i = 0; i < radios.length; i++) {
          radios[i].checked = (radios[i].value === kind);
        }
        renderEnrollKind();
        m.style.display = "flex";
      } catch (e3) {}
    }

    function closeEnrollModal() {
      try { var m = qs("#enrollModal"); if (m) m.style.display = "none"; } catch (e) {}
    }

    function getEnrollKind() {
      try {
        var sel = document.querySelector('input[name="enrollKind"]:checked');
        return sel ? String(sel.value || "PC") : "PC";
      } catch (e) { return "PC"; }
    }

    function renderEnrollKind() {
      try {
        var kind = getEnrollKind();
        var row = qs("#enrollPhoneRow");
        if (row) row.style.display = (kind === "PHONE") ? "flex" : "none";
        var btn = qs("#enrollDo");
        if (btn) btn.textContent = (kind === "PHONE") ? "Enrolar teléfono" : "Enrolar PC";
      } catch (e) {}
    }

    qs("#enrollCancel").addEventListener("click", function () { closeEnrollModal(); });
    qs("#enrollModal").addEventListener("click", function (ev) {
      try { if (ev && ev.target && ev.target.id === "enrollModal") closeEnrollModal(); } catch (e) {}
    });
    (function () {
      var radios = document.querySelectorAll('input[name="enrollKind"]');
      for (var i = 0; i < radios.length; i++) radios[i].addEventListener("change", renderEnrollKind);
    })();

    // Mostrar errores JS en pantalla (Safari/iOS a veces “silencia” y parece que botones no funcionan).
    try {
      window.addEventListener("error", function (ev) {
        try {
          var msg = ev && ev.message ? ev.message : "Error";
          var file = ev && ev.filename ? "\n" + ev.filename + ":" + (ev.lineno || "") : "";
          log("Error JS: " + msg + file);
        } catch (e) {}
      });
      window.addEventListener("unhandledrejection", function (ev) {
        try {
          var r = ev ? ev.reason : null;
          var msg2 = r && r.message ? r.message : r != null ? String(r) : "Error";
          log("Promise error: " + msg2);
        } catch (e2) {}
      });
    } catch (e3) {}

    function getCookie(name) {
      try {
        var esc = name.replace(/[-[\]{}()*+?.,\\^$|#\s]/g, "\\$&");
        var m = String(document.cookie || "").match(new RegExp("(^|;\\s*)" + esc + "=([^;]*)"));
        return m ? decodeURIComponent(m[2] || "") : "";
      } catch (e) {
        return "";
      }
    }

    function setCookie(name, value, maxAgeSeconds, path) {
      try {
        var p = path || "/crm";
        var age = Number(maxAgeSeconds || 0) || 0;
        var v = encodeURIComponent(String(value || ""));
        // iOS/Safari + iframe: requerimos SameSite=None; Secure (en HTTPS) para que el cookie no se pierda.
        var isHttps = false;
        try { isHttps = String(location.protocol || "").indexOf("https") === 0; } catch (e0) { isHttps = false; }
        var ss = isHttps ? "None" : "Lax";
        var parts = [name + "=" + v, "path=" + p, "SameSite=" + ss];
        if (age > 0) parts.push("Max-Age=" + String(age));
        if (isHttps) parts.push("Secure");
        document.cookie = parts.join("; ");
      } catch (e) {}
    }

    function setEnrollIntent(on) {
      try {
        if (on) {
          try { localStorage.setItem("sgjo_open_enroll", "1"); } catch (e0) {}
          try { setCookie("sgjo_open_enroll", "1", 10 * 60, "/crm"); } catch (e1) {}
          try { setCookie("sgjo_open_enroll", "1", 10 * 60, "/"); } catch (e2) {}
        } else {
          try { localStorage.removeItem("sgjo_open_enroll"); } catch (e3) {}
          try { document.cookie = "sgjo_open_enroll=; Max-Age=0; path=/"; } catch (e4) {}
          try { document.cookie = "sgjo_open_enroll=; Max-Age=0; path=/crm"; } catch (e5) {}
        }
      } catch (e6) {}
    }

    function getEnrollIntent() {
      try {
        var v = "";
        try { v = String(localStorage.getItem("sgjo_open_enroll") || "").trim(); } catch (e0) { v = ""; }
        if (!v) {
          try { v = String(getCookie("sgjo_open_enroll") || "").trim(); } catch (e1) { v = ""; }
        }
        return v === "1";
      } catch (e2) { return false; }
    }

    function getToken() {
      var t = "";
      try {
        t =
          localStorage.getItem("token") ||
          localStorage.getItem("gd_token") ||
          sessionStorage.getItem("token") ||
          sessionStorage.getItem("gd_token") ||
          "";
      } catch (e) {
        t = "";
      }
      t = String(t || "").trim();
      if (t) return t;
      try {
        var ck = String(getCookie("gd_token") || "").trim();
        if (ck) return ck;
      } catch (e2) {}
      return "";
    }

    function goLogin(punto_code) {
      try {
        var next = punto_code
          ? API_BASE + "/web/views/rrhh_sgjo_marcacion.html?p=" + encodeURIComponent(punto_code)
          : location.pathname + location.search;
        location.href = API_BASE + "/web/login.html?next=" + encodeURIComponent(next);
      } catch (e) {}
    }

    function goLoginEnroll(punto_code) {
      try {
        var next = punto_code
          ? API_BASE + "/web/views/rrhh_sgjo_marcacion.html?p=" + encodeURIComponent(punto_code) + "&enroll=1"
          : (location.pathname + location.search + (String(location.search||"").indexOf("?")>=0 ? "&" : "?") + "enroll=1");
        location.href = API_BASE + "/web/login.html?next=" + encodeURIComponent(next);
      } catch (e) {}
    }

    function ensureToken(punto_code, autoRedirect) {
      var t = getToken();
      if (t) return true;
      var ck = getCookie("gd_token");
      if (ck) {
        try {
          localStorage.setItem("token", ck);
          localStorage.setItem("gd_token", ck);
        } catch (e) {}
        return true;
      }
      log("Debes iniciar sesión para marcar/enrolar. Toca “Iniciar sesión”.");
      if (autoRedirect) {
        try { goLogin(punto_code || ""); } catch (e2) {}
      }
      return false;
    }

    function apiURL(path) {
      if (String(path || "").indexOf("http") === 0) return path;
      var p = String(path || "");
      return API_BASE + (p.charAt(0) === "/" ? "" : "/") + p;
    }

    function apiJSON(path, opt) {
      opt = opt || {};
      var headers = opt.headers || {};
      var t = getToken();
      if (t) headers.Authorization = "Bearer " + t;
      opt.headers = headers;
      return fetch(apiURL(path), opt)
        .then(function (r) {
          return r.text().then(function (txt) {
            var j = null;
            try {
              j = txt ? JSON.parse(txt) : null;
            } catch (e) {
              j = null;
            }
            // 401 => token inválido/expirado: limpiar y reenviar a login.
            if (r.status === 401) {
              try { clearToken(); } catch (e0) {}
              try { goLogin(getPuntoCode() || ""); } catch (e1) {}
              throw new Error("Token inválido. Inicia sesión nuevamente.");
            }
            if (!r.ok) {
              var msg = j && (j.detail || j.error || j.message) ? j.detail || j.error || j.message : txt || "HTTP " + r.status;
              // 403 se usa también para reglas de negocio (dispositivo no enrolado / método no permitido).
              // No debemos tratarlo como token inválido.
              throw new Error(msg);
            }
            return j;
          });
        });
    }

    function clearToken() {
      try { localStorage.removeItem("token"); } catch (e) {}
      try { localStorage.removeItem("gd_token"); } catch (e2) {}
      try { sessionStorage.removeItem("token"); } catch (e3) {}
      try { sessionStorage.removeItem("gd_token"); } catch (e4) {}
      // Best-effort: si no es HttpOnly, lo borramos.
      try { document.cookie = "gd_token=; Max-Age=0; path=/"; } catch (e5) {}
      try { document.cookie = "gd_token=; Max-Age=0; path=/crm"; } catch (e6) {}
    }

    function getDeviceId(autoGenerate) {
      var gen = (autoGenerate === true);
      var id = "";
      // Fallback #0: query param (?did=...) para casos donde storage/cookies estén bloqueados.
      try{
        var href = String(location.href || "");
        var qi = href.indexOf("?");
        if (qi >= 0){
          var q = href.slice(qi+1).split("&");
          for (var kk=0; kk<q.length; kk++){
            var kv = q[kk].split("=");
            if (decodeURIComponent(kv[0]||"") === "did"){
              id = String(decodeURIComponent(kv[1]||"") || "").trim();
              break;
            }
          }
        }
      }catch(_eQ){}
      try {
        // Prioridad cookie -> localStorage (cookie es más estable en iOS/iframes).
        if (!id) id = String(getCookie("sgjo_device_id") || "").trim();
      } catch (e0) { id = ""; }
      if (!id) {
        try { id = localStorage.getItem("sgjo_device_id") || ""; } catch (e) { id = ""; }
      }
      if (!id && gen) {
        try {
          if (window.crypto && typeof window.crypto.randomUUID === "function") id = window.crypto.randomUUID();
          else id = "dev-" + Math.random().toString(16).slice(2) + Date.now();
        } catch (e2) {
          id = "dev-" + Math.random().toString(16).slice(2) + Date.now();
        }
        try {
          localStorage.setItem("sgjo_device_id", id);
        } catch (e3) {}
        // Safari/ITP puede limpiar localStorage: guardamos respaldo en cookie (1 año).
        try { setCookie("sgjo_device_id", id, 365 * 24 * 3600, "/crm"); } catch (e4) {}
        try { setCookie("sgjo_device_id", id, 365 * 24 * 3600, "/"); } catch (e5) {}
      } else if (id) {
        // Sync ambos almacenamientos para evitar re-enrolamientos.
        try { localStorage.setItem("sgjo_device_id", id); } catch (e6) {}
        try { setCookie("sgjo_device_id", id, 365 * 24 * 3600, "/crm"); } catch (e7) {}
        try { setCookie("sgjo_device_id", id, 365 * 24 * 3600, "/"); } catch (e8) {}
      }
      return id;
    }

    function setDidInUrl(did){
      try{
        did = String(did||"").trim();
        if (!did) return;
        var u = new URL(location.href);
        u.searchParams.set("did", did);
        history.replaceState(null, "", u.toString());
      }catch(_){}
    }

    function ensureStableDeviceId(preferExisting){
      // Si storage falla, pedimos un device_id estable al backend (usuario + UA hash).
      return new Promise(function(resolve){
        try{
          var existing = getDeviceId(false);
          if (existing) return resolve(existing);
        }catch(_e0){}
        var pe = (preferExisting === false) ? "0" : "1";
        apiJSON("/rrhh/sgjo/device/issue?prefer_existing=" + pe, { method: "POST" })
          .then(function(out){
            var did = out && out.device_id ? String(out.device_id||"").trim() : "";
            if (did){
              try{ localStorage.setItem("sgjo_device_id", did); }catch(_e1){}
              try{ setCookie("sgjo_device_id", did, 365*24*3600, "/crm"); }catch(_e2){}
              try{ setCookie("sgjo_device_id", did, 365*24*3600, "/"); }catch(_e3){}
              try{ setDidInUrl(did); }catch(_e4){}
              return resolve(did);
            }
            resolve("");
          })
          .catch(function(){ resolve(""); });
      });
    }

    function getDeviceName() {
      var n = "";
      try { n = localStorage.getItem("sgjo_device_name") || ""; } catch (e) { n = ""; }
      if (!n) {
        try { n = String(getCookie("sgjo_device_name") || "").trim(); } catch (e0) { n = ""; }
      }
      n = String(n || "").trim();
      if (n) return n;
      try {
        var ua = String(navigator.userAgent || "");
        if (ua) {
          // recorta para no meter basura enorme
          n = ua.split("(")[0].trim();
        }
      } catch (e2) {}
      return String(n || "").trim();
    }

    function getPuntoCodeFromQuery() {
      try {
        var href = String(location.href || "");
        var i = href.indexOf("?");
        if (i < 0) return "";
        var q = href.slice(i + 1);
        var parts = q.split("&");
        for (var k = 0; k < parts.length; k++) {
          var kv = parts[k].split("=");
          var key = decodeURIComponent(kv[0] || "");
          if (key === "p" || key === "punto") {
            return decodeURIComponent(kv[1] || "").trim().toUpperCase();
          }
        }
      } catch (e) {}
      return "";
    }

    function getEnrollFlag() {
      try {
        var href = String(location.href || "");
        var i = href.indexOf("?");
        if (i < 0) return false;
        var q = href.slice(i + 1);
        var parts = q.split("&");
        for (var k = 0; k < parts.length; k++) {
          var kv = parts[k].split("=");
          var key = decodeURIComponent(kv[0] || "");
          if (key === "enroll") {
            var v = decodeURIComponent(kv[1] || "");
            return String(v || "").trim() === "1";
          }
        }
      } catch (e) {}
      return false;
    }

    function setEnrollFlagInUrl(on) {
      try {
        var u = new URL(location.href);
        if (on) u.searchParams.set("enroll", "1");
        else u.searchParams.delete("enroll");
        history.replaceState(null, "", u.toString());
      } catch (e) {}
    }

    function getAutoFlag() {
      try {
        var u = new URL(location.href);
        var v = String(u.searchParams.get("auto") || "").trim();
        return (v === "1" || v.toLowerCase() === "true" || v.toLowerCase() === "yes");
      } catch (e) {
        return false;
      }
    }

    function getPuntoCode() {
      var q = "";
      try { q = getPuntoCodeFromQuery(); } catch (e) { q = ""; }
      if (q) return q;
      try {
        var sel = qs("#selPunto");
        if (sel && sel.value) return String(sel.value || "").trim().toUpperCase();
      } catch (e2) {}
      return "";
    }

    function getLocation(allowEmpty) {
      return new Promise(function (resolve, reject) {
        var okEmpty = !!allowEmpty;
        if (!navigator.geolocation) {
          if (okEmpty) return resolve({ lat: null, lng: null, accuracy_m: null, denied: true });
          return reject(new Error("Geolocalización no disponible."));
        }
        navigator.geolocation.getCurrentPosition(
          function (pos) {
            resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy_m: pos.coords.accuracy, denied: false });
          },
          function (err) {
            try{
              var code = err && err.code ? Number(err.code) : 0;
              if (code === 1) {
                if (okEmpty) return resolve({ lat: null, lng: null, accuracy_m: null, denied: true });
                reject(new Error("User denied Geolocation. Activa Ubicación para este sitio en Safari/Chrome y recarga."));
                return;
              }
              if (code === 2) {
                if (okEmpty) return resolve({ lat: null, lng: null, accuracy_m: null, denied: true });
                reject(new Error("Ubicación no disponible. Revisa señal/GPS e inténtalo de nuevo."));
                return;
              }
              if (code === 3) {
                if (okEmpty) return resolve({ lat: null, lng: null, accuracy_m: null, denied: true });
                reject(new Error("Timeout obteniendo ubicación. Intenta de nuevo."));
                return;
              }
            }catch(e0){}
            if (okEmpty) return resolve({ lat: null, lng: null, accuracy_m: null, denied: true });
            reject(new Error((err && err.message) ? err.message : "No pude obtener ubicación."));
          },
          { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
        );
      });
    }

    function init() {
      // Debug visible: confirma que el JS está corriendo y cuál versión se cargó.
      try { log("Cargando marcación… v=2026-05-18f"); } catch (_e0) {}
      // iOS/Safari en iframe puede bloquear cookies/storage => termina pidiendo enrolar cada vez.
      // Preferimos abrir en pantalla completa.
      try{
        if (window.top && window.self !== window.top) {
          // Solo sugerimos, no forzamos (algunos navegadores lo bloquean).
          try{
            log("Tip: Si ves que te vuelve a pedir enrolar, toca “Abrir en pantalla completa” (Safari/iOS a veces bloquea storage dentro del CRM).");
            var a = document.createElement("button");
            a.className = "btn";
            a.type = "button";
            a.textContent = "Abrir en pantalla completa";
            a.addEventListener("click", function(){ try{ window.top.location.href = window.location.href; }catch(_){ window.open(window.location.href, "_blank"); } });
            var host = qs("#log"); // debajo del log
            if (host && host.parentNode) host.parentNode.insertBefore(a, host);
          }catch(_e){}
        }
      }catch(_){}

      var punto_code = getPuntoCodeFromQuery();
      // Ajusta href de login/enrolamiento para preservar next incluso si el JS falla parcialmente.
      try{
        var here = String(location.pathname || "") + String(location.search || "");
        if (here.indexOf("/crm") !== 0) here = "/crm" + here;
        var href = API_BASE + "/web/login.html?next=" + encodeURIComponent(here);
        var a1 = qs("#btnLogin"); if (a1) a1.setAttribute("href", href);
        // Enrolar: vuelve con enroll=1 para abrir el modal automáticamente.
        var uH = null;
        try { uH = new URL(location.href); } catch (_eU0) { uH = null; }
        var hereEnroll = here;
        if (uH) {
          try { uH.searchParams.set("enroll","1"); hereEnroll = String(uH.pathname||"") + "?" + String(uH.searchParams||""); } catch (_eU1) {}
        } else {
          hereEnroll = here + (here.indexOf("?")>=0 ? "&" : "?") + "enroll=1";
        }
        var hrefEnroll = API_BASE + "/web/login.html?next=" + encodeURIComponent(hereEnroll);
        var a2 = qs("#btnEnroll"); if (a2) a2.setAttribute("href", hrefEnroll);
      }catch(_eHref){}
      // Si no hay token, redirigimos al login para evitar pantalla “muerta”.
      // Algunos navegadores bloquean storage/cookies y la UI queda sin acción.
      try{
        if (!ensureToken(punto_code || "", false)) {
          try { goLogin(punto_code || ""); } catch (_eL0) {}
        }
      }catch(_eL1){}

      // Si venimos desde login con intención de enrolar, abre el modal de inmediato.
      try{
        if (getEnrollFlag() || getEnrollIntent()) {
          // Si hay token, abrimos; si no, el redirect al login ya se disparó.
          try { openEnrollModal(); } catch (_eEM0) {}
          // Limpia el flag para que no abra siempre.
          try { setEnrollFlagInUrl(false); } catch (_eEM1) {}
          try { setEnrollIntent(false); } catch (_eEM2) {}
        }
      }catch(_eEM3){}

      // Si venimos de enrolar y este device está pending, mostramos “pantalla de espera” (pausa) y evitamos navegar al CRM.
      try{
        ensureStableDeviceId().then(function(didP){
          didP = String(didP||"").trim();
          if (!didP) return;
          apiJSON("/rrhh/sgjo/device/status?device_id=" + encodeURIComponent(didP))
            .then(function(stP){
              try{
                var pending = !!(stP && stP.pending);
                var enrolled = !!(stP && stP.enrolled);
                if (pending && !enrolled){
                  try{ qs("#btnMark").disabled = true; }catch(_eB0){}
                  log("Tu enrolamiento está pendiente de aprobación. Un admin RRHH debe aprobar este dispositivo antes de marcar.\n\nMantén esta pantalla abierta y recarga cuando te avisen.");
                }
              }catch(_eB1){}
            })
            .catch(function(){});
        });
      }catch(_ePend){}
      // UX: si el dispositivo ya está enrolado, no mostramos "Enrolar dispositivo".
      try {
        ensureStableDeviceId().then(function(did0){
          if (!did0) {
            try { qs("#btnEnroll").style.display = ""; } catch (_e0) {}
            // No bloqueamos marcar: el backend puede usar cualquier dispositivo ya aprobado del usuario.
            try { qs("#btnMark").disabled = false; } catch (_e1) {}
            try { qs("#pDev").textContent = "Device: AUTO"; } catch (_e2) {}
            log("No detecté device_id en este navegador. Igual puedes marcar; si te dice 'no enrolado', enrola 1 vez.");
            return;
          }
          apiJSON("/rrhh/sgjo/device/status?device_id=" + encodeURIComponent(did0))
          .then(function (st) {
            try {
              var enrolled = !!(st && st.enrolled);
              var pending = !!(st && st.pending);
              var b = qs("#btnEnroll");
              if (b) b.style.display = enrolled ? "none" : "";
              // Si no está enrolado, dejamos visible el botón Enrolar siempre.
              if (!enrolled) {
                try { if (b) b.disabled = false; } catch (_eX) {}
              }
              // Si está pendiente, bloqueamos marcar para no generar frustración.
              if (pending && !enrolled) {
                try { qs("#btnMark").disabled = true; } catch (e0) {}
                log("Tu enrolamiento está pendiente de aprobación. Un admin RRHH debe aprobar este dispositivo antes de marcar.");
              }
            } catch (e1) {}
          })
          .catch(function () {});
        }).catch(function(){});
      } catch (e2) {}
      apiJSON("/rrhh/sgjo/config")
        .then(function (cfg) {
          var puntos = (cfg && cfg.puntos) ? cfg.puntos : [];
          var sedes = (cfg && cfg.sedes) ? cfg.sedes : [];
          // Si no viene punto por QR, permitimos seleccionar.
          var selP = qs("#selPunto");
          if (selP) {
            if (!punto_code) selP.style.display = "";
            // Poblar options (siempre).
            try { selP.innerHTML = '<option value="">Selecciona punto…</option>'; } catch (e0) {}
            for (var i = 0; i < puntos.length; i++) {
              var c = String(puntos[i].code || "").toUpperCase();
              var nm = String(puntos[i].nombre || c);
              var opt = document.createElement("option");
              opt.value = c;
              opt.textContent = nm + " (" + c + ")";
              selP.appendChild(opt);
            }
            if (punto_code) selP.value = punto_code;
          }

          function paintPunto(code) {
            code = String(code || "").toUpperCase();
            var pt = null;
            for (var k = 0; k < puntos.length; k++) {
              var c2 = String(puntos[k].code || "").toUpperCase();
              if (c2 === code) { pt = puntos[k]; break; }
            }
            var sede = null;
            if (pt) {
              for (var j = 0; j < sedes.length; j++) {
                if (String(sedes[j].id_sede) == String(pt.id_sede)) { sede = sedes[j]; break; }
              }
            }
            qs("#pSede").textContent = "Sede: " + ((sede && sede.nombre) ? sede.nombre : "—");
            qs("#pPunto").textContent = "Punto: " + ((pt && pt.nombre) ? pt.nombre : (code || "—"));
          }

          paintPunto(punto_code || "");
          if (selP) {
            selP.addEventListener("change", function () {
              try { paintPunto(getPuntoCode()); } catch (e1) {}
            });
          }

          // Si el usuario entra sin QR, guiamos pero no bloqueamos.
          if (!punto_code) {
            log("Selecciona un punto para marcar. Si vienes desde el QR, el punto se preselecciona.");
          }
          return apiJSON("/rrhh/sgjo/me");
        })
        .then(function (me) {
          try { window.__ME = me || {}; } catch (e0) {}
          // Si el backend reporta un device aprobado, lo usamos siempre como source-of-truth.
          try{
            var didDefault = String((me && me.device_id_default) ? me.device_id_default : "").trim();
            if (didDefault){
              try{ localStorage.setItem("sgjo_device_id", didDefault); }catch(_e1){}
              try{ setCookie("sgjo_device_id", didDefault, 365*24*3600, "/crm"); }catch(_e2){}
              try{ setCookie("sgjo_device_id", didDefault, 365*24*3600, "/"); }catch(_e3){}
              try{ setDidInUrl(didDefault); }catch(_e4){}
              try{ qs("#pDev").textContent = "Device: " + didDefault; }catch(_e5){}
            } else {
              ensureStableDeviceId().then(function(d0){
                try{ qs("#pDev").textContent = "Device: " + String(d0 || "—"); }catch(_){}
              });
            }
          }catch(_){}
          qs("#pModo").textContent = "Modo: " + String((me && me.modality_today) ? me.modality_today : "—");
          var pol = String((me && me.marcacion_method) ? me.marcacion_method : "BOTH").toUpperCase();
          var can = (me && me.puede_marcar === false) ? "NO" : "SÍ";
          qs("#pPolicy").textContent = "Método: " + pol + " · Puede marcar: " + can;
          // Habilitar/Deshabilitar botón Marcar según reglas mínimas (sin bloquear por UX).
          var allowMark = true;
          if (!(me && me.rut)) {
            log("Tu usuario no tiene RUT en el sistema. Pide a RRHH/Admin que registre tu RUT para poder marcar.");
            allowMark = false;
          }
          var sel = qs("#selMethod");
          if (sel) {
            if (pol === "QR") { sel.value = "QR"; sel.disabled = true; }
            else if (pol === "GPS" || pol === "GEO") { sel.value = "GEO"; sel.disabled = true; }
            else sel.disabled = false;
          }
          // Si no puede marcar por policy RRHH, deshabilitar.
          if (me && me.puede_marcar === false) allowMark = false;
          try { qs("#btnMark").disabled = !allowMark; } catch (e0) {}

          // UX: si ya tiene dispositivo enrolado, ocultamos botón "Enrolar dispositivo".
          try{
            var hasDev = !!(me && (me.has_enrolled_device || me.device_id_default));
            var cnt = Number((me && me.enrolled_devices_count) ? me.enrolled_devices_count : 0) || 0;
            if (hasDev && cnt > 0) {
              try{ qs("#btnEnroll").style.display = "none"; }catch(_e0){}
            } else {
              try{ qs("#btnEnroll").style.display = ""; }catch(_e1){}
            }

            // Si el usuario NO tiene dispositivos enrolados, abrir modal automáticamente (1 vez por sesión).
            // Esto evita depender de query params / cookies que a veces se pierden al volver del login.
            if (!(hasDev && cnt > 0)) {
              try{
                var k = "sgjo_prompt_enroll_once";
                if (sessionStorage.getItem(k) !== "1") {
                  sessionStorage.setItem(k, "1");
                  openEnrollModal();
                }
              }catch(_eAutoEn){}
            }
          }catch(_){}

          if (!allowMark) {
            if (me && me.puede_marcar === false) log("No tienes permiso RRHH para marcar (puede_marcar=false). Contacta a RRHH.");
          } else {
            if (me && (me.has_enrolled_device || me.device_id_default)) {
              log("Listo. Marca Entrada/Salida (QR o GPS según tu permiso RRHH).");
            } else {
              log("Listo. Enrola el dispositivo la primera vez, luego marca (Entrada/Salida + QR o GPS según tu permiso RRHH).");
            }
          }
          // Sugerir tipo IN/OUT según estado del día.
          return apiJSON("/rrhh/sgjo/today");
        })
        .then(function (st) {
          try {
            window.__TIPO = "IN";
            // Si ya tiene IN hoy => sugerir OUT; si no => IN.
            if (st && st.ok && st.has_in && !st.has_out) {
              window.__TIPO = "OUT";
              log("Te falta marcar SALIDA (OUT) hoy. Por defecto te dejo SALIDA sugerida.");
            }
            renderTipo();
          } catch (e) {}
          // Auto-mark (desde CRM): si viene auto=1 y aún no tiene IN hoy, marcamos IN de inmediato.
          try {
            if (getAutoFlag() && st && st.ok && !st.has_in) {
              // Evita loops: 1 intento por día/sesión.
              var kAuto = "sgjo_auto_mark_attempted_" + String((st && st.day) ? st.day : (new Date()).toISOString().slice(0,10));
              try { if (sessionStorage.getItem(kAuto) === "1") return; } catch (_eA0) {}
              try { sessionStorage.setItem(kAuto, "1"); } catch (_eA1) {}

              // Si no hay dispositivo enrolado, NO auto-intentamos.
              var me0 = null;
              try { me0 = window.__ME || null; } catch (_eA2) { me0 = null; }
              var hasDev0 = !!(me0 && (me0.has_enrolled_device || me0.device_id_default));
              var cnt0 = Number((me0 && me0.enrolled_devices_count) ? me0.enrolled_devices_count : 0) || 0;
              if (!(hasDev0 && cnt0 > 0)) {
                // Quita flag auto para no insistir.
                try{
                  var u0 = new URL(location.href);
                  u0.searchParams.delete("auto");
                  history.replaceState(null, "", u0.toString());
                }catch(_eA3){}
                return;
              }
              var punto0 = getPuntoCode();
              if (!punto0) return;
              var btn0 = qs("#btnMark");
              try { if (btn0) btn0.disabled = true; } catch (_e0) {}
              ensureStableDeviceId().then(function (didA) {
                var did = String(didA || "").trim();
                if (!did) did = "AUTO";
                // Preferir QR si el select no está forzado.
                try {
                  var sel0 = qs("#selMethod");
                  if (sel0 && !sel0.disabled) sel0.value = "QR";
                } catch (_e1) {}
                doMarkWithDeviceId(punto0, did, btn0 || { disabled: false });

                // Quita flag auto para evitar reintentos si el usuario recarga.
                try{
                  var u1 = new URL(location.href);
                  u1.searchParams.delete("auto");
                  history.replaceState(null, "", u1.toString());
                }catch(_eA4){}
              });
            }
          } catch (_e2) {}
        })
        .catch(function (e) {
          log("Error: " + (e && e.message ? e.message : e));
        });
    }

    function renderTipo(){
      try{
        var t = String(window.__TIPO || "IN").toUpperCase();
        window.__TIPO = (t === "OUT") ? "OUT" : "IN";
        var pill = qs("#pillTipo");
        var btn = qs("#btnToggleTipo");
        if (pill) pill.textContent = "Tipo sugerido: " + (window.__TIPO === "OUT" ? "Salida (OUT)" : "Entrada (IN)");
        if (btn) btn.textContent = (window.__TIPO === "OUT" ? "Cambiar a entrada" : "Cambiar a salida");
      }catch(e){}
    }

    qs("#btnToggleTipo").addEventListener("click", function(){
      try{
        window.__TIPO = (String(window.__TIPO || "IN").toUpperCase() === "OUT") ? "IN" : "OUT";
        renderTipo();
      }catch(e){}
    });

    qs("#btnLogin").addEventListener("click", function (ev) {
      try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (e0) {}
      var punto_code = getPuntoCode();
      goLogin(punto_code || "");
    });

    qs("#btnEnroll").addEventListener("click", function (ev) {
      try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (e0) {}
      var punto_code = getPuntoCode();
      // Si no hay token, ir a login y volver con intención de enrolar.
      if (!ensureToken(punto_code || "", false)) {
        try { setEnrollIntent(true); } catch (_eI0) {}
        try { goLogin(punto_code || ""); } catch (eL) {}
        return;
      }
      // precargar datos para hints
      apiJSON("/rrhh/sgjo/me")
        .then(function (me) {
          try { window.__ENROLL_ME = me || {}; } catch (e0) {}
          try {
            var tel = String((me && me.telefono_rrhh) ? me.telefono_rrhh : "").trim();
            if (tel) qs("#enrollPhone").value = _ensurePhonePrefix(tel);
          } catch (e1) {}
        })
        .catch(function () { try { window.__ENROLL_ME = {}; } catch (e2) {} })
        .then(function () { openEnrollModal(); });
    });

    qs("#enrollDo").addEventListener("click", function () {
      try {
        var punto_code = getPuntoCode();
        if (!ensureToken(punto_code || "", true)) return;
        var kind = getEnrollKind();
        var device_name = String(qs("#enrollDeviceName").value || "").trim();
        var tel_hint = _ensurePhonePrefix(qs("#enrollPhone").value || "");
        var me = null;
        try { me = window.__ENROLL_ME || null; } catch (e0) { me = null; }
        var rut_hint = String((me && me.rut) ? me.rut : "").trim();

        if (!device_name) { alert("Nombre del dispositivo requerido"); return; }
        if (kind === "PHONE" && !tel_hint) { alert("Teléfono requerido para enrolar teléfono"); return; }
        if (!rut_hint) rut_hint = String(prompt("Ingresa tu RUT (sin puntos, con guion si corresponde):") || "").trim();
        if (!rut_hint) { alert("RUT requerido"); return; }

        // PC: no pedir teléfono (solo si viene ya en RRHH lo enviamos como hint).
        if (kind === "PC") {
          try { localStorage.setItem("sgjo_device_name", device_name); } catch (e1) {}
          // si el usuario escribió algo en teléfono igual lo ignoramos en PC para no confundir
          if (tel_hint && tel_hint.length < 6) tel_hint = "";
        }

        qs("#enrollDo").disabled = true;
        return ensureStableDeviceId(false).then(function(device_id){
          device_id = String(device_id||"").trim();
          if (!device_id) throw new Error("No pude obtener device_id (recarga e intenta nuevamente).");
          return apiJSON("/rrhh/sgjo/device/enroll", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device_id: device_id, device_name: device_name, device_kind: kind, rut: rut_hint, telefono: (kind === "PHONE" ? tel_hint : "") })
          });
        })
        .then(function (out) {
          closeEnrollModal();
          if (out && out.ok && out.already) log("Este dispositivo ya estaba enrolado (aprobado). Ya puedes marcar.");
          else if (out && out.ok) log("Dispositivo enrolado OK.");
          else if (out && out.pending_approval) {
            var rid = out.id_request ? "\nID solicitud: " + out.id_request : "";
            log("Solicitud enviada. Un admin debe aprobar este dispositivo (RRHH)." + rid + "\nLuego vuelve a intentar marcar.");
          } else log("Solicitud enviada. Espera aprobación y recarga.");

          if (kind === "PC") {
            try {
              var wants = confirm("¿Quieres enrolar tu teléfono ahora?\n\nImportante: debes hacerlo desde tu teléfono (escaneando el QR), no desde este PC.");
              if (wants) {
                log("En tu TELÉFONO: escanea el QR del punto y toca “Enrolar dispositivo”.\nPon nombre (ej: iPhone Mauricio) y tu teléfono.\nLuego RRHH debe aprobar y ya podrás marcar desde ese teléfono.");
              }
            } catch (e2) {}
          }
        })
        .catch(function (e) {
          log("No pude enrolar: " + (e && e.message ? e.message : e));
        })
        .then(function () {
          try { qs("#enrollDo").disabled = false; } catch (e3) {}
        });
      } catch (e4) {
        try { qs("#enrollDo").disabled = false; } catch (e5) {}
        log("No pude enrolar: " + (e4 && e4.message ? e4.message : e4));
      }
    });

    function doMarkWithDeviceId(punto_code, device_id, btn) {
      // Para usuarios BOTH: si el navegador niega geolocalización, permitimos fallback a QR.
      var sel = qs("#selMethod");
      var method0 = String((sel && sel.value) ? sel.value : "QR").toUpperCase();
      if (method0 === "GPS") method0 = "GEO";
      var pol0 = "";
      try {
        pol0 = String(((window.__ME || {}).marcacion_method) ? (window.__ME || {}).marcacion_method : "").toUpperCase();
        if (pol0 === "MIXTO") pol0 = "BOTH";
      } catch (e0) { pol0 = ""; }
      // Si el usuario eligió QR, permitimos lat/lng null.
      var allowEmpty = (method0 === "QR");
      getLocation(allowEmpty)
        .then(function (loc) {
          var method = method0;
          var tipo = String(window.__TIPO || "IN").toUpperCase();
          if (loc && loc.denied && method === "QR") {
            log("Nota: no se pudo obtener ubicación (GPS). Marcando por QR igualmente (fallback).");
          }
          var payload = { punto_code: punto_code, device_id: device_id, method: method, lat: (loc ? loc.lat : null), lng: (loc ? loc.lng : null), accuracy_m: (loc ? loc.accuracy_m : null) };
          payload.tipo = (tipo === "OUT") ? "OUT" : "IN";
          return apiJSON("/rrhh/sgjo/marcar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
        })
        .then(function (out) {
          var dist = (out && out.distance_m != null) ? (Number(out.distance_m).toFixed(1) + "m") : "—";
          var wr = (out && out.within_radius === false) ? "\n⚠ Fuera de rango (permitido). Queda marcado para revisión RRHH." : "";
          log("OK · " + ((out && out.tipo) ? out.tipo : "—") + " · " + ((out && out.sede) ? out.sede : "—") +
              "\nDistancia: " + dist +
              "\nFallback: " + ((out && out.used_fallback) ? "sí" : "no") +
              "\nModo: " + ((out && out.modality) ? out.modality : "—") + wr);
        })
        .catch(function (e) {
          var msg = (e && e.message) ? e.message : String(e || "");
          try{
            var m0 = String(msg||"").toLowerCase();
          if (m0.indexOf("dispositivo no enrolado") >= 0) {
            log("Antes de marcar, debes enrolar este dispositivo 1 vez (y esperar aprobación RRHH si aplica).");
            try { openEnrollModal(); } catch (_e0) {}
            return;
          }
          }catch(_e00){}
          try{
            var msgL = String(msg||"").toLowerCase();
            var isDenied = msgL.indexOf("denied") >= 0;
            var isOutOfRange = (msgL.indexOf("fuera de rango") >= 0) || (msgL.indexOf("out of range") >= 0);
            // Para BOTH: si GEO falla por permisos o por rango, hacemos fallback a QR (sin lat/lng).
            if ((isDenied && method0 === "GEO") || (isOutOfRange && method0 === "GEO")) {
              if (pol0 === "BOTH" || !pol0) {
                try { if (sel) sel.value = "QR"; } catch (_e2) {}
                var tipo2 = String(window.__TIPO || "IN").toUpperCase();
                var payload2 = { punto_code: punto_code, device_id: device_id, method: "QR", lat: null, lng: null, accuracy_m: null };
                payload2.tipo = (tipo2 === "OUT") ? "OUT" : "IN";
                return apiJSON("/rrhh/sgjo/marcar", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(payload2)
                }).then(function(out2){
                  var dist2 = (out2 && out2.distance_m != null) ? (Number(out2.distance_m).toFixed(1) + "m") : "—";
                  log("OK · " + ((out2 && out2.tipo) ? out2.tipo : "—") + " · " + ((out2 && out2.sede) ? out2.sede : "—") +
                      "\nDistancia: " + dist2 +
                      "\nFallback: " + ((out2 && out2.used_fallback) ? "sí" : "no") +
                      "\nModo: " + ((out2 && out2.modality) ? out2.modality : "—"));
                  // Notifica al CRM para volver al dashboard u otra vista.
                  try { window.parent && window.parent.postMessage({ type: "sgjo_marked", tipo: (out2 && out2.tipo) ? out2.tipo : null }, "*"); } catch (_p0) {}
                  return;
                }).catch(function(e2){
                  msg = (e2 && e2.message) ? e2.message : String(e2 || "");
                  log("Error: " + msg);
                  return;
                });
              }
            }
          }catch(_e1){}
          log("Error: " + msg);
        })
        .then(function () { try { btn.disabled = false; } catch (_e9) {} });
    }

    qs("#btnMark").addEventListener("click", function () {
      var punto_code = getPuntoCode();
      if (!punto_code) { log("Debes seleccionar un punto antes de marcar (o entrar desde el QR con ?p=...)."); return; }
      if (!ensureToken(punto_code, true)) return;
      var btn = qs("#btnMark");
      btn.disabled = true;
      var device_id = "";
      try { device_id = getDeviceId(false); } catch (_e0) { device_id = ""; }
      if (!device_id){
        ensureStableDeviceId().then(function(d1){
          device_id = String(d1||"").trim();
          // Si aun así no hay device_id, marcamos con AUTO para que el backend use el último dispositivo aprobado.
          doMarkWithDeviceId(punto_code, device_id || "AUTO", btn);
        });
        return;
      }
      doMarkWithDeviceId(punto_code, device_id, btn);
    });

    init();
