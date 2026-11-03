// 版本号变了会强制换新缓存；策略改为"网络优先，离线才用缓存"
const CACHE = "books-v5";
const SHELL = ["/", "/style.css", "/app.js", "/manifest.json",
               "/icon-180.png", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // 接口永远走网络
  if (url.pathname.startsWith("/api/")) return;
  // 其余：网络优先 → 成功就顺手更新缓存 → 失败才回退缓存
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp && resp.status === 200 && e.request.method === "GET") {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/")))
  );
});
