/**
 * Sarathi Service Worker v3.0 — PWA Offline Cache
 * Caches shell, scheme pages, and static assets for offline use
 */

const CACHE_NAME = "sarathi-v3";
const CACHE_FIRST = [
  "/static/sarathi_voice.js",
  "/static/manifest.json",
  "/",
  "/login",
  "/register",
];
const NETWORK_FIRST = [
  "/api/",
  "/admin",
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(CACHE_FIRST).catch(err => {
        console.warn("[SW] Pre-cache warning:", err);
      });
    })
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Always network-first for API calls, admin, and login
  if (NETWORK_FIRST.some(p => url.pathname.startsWith(p))) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ error: "Offline", offline: true }),
                     { headers: { "Content-Type": "application/json" } })
      )
    );
    return;
  }

  // Cache-first for static assets
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(resp => {
        if (!resp || resp.status !== 200) return resp;
        const clone = resp.clone();
        caches.open(CACHE_NAME).then(cache => {
          // Cache HTML pages and static files
          if (event.request.url.includes("/service/") || event.request.url.includes("/static/")) {
            cache.put(event.request, clone);
          }
        });
        return resp;
      }).catch(() => {
        // Offline fallback for HTML pages
        if (event.request.headers.get("accept")?.includes("text/html")) {
          return caches.match("/") || new Response("<h1>Sarathi — Offline</h1><p>Please connect to the internet.</p>",
            { headers: { "Content-Type": "text/html" } });
        }
      });
    })
  );
});
