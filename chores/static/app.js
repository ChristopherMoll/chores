// Progressive enhancement only: every action also works as a plain form post.
(function () {
  "use strict";
  const csrf = document.body.dataset.csrf;

  function paint(root) {
    root.querySelectorAll("[data-color]").forEach(el => el.style.setProperty("--c", el.dataset.color));
    root.querySelectorAll("[data-pct]").forEach(el => { el.style.width = el.dataset.pct + "%"; });
    root.querySelectorAll("[data-h]").forEach(el => { el.style.height = Math.max(3, +el.dataset.h) + "%"; });
  }

  let toastTimer;
  function toast(msg, kind) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.className = "toast show " + (kind || "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast"; }, 2600);
  }

  function recount(section) {
    if (!section) return;
    const items = section.querySelectorAll("ul.tasks > li.task");
    const done = section.querySelectorAll("ul.tasks > li.task.done").length;
    const pct = items.length ? Math.round(100 * done / items.length) : 0;
    const n = section.querySelector(".n-done");
    if (n) n.textContent = done;
    const bar = section.querySelector(".bar > span");
    if (bar) { bar.dataset.pct = pct; bar.style.width = pct + "%"; }
    return { done, total: items.length };
  }

  document.addEventListener("submit", async (ev) => {
    const form = ev.target;
    if (!form.classList.contains("tick-form")) return;
    ev.preventDefault();
    const li = form.closest("li.task");
    const btn = form.querySelector("button");
    btn.disabled = true;
    try {
      const res = await fetch(form.action, {
        method: "POST",
        headers: { "Accept": "application/json", "X-CSRF-Token": csrf },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({ ok: false, error: "Something went wrong." }));
      if (!data.ok) { toast(data.error || "Couldn't save that.", "error"); return; }
      li.classList.toggle("done", data.done);
      li.classList.add("pop");
      setTimeout(() => li.classList.remove("pop"), 400);
      const meta = li.querySelector(".task-meta");
      let tag = meta.querySelector(".doneby");
      if (data.done && !tag) {
        tag = document.createElement("span");
        tag.className = "doneby";
        tag.textContent = "✓ " + data.by;
        meta.appendChild(tag);
      } else if (!data.done && tag) {
        tag.remove();
      }
      btn.title = data.done ? "Undo" : "Cross off";
      const section = li.closest(".period");
      const c = recount(section);
      if (data.done) {
        if (section && section.dataset.period === "daily" && c && c.done === c.total) {
          toast("Clean sweep! Everything for today is done 🧹", "win");
        } else {
          toast("+" + data.points + " pts", "ok");
        }
      } else {
        toast("Undone", "");
      }
    } catch (e) {
      form.submit();   // network hiccup: fall back to a normal post
    } finally {
      btn.disabled = false;
    }
  });

  // Confirm destructive buttons.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-confirm]");
    if (b && !window.confirm(b.dataset.confirm)) ev.preventDefault();
  });

  // Deal grid: colour owner selects and flag unsaved changes.
  const deal = document.querySelector("form.deal");
  if (deal) {
    deal.addEventListener("change", (ev) => {
      deal.classList.add("dirty");
      if (ev.target.classList.contains("owner-sel")) ev.target.dataset.owner = ev.target.value;
    });
    deal.addEventListener("submit", () => deal.classList.remove("dirty"));
    window.addEventListener("beforeunload", (ev) => {
      if (deal.classList.contains("dirty")) { ev.preventDefault(); ev.returnValue = ""; }
    });
  }

  // Close the account menu when clicking elsewhere.
  document.addEventListener("click", (ev) => {
    document.querySelectorAll("details.menu[open]").forEach(d => { if (!d.contains(ev.target)) d.open = false; });
  });

  // Task form: show only the due field that applies to the chosen frequency.
  const freqSelect = document.querySelector("select[data-due-switch]");
  if (freqSelect) {
    const syncDue = () => document.querySelectorAll("[data-due-for]").forEach(el => { el.hidden = el.dataset.dueFor !== freqSelect.value; });
    freqSelect.addEventListener("change", syncDue);
    syncDue();
  }

  // Reminder form: a time of day only makes sense for "days before".
  const unitSelect = document.querySelector("select[name=unit]");
  const atTime = document.querySelector("[data-at-time]");
  if (unitSelect && atTime) {
    const syncUnit = () => { atTime.hidden = unitSelect.value !== "days"; };
    unitSelect.addEventListener("change", syncUnit);
    syncUnit();
  }

  // Push notifications: the "Notifications" card on your own dashboard.
  const pushCard = document.getElementById("push-card");
  if (pushCard) setupPush(pushCard);

  function setupPush(card) {
    const status = document.getElementById("push-status");
    const on = document.getElementById("push-on");
    const off = document.getElementById("push-off");
    const test = document.getElementById("push-test");
    card.hidden = false;

    if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
      const iPhone = /iPhone|iPad/.test(navigator.userAgent);
      status.textContent = iPhone
        ? "To get reminders on this iPhone: tap Share, then Add to Home Screen, then open chores from its icon and log in there. Needs iOS 16.4 or later."
        : "This browser can't receive push notifications.";
      return;
    }

    const post = (url, body) => fetch(url, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrf },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    });
    const keyBytes = (b64) => {
      const raw = atob(b64.replace(/-/g, "+").replace(/_/g, "/"));
      return Uint8Array.from(raw, c => c.charCodeAt(0));
    };
    const worker = navigator.serviceWorker.register("/sw.js").then(() => navigator.serviceWorker.ready);

    async function render() {
      const reg = await worker;
      const sub = await reg.pushManager.getSubscription();
      const enabled = !!sub && Notification.permission === "granted";
      on.hidden = enabled || Notification.permission === "denied";
      off.hidden = test.hidden = !enabled;
      if (enabled) {
        status.textContent = "This device will get your reminders.";
        post("/push/subscribe", sub.toJSON()).catch(() => {});   // keeps the server's copy current
      } else if (Notification.permission === "denied") {
        status.textContent = "Notifications are blocked. Turn them on for chores in your device's Settings.";
      } else {
        status.textContent = "Turn this on to get the reminders you set with \"Nag me\" on this device.";
      }
    }

    on.addEventListener("click", async () => {
      on.disabled = true;
      try {
        // iOS only shows the prompt if this runs directly from the tap.
        const permission = await Notification.requestPermission();
        if (permission === "granted") {
          const reg = await worker;
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: keyBytes(card.dataset.vapid),
          });
          const res = await post("/push/subscribe", sub.toJSON());
          if (!res.ok) throw new Error("server refused");
          toast("Notifications on", "ok");
        }
      } catch (e) {
        toast("Couldn't turn on notifications.", "error");
      } finally {
        on.disabled = false;
        render();
      }
    });

    off.addEventListener("click", async () => {
      const reg = await worker;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await post("/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
        await sub.unsubscribe();
      }
      toast("Notifications off", "");
      render();
    });

    test.addEventListener("click", async () => {
      const res = await post("/push/test").catch(() => null);
      const data = res ? await res.json().catch(() => ({})) : {};
      toast(data.ok ? "Test sent" : "Couldn't send the test.", data.ok ? "ok" : "error");
    });

    render().catch(() => { status.textContent = "Notifications aren't available right now."; });
  }

  paint(document);
  requestAnimationFrame(() => document.body.classList.add("ready"));
})();
