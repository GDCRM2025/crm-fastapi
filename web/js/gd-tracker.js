(function () {
  "use strict";
  var script = document.currentScript;
  var siteCode = (script && script.dataset.siteCode) || window.GD_SITE_CODE || "";
  var apiBase = ((script && script.dataset.apiBase) || "").replace(/\/$/, "");
  if (!siteCode) return;

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === "x" ? r : (r & 3 | 8)).toString(16);
    });
  }
  function stored(store, key) {
    var value = store.getItem(key);
    if (!value) { value = uuid(); store.setItem(key, value); }
    return value;
  }
  var visitorId = stored(localStorage, "gd_visitor_id");
  var sessionId = stored(sessionStorage, "gd_session_id");
  document.cookie = "gd_visitor_id=" + visitorId + "; Path=/; Max-Age=31536000; SameSite=Lax; Secure";
  document.cookie = "gd_session_id=" + sessionId + "; Path=/; SameSite=Lax; Secure";
  var params = new URLSearchParams(location.search);
  var utm = {};
  ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"].forEach(function (key) {
    var value = params.get(key); if (value) utm[key] = value;
  });
  function send(path, body) {
    return fetch(apiBase + path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), keepalive: true}).catch(function () {});
  }
  send("/collect/v1/session", {
    site_code: siteCode, visitor_id: visitorId, session_id: sessionId,
    landing_url: location.href, referrer: document.referrer || null, utm: utm
  }).then(function () {
    return send("/collect/v1/event", {
      site_code: siteCode, session_id: sessionId, event_id: uuid(), event_type: "session_start", page_url: location.href
    });
  }).then(function () {
    return send("/collect/v1/event", {
      site_code: siteCode, session_id: sessionId, event_id: uuid(), event_type: "page_view", page_url: location.href
    });
  });
  document.addEventListener("click", function (event) {
    var link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || !/^(https?:\/\/)?(wa\.me|api\.whatsapp\.com)|whatsapp:/i.test(link.href)) return;
    var clickId = uuid();
    try { sessionStorage.setItem("gd_whatsapp_click_id", clickId); } catch (_) {}
    try {
      var target = new URL(link.href, location.href);
      var currentText = target.searchParams.get("text") || "";
      if (!/Ref GD:/i.test(currentText)) {
        target.searchParams.set("text", currentText + (currentText ? "\n" : "") + "Ref GD:" + clickId);
        link.href = target.toString();
      }
    } catch (_) {}
    send("/collect/v1/event", {
      site_code: siteCode, session_id: sessionId, event_id: clickId,
      event_type: "click_whatsapp", page_url: location.href, metadata: {click_id: clickId}
    });
  }, true);
  document.querySelectorAll("form").forEach(function (form) {
    var input = form.querySelector('input[name="gd_session_id"]');
    if (!input) { input = document.createElement("input"); input.type = "hidden"; input.name = "gd_session_id"; form.appendChild(input); }
    input.value = sessionId;
  });
  window.GDTracker = {visitorId: visitorId, sessionId: sessionId};
})();
