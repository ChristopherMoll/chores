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

  paint(document);
  requestAnimationFrame(() => document.body.classList.add("ready"));
})();
