/* GreenDiamond App (PWA shell) - minimal service worker */
const CACHE_NAME = "gd-staff-shell-v4";
const SHELL_URLS = [
  "./views/staff.html",
  "./views/staff_ops.html",
  "./styles.css",
  "./staff.manifest.json",
  "./pwa/gd-128.png",
  "./pwa/gd-192.png",
  "./pwa/gd-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => null));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.map((k) => (k !== CACHE_NAME ? caches.delete(k) : Promise.resolve(true)))))
      .catch(() => null)
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Never cache API calls or auth.
  if (url.pathname.includes("/crm/") && (url.pathname.includes("/auth/") || url.pathname.includes("/chat/") || url.pathname.includes("/notifications/"))) {
    return;
  }

  // Navigation: network-first, fallback cache.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          // Nunca cachear 404/500 (evita quedar pegado con {"detail":"Not Found"} en iOS/Safari).
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => null);
          }
          return res;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match("./views/staff.html")))
    );
    return;
  }

  // Static assets: cache-first.
  event.respondWith(caches.match(req).then((cached) => cached || fetch(req).catch(() => cached)));
});

// Web Push notifications
self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let data = {};
      try {
        data = event.data ? event.data.json() : {};
      } catch (_) {
        try {
          data = { body: String(event.data && event.data.text ? event.data.text() : "") };
        } catch (_) {
          data = {};
        }
      }
      const title = (data.title || "GreenDiamond").toString();
      const body = (data.body || "").toString();
      const url = (data.url || "/crm/web/views/staff.html").toString();
      const tag = (data.tag || "gd").toString();
      await self.registration.showNotification(title, {
        body,
        tag,
        data: { url },
        icon: "./pwa/gd-192.png",
        badge: "./pwa/gd-128.png",
      });
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification && event.notification.data && event.notification.data.url) || "/crm/web/views/staff.html";
  event.waitUntil(
    (async () => {
      const allClients = await clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const c of allClients) {
        try {
          if ("focus" in c) {
            await c.focus();
            try {
              c.navigate(url);
            } catch (_) {}
            return;
          }
        } catch (_) {}
      }
      try {
        await clients.openWindow(url);
      } catch (_) {}
    })()
  );
});
