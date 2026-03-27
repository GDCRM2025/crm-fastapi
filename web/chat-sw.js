/* GD Chat - minimal service worker (installable PWA) */
const CACHE_NAME = "gd-chat-shell-v2";
const SHELL_URLS = [
  "./views/chat.html",
  "./styles.css",
  "./chat.manifest.json",
  "./pwa/gd-128.png",
  "./pwa/gd-192.png",
  "./pwa/gd-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => null)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => (k !== CACHE_NAME ? caches.delete(k) : Promise.resolve(true))))
    ).catch(() => null)
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Never cache API calls.
  if (url.pathname.includes("/crm/chat/") || url.pathname.includes("/chat/") || url.pathname.includes("/crm/auth/")) {
    return;
  }

  // Navigation: network-first, fallback cache.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => null);
          return res;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match("./views/chat.html")))
    );
    return;
  }

  // Static assets: cache-first, fallback network.
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).catch(() => cached))
  );
});
