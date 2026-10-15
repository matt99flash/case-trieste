// Uso offline: ogni file della dashboard (pagina, dati, libreria della mappa) si scarica sempre dalla rete quando c'è
// connessione e se ne tiene una copia; senza connessione si usa l'ultima copia salvata.
const CACHE = "case-trieste-v1";
const SHELL = ["./", "index.html", "app.js", "style.css", "manifest.json", "icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// dashboard e libreria della mappa; non le tessere della mappa né le foto (troppe)
const keep = url => url.origin === self.location.origin || url.hostname === "cdnjs.cloudflare.com";

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (!keep(url)) return;
  // la chiave senza "?t=..." (usato per saltare la cache di GitHub Pages)
  const key = url.origin === self.location.origin ? url.origin + url.pathname : req.url;
  e.respondWith(fetch(req).then(res => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(key, copy));
    }
    return res;
  }).catch(() => caches.match(key).then(hit => hit || (req.mode === "navigate" ? caches.match("./") : Response.error()))));
});
