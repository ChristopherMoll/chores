// Progressive enhancement: task actions and management still work as normal forms.
(function () {
  "use strict";
  const csrf = document.body.dataset.csrf;
  const root = document.documentElement;
  const query = (selector, scope = document) => scope.querySelector(selector);
  const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  let toastTimer;
  let savingTask = false;
  let keyboardInteraction = false;
  document.addEventListener("keydown", () => { keyboardInteraction = true; });
  document.addEventListener("pointerdown", () => { keyboardInteraction = false; });

  function setting(key, fallback) {
    try { return localStorage.getItem("molechores-" + key) || fallback; } catch (_) { return fallback; }
  }
  function saveSetting(key, value) {
    try { localStorage.setItem("molechores-" + key, value); return true; } catch (_) { return false; }
  }
  function paint(scope = document) {
    all("[data-color]", scope).forEach(el => el.style.setProperty("--c", el.dataset.color));
    all("[data-pct]", scope).forEach(el => { el.style.width = el.dataset.pct + "%"; });
  }
  function toast(message, kind = "", action = null, actionLabel = "Undo") {
    const box = query("#toast");
    if (!box) return;
    clearTimeout(toastTimer);
    query("#toast-message").textContent = message;
    const button = query("#toast-action");
    button.hidden = !action;
    button.textContent = actionLabel;
    button.onclick = action;
    box.className = "toast show " + kind;
    if (!action) toastTimer = setTimeout(() => { box.className = "toast"; }, 6000);
  }
  query("#toast-close")?.addEventListener("click", () => { query("#toast").className = "toast"; clearTimeout(toastTimer); });

  function applyAppearance() {
    const appearance = setting("appearance", "system");
    if (appearance === "light" || appearance === "dark") root.dataset.theme = appearance;
    else delete root.dataset.theme;
    root.dataset.quiet = setting("quiet", "false");
    all('meta[name="theme-color"]').forEach((meta, index) => {
      const dark = appearance === "system" ? index === 1 : appearance === "dark";
      meta.setAttribute("content", dark ? "#191f1c" : "#f4f1ea");
    });
  }
  applyAppearance();
  const appearance = query("#appearance");
  const quiet = query("#quiet-celebrations");
  if (appearance) appearance.value = setting("appearance", "system");
  if (quiet) quiet.checked = setting("quiet", "false") === "true";
  [appearance, quiet].filter(Boolean).forEach(control => control.addEventListener("change", () => {
    const saved = saveSetting(control === appearance ? "appearance" : "quiet", control === appearance ? control.value : String(control.checked));
    applyAppearance();
    query("#preference-status").textContent = saved ? "Saved on this device." : "This browser couldn't save your preference.";
  }));

  function connectionStatus() {
    const banner = query("#connection-status");
    if (banner) banner.hidden = navigator.onLine;
  }
  connectionStatus();
  window.addEventListener("online", connectionStatus);
  window.addEventListener("offline", connectionStatus);

  async function refreshTogether() {
    if (!query("#household-summary")) return;
    try {
      const response = await fetch(location.href, { cache: "no-store", credentials: "same-origin" });
      if (!response.ok || response.redirected) throw new Error("refresh");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      ["household-summary", "household-feed"].forEach(id => {
        const next = page.getElementById(id);
        const current = document.getElementById(id);
        if (next && current) { current.replaceWith(next); paint(next); }
      });
      const nextCount = page.querySelector("#volunteer-section > summary .pill");
      const count = query("#volunteer-section > summary .pill");
      if (nextCount && count) count.textContent = nextCount.textContent;
    } catch (_) {
      // Task status is already confirmed. The background refresh is retried on return.
      needsRefresh = true;
    }
  }

  async function setTask(url, desired, taskId, period) {
    if (savingTask) return;
    if (!navigator.onLine) { toast("You're offline. Reconnect, then try again.", "error"); return; }
    savingTask = true;
    const row = query('[data-task="' + taskId + '"]');
    const nextIndex = row ? all(".tasks > .task:not(.done)").indexOf(row) : 0;
    const expandedIds = all(".task-body[open]").map(el => el.closest(".task").dataset.task);
    const completedOpen = !!query(".completed-group[open]");
    row?.classList.add("saving");
    all(".tick").forEach(button => { button.dataset.wasDisabled = String(button.disabled); button.disabled = true; });
    const data = new FormData();
    data.set("done", desired ? "1" : "0");
    if (period) data.set("period", period);
    data.set("view", query(".dashboard")?.dataset.view || "today");
    data.set("return_to", location.pathname + location.search);
    data.set("surface", query("#volunteer-region") ? "together" : "tasks");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, { method: "POST", body: data, headers: { "Accept": "application/json", "X-CSRF-Token": csrf }, credentials: "same-origin", signal: controller.signal });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.ok) {
        const error = new Error(response.status === 400 || response.redirected ? "Your session may have expired. Refresh the page and try again." : result?.error || "Couldn't save that. Please try again.");
        error.refresh = response.status === 400 || response.status === 409 || response.redirected;
        error.noRetry = response.status === 403;
        throw error;
      }
      const taskRegion = query("#tasks-region");
      if (taskRegion) {
        taskRegion.innerHTML = result.tasks_html;
        paint(taskRegion);
        if (completedOpen) query(".completed-group", taskRegion)?.setAttribute("open", "");
        expandedIds.forEach(id => query('[data-task="' + id + '"] .task-body', taskRegion)?.setAttribute("open", ""));
      }
      const volunteerRegion = query("#volunteer-region");
      if (volunteerRegion) { volunteerRegion.innerHTML = result.volunteer_html; paint(volunteerRegion); }
      Object.entries(result.counts).forEach(([key, count]) => all('[data-view-count="' + key + '"]').forEach(el => { el.textContent = count; }));
      const mole = query("#hero-mole");
      const lede = query("#hero-lede");
      const allDone = result.today.total > 0 && result.today.done === result.today.total;
      if (mole) mole.src = allDone ? mole.dataset.allDone : mole.dataset.working;
      if (lede) lede.textContent = allDone ? "Today's list is taken care of." : result.today.total ? "A little at a time." : "A little breathing room today.";
      if (result.done) {
        const ownCompletion = result.can_undo;
        const message = ownCompletion ? (allDone && taskRegion ? "Today's list is taken care of. Enjoy the space you made." : "One more thing taken care of. +" + result.points + " points.") : result.by + " already took care of this. Thank you both.";
        toast(message, allDone && taskRegion ? "win" : "", ownCompletion ? () => setTask(url, false, taskId, period) : null);
      } else {
        toast("Task restored to your list.");
      }
      if (keyboardInteraction) {
        const buttons = all(".tasks > .task:not(.done) .tick:not(:disabled)");
        const next = buttons[Math.max(0, Math.min(nextIndex, buttons.length - 1))];
        (next || query("#toast-action:not([hidden])") || query("#toast-close"))?.focus({ preventScroll: true });
      }
      await refreshTogether();
    } catch (error) {
      const retry = error.refresh ? () => location.reload() : error.noRetry ? null : () => setTask(url, desired, taskId, period);
      toast(error.name === "AbortError" || error instanceof TypeError ? "Couldn't confirm the save. It's safe to try again." : error.message, "error", retry, error.refresh ? "Refresh list" : "Try again");
    } finally {
      clearTimeout(timeout);
      savingTask = false;
      row?.classList.remove("saving");
      all(".tick[data-was-disabled]").forEach(button => { button.disabled = button.dataset.wasDisabled === "true"; delete button.dataset.wasDisabled; });
    }
  }
  document.addEventListener("submit", event => {
    const form = event.target;
    if (!form.classList.contains("tick-form")) return;
    event.preventDefault();
    setTask(form.action, query('[name="done"]', form).value === "1", form.closest(".task").dataset.task, query('[name="period"]', form).value);
  });

  document.addEventListener("click", event => {
    const destructive = event.target.closest("[data-confirm]");
    if (destructive && !window.confirm(destructive.dataset.confirm)) event.preventDefault();
    all("details.menu[open], details.tools-menu[open]").forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });

  function setupForms(scope = document) {
    all("select[data-due-switch]", scope).forEach(select => {
      const form = select.closest("form");
      const sync = () => {
        all("[data-due-for]", form).forEach(el => { el.hidden = el.dataset.dueFor !== select.value; });
        const hoursLabel = query("[data-hours-label]", form);
        if (hoursLabel) hoursLabel.textContent = select.value === "once" ? "Estimated total hours" : "Estimated hours per month";
      };
      select.addEventListener("change", sync);
      sync();
    });
    all("[data-track-changes]", scope).forEach(form => {
      form.addEventListener("input", () => form.classList.add("dirty"));
      form.addEventListener("change", () => form.classList.add("dirty"));
      form.addEventListener("submit", () => form.classList.remove("dirty"));
    });
  }
  setupForms();
  const unit = query("select[name=unit]");
  const atTime = query("[data-at-time]");
  if (unit && atTime) {
    const sync = () => { atTime.hidden = unit.value !== "days"; };
    unit.addEventListener("change", sync); sync();
  }
  window.addEventListener("beforeunload", event => {
    if (query("form.dirty")) { event.preventDefault(); event.returnValue = ""; }
  });

  // Keep workload previews based on all tasks, even when the table is filtered.
  const deal = query("form.deal");
  if (deal) {
    const rows = all("[data-grid-row]", deal);
    const fields = row => all("input[name], select[name]", row).filter(input => input.type !== "hidden");
    const value = input => input.type === "checkbox" ? input.checked : input.value;
    const initial = new Map(rows.map(row => [row, new Map(fields(row).map(input => [input, value(input)]))]));
    const loads = JSON.parse(deal.dataset.loads);
    const save = query("[data-grid-save]", deal);
    save.disabled = true;
    query("[data-bulk-tools]", deal).hidden = false;
    const state = (row, original) => {
      const tid = row.dataset.taskId;
      const read = name => { const input = query('[name="' + name + '_' + tid + '"]', row); return original ? initial.get(row).get(input) : value(input); };
      return { owner: read("owner"), frequency: read("frequency"), hours: Number(read("hours")) || 0, active: read("active") };
    };
    function updateDeal() {
      let changed = 0;
      const totals = Object.fromEntries(Object.entries(loads).map(([name, load]) => [name, load.total]));
      function account(item, direction) {
        if (!item.active || item.frequency === "once") return;
        Object.keys(totals).forEach(name => { if (item.owner === name || item.owner === "shared") totals[name] += direction * item.hours * (item.owner === "shared" ? 0.5 : 1); });
      }
      rows.forEach(row => {
        const dirty = fields(row).some(input => value(input) !== initial.get(row).get(input));
        row.classList.toggle("row-dirty", dirty);
        if (dirty) changed++;
        const current = state(row, false);
        row.classList.toggle("off", !current.active);
        query(".owner-sel", row).dataset.owner = current.owner;
        account(state(row, true), -1); account(current, 1);
      });
      Object.entries(totals).forEach(([name, total]) => { const el = query('[data-load-for="' + name + '"]'); if (el) el.textContent = Math.max(0, total).toLocaleString(undefined, { maximumFractionDigits: 1 }); });
      query("[data-load-caption]").textContent = changed ? "Preview after saving · estimated hours per month" : "Estimated hours per month · half of shared work each";
      deal.classList.toggle("dirty", changed > 0);
      query("[data-dirty-note]", deal).textContent = changed ? changed + " task" + (changed === 1 ? "" : "s") + " with unsaved changes" : "All changes saved.";
      save.disabled = changed === 0;
      query("[data-discard]", deal).hidden = changed === 0;
      const selected = all("[data-row-select]:checked", deal).length;
      query("[data-selected-count]", deal).textContent = selected + " selected";
      query("[data-apply-owner]", deal).disabled = selected === 0 || !query("[data-bulk-owner]", deal).value;
      const selectAll = query("[data-select-all]", deal);
      selectAll.checked = rows.length > 0 && selected === rows.length;
      selectAll.indeterminate = selected > 0 && selected < rows.length;
    }
    deal.addEventListener("input", updateDeal);
    deal.addEventListener("change", updateDeal);
    deal.addEventListener("submit", () => deal.classList.remove("dirty"));
    query("[data-select-all]", deal).addEventListener("change", event => { all("[data-row-select]", deal).forEach(input => { input.checked = event.target.checked; }); updateDeal(); });
    query("[data-apply-owner]", deal).addEventListener("click", () => { rows.filter(row => query("[data-row-select]", row).checked).forEach(row => { query(".owner-sel", row).value = query("[data-bulk-owner]", deal).value; }); updateDeal(); });
    query("[data-discard]", deal).addEventListener("click", () => { initial.forEach(inputs => inputs.forEach((original, input) => { if (input.type === "checkbox") input.checked = original; else input.value = original; })); updateDeal(); });
    updateDeal();
  }

  // A full-page editor is always available. Wide desktops can open it beside the table.
  const editor = query("#task-editor");
  let editorController;
  function lockGrid(locked) {
    if (!deal) return;
    all("input, select, button", deal).forEach(control => {
      if (locked) {
        control.dataset.editorDisabled = String(control.disabled);
        control.disabled = true;
      } else if ("editorDisabled" in control.dataset) {
        control.disabled = control.dataset.editorDisabled === "true";
        delete control.dataset.editorDisabled;
      }
    });
  }
  function closeEditor() {
    if (query("form.dirty", editor) && !window.confirm("Discard the unsaved changes in this task?")) return false;
    editorController?.abort();
    lockGrid(false);
    query("[data-task-editor-body]", editor).replaceChildren();
    editor.hidden = true;
    query(".management-workspace").classList.remove("editor-open");
    const link = query(".editor-selected [data-task-editor]");
    all(".editor-selected").forEach(row => row.classList.remove("editor-selected"));
    if (link && keyboardInteraction) link.focus();
    return true;
  }
  if (editor) {
    query("[data-close-editor]").addEventListener("click", closeEditor);
    document.addEventListener("click", async event => {
      const cancel = event.target.closest("[data-cancel-editor]");
      if (cancel && editor.contains(cancel)) { event.preventDefault(); closeEditor(); return; }
      const link = event.target.closest("[data-task-editor]");
      if (!link || !window.matchMedia("(min-width:1250px)").matches || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
      event.preventDefault();
      if (deal.classList.contains("dirty")) { toast("Save or discard the table changes before editing task details."); return; }
      if (!closeEditor()) return;
      editorController = new AbortController();
      try {
        const response = await fetch(link.href, { signal: editorController.signal, credentials: "same-origin" });
        if (!response.ok || response.redirected) { location.assign(link.href); return; }
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        const content = query("[data-editor-content]", page);
        if (!content) { location.assign(link.href); return; }
        query("[data-task-editor-body]", editor).replaceChildren(content);
        editor.hidden = false;
        query(".management-workspace").classList.add("editor-open");
        lockGrid(true);
        link.closest("tr").classList.add("editor-selected");
        setupForms(editor); paint(editor);
        if (keyboardInteraction) query("[data-close-editor]").focus({ preventScroll: true });
      } catch (error) { if (error.name !== "AbortError") location.assign(link.href); }
    });
    document.addEventListener("keydown", event => { if (event.key === "Escape" && !editor.hidden) closeEditor(); });
  }

  const installed = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const installNudge = query("[data-install-nudge]");
  if (installNudge) installNudge.hidden = installed || !/iPhone|iPad/.test(navigator.userAgent) || setting("install-dismissed", "false") === "true";
  query("[data-dismiss-install]")?.addEventListener("click", () => { installNudge.hidden = true; saveSetting("install-dismissed", "true"); });
  const installedMessage = query("[data-installed-message]");
  if (installedMessage) installedMessage.hidden = !installed;

  function revealLinkedTask() {
    const match = location.hash.match(/^#task-(\d+)$/);
    if (!match) return;
    const task = document.getElementById("task-" + match[1]);
    if (!task) {
      // Old notifications used a dashboard anchor. Route them to the correct list.
      if (query(".dashboard")) location.replace("/tasks/" + match[1]);
      return;
    }
    let parent = task.parentElement;
    while (parent) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
    query(".task-body", task)?.setAttribute("open", "");
    task.scrollIntoView({ block: "center", behavior: "instant" });
  }
  revealLinkedTask();
  window.addEventListener("hashchange", revealLinkedTask);

  // Re-read state after returning to the installed app; never discard unsaved edits.
  let hiddenAt = 0;
  let needsRefresh = false;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { hiddenAt = Date.now(); return; }
    if ((needsRefresh || (hiddenAt && Date.now() - hiddenAt > 30000)) && navigator.onLine && !query("form.dirty") && !savingTask) location.reload();
  });
  window.addEventListener("pageshow", event => { if (event.persisted && !query("form.dirty") && navigator.onLine) location.reload(); });

  const pushCard = query("#push-card");
  if (pushCard) setupPush(pushCard);
  function setupPush(card) {
    const status = query("#push-status");
    const on = query("#push-on");
    const off = query("#push-off");
    const test = query("#push-test");
    card.hidden = false;
    if (/iPhone|iPad/.test(navigator.userAgent) && !installed) {
      status.textContent = "For reminders on this iPhone, add MoleChores to your Home Screen and open it from its icon first.";
      return;
    }
    if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
      status.textContent = "This browser can't receive notifications. Your tasks are still here whenever you need them.";
      return;
    }
    const post = (url, body) => fetch(url, { method: "POST", headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrf }, credentials: "same-origin", body: JSON.stringify(body || {}) });
    const keyBytes = b64 => Uint8Array.from(atob(b64.replace(/-/g, "+").replace(/_/g, "/")), char => char.charCodeAt(0));
    const worker = navigator.serviceWorker.register("/sw.js").then(() => navigator.serviceWorker.ready);
    let busy = false;
    async function render() {
      const reg = await worker;
      const sub = await reg.pushManager.getSubscription();
      const enabled = !!sub && Notification.permission === "granted";
      on.hidden = enabled || Notification.permission === "denied";
      off.hidden = test.hidden = !enabled;
      if (enabled) {
        const response = await post("/push/subscribe", sub.toJSON());
        status.textContent = response.ok ? "This device is ready for the reminders you choose." : "This device couldn't connect to your reminders. Please try again.";
      } else if (Notification.permission === "denied") status.textContent = "Notifications are blocked. You can enable them for MoleChores in your device's Settings.";
      else status.textContent = "Enable notifications, then choose Remind me on a task. You're in charge of every nudge.";
    }
    on.addEventListener("click", async () => {
      if (busy) return; busy = true; on.disabled = true;
      try {
        // Keep the permission request directly inside this user gesture for iOS.
        const permission = await Notification.requestPermission();
        if (permission === "granted") {
          const reg = await worker;
          const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(card.dataset.vapid) });
          if (!(await post("/push/subscribe", sub.toJSON())).ok) throw new Error("subscribe");
          toast("Notifications are ready when you need them.");
        }
      } catch (_) { toast("Couldn't enable notifications. Please try again.", "error"); }
      finally { busy = false; on.disabled = false; render().catch(() => { status.textContent = "Couldn't check your notification settings. Please reconnect and try again."; }); }
    });
    off.addEventListener("click", async () => {
      if (busy) return; busy = true; off.disabled = true;
      try {
        const reg = await worker;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          if (!(await post("/push/unsubscribe", { endpoint: sub.endpoint })).ok) throw new Error("unsubscribe");
          await sub.unsubscribe();
        }
        toast("Notifications turned off on this device.");
      } catch (_) { toast("Couldn't turn off notifications. Please try again.", "error"); }
      finally { busy = false; off.disabled = false; render().catch(() => {}); }
    });
    test.addEventListener("click", async () => {
      test.disabled = true;
      try { const response = await post("/push/test"); const result = await response.json(); toast(result.ok ? "Test reminder sent." : "Couldn't send the test. Please try again.", result.ok ? "" : "error"); }
      catch (_) { toast("Couldn't send the test. Please reconnect and try again.", "error"); }
      finally { test.disabled = false; }
    });
    render().catch(() => { status.textContent = "Notifications aren't available right now. Your task list is ready to use."; });
  }
  paint();
  const navigation = query(".app-nav");
  if (navigation) {
    const sizeNavigation = () => {
      const fixed = getComputedStyle(navigation).position === "fixed";
      navigation.classList.toggle("large-text", fixed && parseFloat(getComputedStyle(root).fontSize) > 24);
      root.style.setProperty("--nav-height", fixed ? Math.ceil(navigation.getBoundingClientRect().height) + "px" : "0px");
    };
    sizeNavigation();
    if ("ResizeObserver" in window) new ResizeObserver(sizeNavigation).observe(navigation);
    window.addEventListener("resize", sizeNavigation);
  }
  requestAnimationFrame(() => document.body.classList.add("ready"));
})();
