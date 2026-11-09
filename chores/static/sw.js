// Service worker: shows each push as a notification and opens the right page when it's tapped.
// iOS revokes push permission if a push doesn't show a notification, so always show one.
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* fall through to the default text */ }
  event.waitUntil(self.registration.showNotification(data.title || "chores", {
    body: data.body || "",
    tag: data.tag,
    icon: "/static/icon-192.png",
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data.url, self.location.origin).href;
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (open.length) {
      const client = await open[0].focus();
      if (client.navigate) await client.navigate(url);
      return;
    }
    await self.clients.openWindow(url);
  })());
});
