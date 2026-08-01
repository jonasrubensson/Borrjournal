/* Service worker. Två uppgifter: ta emot push och öppna rätt vy vid klick.
   Medvetet ingen offline-cache av API-svar - fel data i fält är värre än ingen data. */
// Versionen sätts in av servern när filen levereras, så att en ny version
// får en egen cache och den gamla städas bort automatiskt.
const VERSION = "__VERSION__";
const SHELL = `borrjournal-shell-${VERSION}`;
const SHELL_FILES = ["/"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Låter sidan be en väntande service worker att ta över direkt
self.addEventListener("message", (event) => {
  if (event.data === "ta-over") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then((hit) => hit || caches.match("/")))
  );
});

self.addEventListener("push", (event) => {
  let data = { title: "Borrjournal", body: "Du har en påminnelse", url: "/#/paminnelser" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (_) {
    if (event.data) data.body = event.data.text();
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      tag: data.tag || "borrjournal",
      renotify: true,
      data: { url: data.url || "/#/paminnelser" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/#/paminnelser";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
