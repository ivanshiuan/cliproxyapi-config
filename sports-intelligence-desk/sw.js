/* SID Terminal — offline cache (PWA) */
const CACHE = "sid-v1";
const ASSETS = [
  "./", "./index.html", "./manifest.webmanifest",
  "./css/terminal.css", "./js/data.js", "./js/engine.js", "./js/app.js",
];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) =>
    Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
