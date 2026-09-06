/* Offline and installable.
 *
 * Two caches on purpose: the app shell is stamped with the build so a deploy
 * replaces it, while Pyodide is stamped with its own version so a deploy does
 * NOT make everyone re-download several megabytes of unchanged WASM.
 */
const BUILD = "__BUILD__";
const SHELL = "marksman-shell-" + BUILD;
const RUNTIME = "marksman-runtime-v0.28.2";
const ASSETS = ["./", "index.html", "boot.js", "manifest.webmanifest",
                "icon.png", "icon-192.png", "icon-512.png", "marksman.zip"];

// cache:"reload" matters: GitHub Pages serves HTML with max-age=600, so a
// plain addAll would happily cache the copy the browser already had and a
// deploy would take ten minutes to show up.
self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL)
    .then(c => c.addAll(ASSETS.map(u => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys
      .filter(k => k !== SHELL && k !== RUNTIME)
      .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  const runtime = url.host === "cdn.jsdelivr.net";
  if (!runtime && url.origin !== location.origin) return;
  // A link into a tab ("...#log?x=1") is still the one page, so navigations
  // match without their query string.
  const opts = e.request.mode === "navigate" ? { ignoreSearch: true } : undefined;
  e.respondWith(
    caches.match(e.request, opts).then(hit => hit || fetch(e.request).then(res => {
      if (res.ok || res.type === "opaque") {
        const copy = res.clone();
        caches.open(runtime ? RUNTIME : SHELL).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match("index.html"))));
});
