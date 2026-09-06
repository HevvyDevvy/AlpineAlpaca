const CACHE_NAME = "alpine-alpaca-v1";
const APP_SHELL = [
  "/static/styles.css",
  "/static/script.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/images/alpaca-hero.png",
  "/static/images/alpaca-face-sm.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
});

// Network-first for pages (so trading data is always fresh); cache-first
// for the static app shell. Never cache POST requests — this app changes
// real account state on POST and must never replay a stale request.
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  if (APP_SHELL.some((path) => event.request.url.endsWith(path))) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  } else {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  }
});
