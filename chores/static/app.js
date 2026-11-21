// Progressive enhancement: task actions and management still work as normal forms.
(function () {
  "use strict";
  const csrf = document.body.dataset.csrf;
  const root = document.documentElement;
  const query = (selector, scope = document) => scope.querySelector(selector);
  const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  let toastTimer;
  let savingTask = false;
  let taskRevision = 0;
  let lastLiveCheck = Date.now();
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
    window.MoleMotion?.preferencesChanged();
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

  async function refreshScheduledRegions(background = false) {
    const revision = taskRevision;
    const response = await fetch(location.href, {cache: "no-store", credentials: "same-origin"});
    if (!response.ok || response.redirected) { needsRefresh = true; return; }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    if (background && (savingTask || revision !== taskRevision || query(".search-panel[aria-busy=true]") || query("form.dirty") || document.activeElement?.closest("form"))) {
      needsRefresh = true;
      return;
    }
    const motion = window.MoleMotion?.capture();
    const expanded = all(".task-body[open]").map(el => el.closest(".task").dataset.rowKey);
    const closedRuns = all("[data-run-group]:not([open])").map(el => el.dataset.runGroup);
    const openDisclosures = all("[data-disclosure][open]").map(el => el.dataset.disclosure);
    const focused = document.activeElement;
    const focusRow = focused?.closest(".task")?.dataset.rowKey;
    const focusDisclosure = focused?.closest("[data-disclosure]")?.dataset.disclosure;
    ["tasks-region", "volunteer-region", "shared-region", "task-detail-region", "sequence-run-region", "sequence-heading", "sequence-actions", "occurrence-region", "household-summary", "household-feed"].forEach(id => {
      const current = document.getElementById(id), next = page.getElementById(id);
      if (current && next) { current.replaceWith(next); paint(next); setupForms(next); }
    });
    expanded.forEach(id => query('[data-row-key="' + id + '"] .task-body')?.setAttribute("open", ""));
    closedRuns.forEach(id => all('[data-run-group="' + id + '"]').forEach(el => el.removeAttribute("open")));
    openDisclosures.forEach(key => query('[data-disclosure="' + key + '"]')?.setAttribute("open", ""));
    all("[data-view-count]", page).forEach(next => all('[data-view-count="' + next.dataset.viewCount + '"]').forEach(el => { el.textContent = next.textContent; }));
    const hero = query("#hero-mole", page), lede = query("#hero-lede", page);
    if (hero) window.MoleMotion?.react(false, hero.dataset.pose === "celebrate");
    if (lede && query("#hero-lede")) query("#hero-lede").textContent = lede.textContent;
    if (focusRow) document.getElementById(focusRow)?.querySelector(".task-body > summary")?.focus({preventScroll:true});
    else if (focusDisclosure) query('[data-disclosure="' + focusDisclosure + '"] > summary')?.focus({preventScroll:true});
    window.MoleMotion?.restore(motion);
  }

  async function setTask(url, desired, taskId, period, occurrenceId = null, early = false, completionId = null, cover = false, credit = "") {
    if (savingTask) return;
    if (!navigator.onLine) { toast("You're offline. Reconnect, then try again.", "error"); return; }
    savingTask = true;
    taskRevision++;
    const row = completionId ? query('[data-completion-id="' + completionId + '"]') : document.getElementById(occurrenceId ? 'occurrence-' + occurrenceId : 'task-' + taskId);
    const nextIndex = row ? all(".tasks > .task:not(.done)").indexOf(row) : 0;
    const expandedIds = all(".task-body[open]").map(el => el.closest(".task").dataset.rowKey);
    const openDisclosures = all("[data-disclosure][open]").map(el => el.dataset.disclosure);
    const closedRuns = all("[data-run-group]:not([open])").map(el => el.dataset.runGroup);
    row?.classList.add("saving");
    row?.setAttribute("aria-busy", "true");
    all(".tick-form button").forEach(button => { button.dataset.wasDisabled = String(button.disabled); button.disabled = true; });
    const data = new FormData();
    data.set("done", desired ? "1" : "0");
    if (period) data.set("period", period);
    if (occurrenceId) data.set("occurrence_id", occurrenceId);
    if (early) data.set("early", "1");
    if (completionId) data.set("completion_id", completionId);
    if (cover) data.set("cover", "1");
    if (credit) data.set("credit", credit);
    data.set("board_user", query(".dashboard")?.dataset.username || "");
    data.set("view", query(".dashboard")?.dataset.view || "today");
    data.set("q", query(".dashboard")?.dataset.q || "");
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
      await window.MoleMotion?.confirm(row, result.done);
      const motion = window.MoleMotion?.capture(row);
      const taskRegion = query("#tasks-region");
      if (taskRegion) {
        taskRegion.innerHTML = result.tasks_html;
        paint(taskRegion);
      }
      const volunteerRegion = query("#volunteer-region");
      if (volunteerRegion) { volunteerRegion.innerHTML = result.volunteer_html; paint(volunteerRegion); }
      if (query("#sequence-run-region, #occurrence-region, #task-detail-region, #shared-region")) await refreshScheduledRegions();
      else { if (taskRegion) setupForms(taskRegion); if (volunteerRegion) setupForms(volunteerRegion); }
      openDisclosures.forEach(key => query('[data-disclosure="' + key + '"]')?.setAttribute("open", ""));
      closedRuns.forEach(id => all('[data-run-group="' + id + '"]').forEach(el => el.removeAttribute("open")));
      expandedIds.forEach(id => query('[data-row-key="' + id + '"] .task-body')?.setAttribute("open", ""));
      Object.entries(result.counts).forEach(([key, count]) => all('[data-view-count="' + key + '"]').forEach(el => { el.textContent = count; }));
      const lede = query("#hero-lede");
      const allDone = result.today.total > 0 && result.today.done === result.today.total;
      window.MoleMotion?.restore(motion);
      window.MoleMotion?.react(result.done && result.can_undo, allDone);
      if (lede) lede.textContent = allDone ? "Today's list is taken care of." : result.today.total ? "One thing at a time." : "A little breathing room today.";
      if (result.done) {
        const ownCompletion = result.can_undo;
        let message = ownCompletion ? (allDone && taskRegion ? "Today's list is taken care of. Enjoy the space you made." : "One more thing taken care of. +" + result.points + " points.") : result.by + " already took care of this. Thank you both.";
        if (result.credited) message = result.thanks_sent === false ? result.credited + " gets the credit. Your thank-you will be waiting in the app." : result.credited + " gets the credit, and your thank-you is on its way.";
        if (result.next_step) message += " " + result.next_step.name + " will be ready " + result.next_step.at + ".";
        toast(message, allDone && taskRegion ? "win" : "", ownCompletion ? () => setTask(url, false, taskId, period, result.occurrence_id || occurrenceId, false, result.completion_id) : null);
      } else {
        toast("Task restored to your list.");
      }
      if (keyboardInteraction) {
        const buttons = all(".tasks > .task:not(.done) .tick:not(:disabled)").filter(button => button.getClientRects().length);
        const next = buttons[Math.max(0, Math.min(nextIndex, buttons.length - 1))];
        const restored = !result.done && document.getElementById(result.occurrence_id ? 'occurrence-' + result.occurrence_id : 'task-' + taskId)?.querySelector(".tick:not(:disabled)");
        const focus = restored || next || query("#toast-action:not([hidden])") || query("#toast-close");
        if (window.MoleMotion) window.MoleMotion.focusControl(focus); else focus?.focus({preventScroll: true});
      }
      if (!query("#shared-region")) await refreshTogether();
    } catch (error) {
      const retry = error.refresh ? () => location.reload() : error.noRetry ? null : () => setTask(url, desired, taskId, period, occurrenceId, early, completionId, cover, credit);
      toast(error.name === "AbortError" || error instanceof TypeError ? "Couldn't confirm the save. It's safe to try again." : error.message, "error", retry, error.refresh ? "Refresh list" : "Try again");
    } finally {
      clearTimeout(timeout);
      savingTask = false;
      row?.classList.remove("saving");
      row?.removeAttribute("aria-busy");
      all(".tick-form button[data-was-disabled]").forEach(button => { button.disabled = button.dataset.wasDisabled === "true"; delete button.dataset.wasDisabled; });
    }
  }
  document.addEventListener("submit", event => {
    const form = event.target;
    if (!form.classList.contains("tick-form")) return;
    event.preventDefault();
    setTask(form.action, query('[name="done"]', form).value === "1", form.closest(".task").dataset.task, query('[name="period"]', form).value, query('[name="occurrence_id"]', form)?.value, query('[name="early"]', form)?.value === "1", query('[name="completion_id"]', form)?.value, query('[name="cover"]', form)?.value === "1", query('[name="credit"]', form)?.value || "");
  });

  // "Say thank you" on something your partner already crossed off.
  document.addEventListener("submit", async event => {
    const form = event.target;
    if (!form.classList.contains("thanks-form")) return;
    event.preventDefault();
    if (!navigator.onLine) { toast("You're offline. Reconnect, then try again.", "error"); return; }
    const button = query("button", form);
    button.disabled = true;
    try {
      const response = await fetch(form.action, { method: "POST", headers: { "Accept": "application/json", "X-CSRF-Token": csrf }, credentials: "same-origin" });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.ok) throw new Error(result?.error || "Couldn't send your thank-you. Refresh the page and try again.");
      const note = document.createElement("p");
      note.className = "thanks-line muted small";
      note.textContent = "You said thank you.";
      form.replaceWith(note);
      toast(result.message);
    } catch (error) {
      button.disabled = false;
      toast(error instanceof TypeError ? "Couldn't send your thank-you. Check your connection and try again." : error.message, "error");
    }
  });

  document.addEventListener("click", event => {
    const destructive = event.target.closest("[data-confirm]");
    if (destructive && !window.confirm(destructive.dataset.confirm)) event.preventDefault();
    all("details.menu[open], details.tools-menu[open], details.list-picker[open]").forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });

  // A horizontal swipe reveals shortcuts; vertical scrolling never completes a task.
  let taskGesture, suppressTaskClickUntil = 0;
  document.addEventListener("pointerdown", event => {
    const summary = event.target.closest("summary[data-task-detail]");
    if (event.pointerType !== "touch" || !event.isPrimary || !summary || !matchMedia("(max-width:999px)").matches) return;
    taskGesture = {summary, id:event.pointerId, x:event.clientX, y:event.clientY};
  }, {passive:true});
  document.addEventListener("pointerup", event => {
    const gesture = taskGesture;
    taskGesture = null;
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (Math.abs(dx) < 65 || Math.abs(dy) > 30 || Math.abs(dx) < Math.abs(dy) * 2) return;
    suppressTaskClickUntil = Date.now() + 400;
    const content = gesture.summary.closest(".task-content");
    let shortcuts = query(".task-shortcuts", content);
    if (dx > 0) { shortcuts?.remove(); return; }
    all(".task-shortcuts").forEach(el => { if (el !== shortcuts) el.remove(); });
    if (shortcuts) return;
    shortcuts = document.createElement("div");
    shortcuts.className = "task-detail-actions task-shortcuts";
    shortcuts.setAttribute("role", "group"); shortcuts.setAttribute("aria-label", "Quick task actions");
    const details = document.createElement("a");
    details.className = "btn"; details.href = gesture.summary.dataset.taskDetail; details.textContent = "Details";
    shortcuts.appendChild(details);
    const reminder = query('.task-detail-actions a[href*="/remind"]', content);
    if (reminder) { const link = reminder.cloneNode(true); link.className = "btn"; shortcuts.appendChild(link); }
    const close = document.createElement("button");
    close.type = "button"; close.className = "text-action"; close.textContent = "Close";
    close.setAttribute("aria-label", "Hide quick task actions"); close.addEventListener("click", () => shortcuts.remove());
    shortcuts.appendChild(close); content.appendChild(shortcuts);
  }, {passive:true});
  document.addEventListener("pointercancel", () => { taskGesture = null; }, {passive:true});

  // iPhone details have their own screen; desktop keeps quick inline disclosures.
  document.addEventListener("click", event => {
    const summary = event.target.closest("summary[data-task-detail]");
    if (!summary || !matchMedia("(max-width:999px)").matches || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (Date.now() < suppressTaskClickUntil) return;
    location.assign(summary.dataset.taskDetail);
  });

  // Native dialog provides focus trapping, Escape, and a focused keyboard entry flow.
  const capture = query("#capture-dialog");
  if (capture && typeof capture.showModal === "function") {
    capture.appendChild(query("#quick-capture"));
    query(".capture-launch").hidden = false;
    query("[data-close-capture]").hidden = false;
    query("[data-open-capture]").addEventListener("click", () => {
      capture.showModal(); document.body.classList.add("sheet-open");
      query("#quick-task", capture).focus();
    });
    query("[data-close-capture]").addEventListener("click", () => capture.close());
    capture.addEventListener("click", event => { if (event.target === capture) capture.close(); });
    capture.addEventListener("close", () => {
      document.body.classList.remove("sheet-open");
      query("form", capture).classList.remove("dirty");
      query("[data-open-capture]").focus({preventScroll:true});
    });
  }

  // Reminder presets stay beside the task instead of sending iPhone users away.
  let reminderSheet, reminderUrl, reminderBusy = false;
  function renderReminder(page, message = "") {
    const content = query("[data-reminder-content]", page);
    if (!content) throw new Error("reminder");
    reminderSheet.replaceChildren(content);
    query("[data-close-reminder]", content).hidden = false;
    query("[data-sheet-status]", content).textContent = message;
    all("form", content).forEach(form => { if (!form.getAttribute("action")) form.action = reminderUrl; });
    setupForms(content); paint(content);
    const card = query("#push-card", content);
    if (card) setupPush(card);
  }
  document.addEventListener("click", async event => {
    if (event.target.closest("[data-close-reminder]")) { reminderSheet?.close(); return; }
    const link = event.target.closest('.task-detail-actions a[href*="/remind"]');
    if (!link || !matchMedia("(max-width:999px)").matches || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (typeof HTMLDialogElement === "undefined" || !HTMLDialogElement.prototype.showModal) return;
    event.preventDefault();
    if (reminderBusy) return;
    reminderBusy = true;
    reminderUrl = link.href;
    link.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(reminderUrl, {credentials:"same-origin", cache:"no-store"});
      if (!response.ok || response.redirected) throw new Error("reminder");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!reminderSheet) {
        reminderSheet = document.createElement("dialog");
        reminderSheet.className = "capture-sheet reminder-sheet";
        reminderSheet.setAttribute("aria-label", "Remind me");
        document.body.appendChild(reminderSheet);
        reminderSheet.addEventListener("close", () => {
          document.body.classList.remove("sheet-open");
          if (needsRefresh && navigator.onLine && !query("form.dirty")) {
            needsRefresh = false;
            refreshScheduledRegions(true).catch(() => { needsRefresh = true; });
          }
        });
        reminderSheet.addEventListener("click", e => { if (e.target === reminderSheet) reminderSheet.close(); });
      }
      renderReminder(page);
      reminderSheet.showModal(); document.body.classList.add("sheet-open");
    } catch (_) { location.assign(reminderUrl); }
    finally { reminderBusy = false; link.removeAttribute("aria-busy"); }
  });
  document.addEventListener("submit", async event => {
    const form = event.target;
    if (!reminderSheet?.contains(form)) return;
    event.preventDefault();
    if (reminderBusy) return;
    const status = query("[data-sheet-status]", reminderSheet);
    if (!navigator.onLine) { status.textContent = "You're offline. Reconnect, then try again."; return; }
    reminderBusy = true;
    const data = new FormData(form);
    if (event.submitter?.name) data.set(event.submitter.name, event.submitter.value);
    all("button", form).forEach(button => { button.disabled = true; });
    status.textContent = "Saving…";
    try {
      const response = await fetch(form.action, {method:"POST", body:data, credentials:"same-origin"});
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!response.ok) throw new Error("reminder");
      renderReminder(page, all(".flash", page).map(el => el.textContent.trim()).join(" "));
      needsRefresh = true;
    } catch (_) {
      status.textContent = "Couldn't confirm the save. Reconnect and try again; duplicate reminders won't be added.";
      all("button", form).forEach(button => { button.disabled = false; });
    } finally { reminderBusy = false; }
  });

  // Keep each destination's place, including a return from task details or editing.
  const placePrefix = "molechores-place-" + document.body.dataset.userId + "-";
  const tabKey = placePrefix + "tasks-url";
  function rememberPlace() {
    if (!document.body.dataset.userId) return;
    try {
      const key = location.pathname + location.search;
      sessionStorage.setItem(placePrefix + key, JSON.stringify({
        y: scrollY,
        open: all("[data-disclosure][open]").map(el => el.dataset.disclosure),
        closedRuns: all("[data-run-group]:not([open])").map(el => el.dataset.runGroup),
        overview: !!query("#household-overview[open]")
      }));
      if (query('.dashboard[data-own="true"]')) sessionStorage.setItem(tabKey, key);
    } catch (_) { /* Storage is optional in private browsing. */ }
  }
  function restorePlace() {
    if (location.hash || !document.body.dataset.userId) return;
    try {
      const place = JSON.parse(sessionStorage.getItem(placePrefix + location.pathname + location.search) || "null");
      if (!place) return;
      for (const key of place.open || []) all("[data-disclosure]").filter(el => el.dataset.disclosure === key).forEach(el => { el.open = true; });
      for (const key of place.closedRuns || []) all("[data-run-group]").filter(el => el.dataset.runGroup === key).forEach(el => { el.open = false; });
      if (place.overview && query("#household-overview")) query("#household-overview").open = true;
      scrollTo({top:place.y || 0, behavior:"instant"});
    } catch (_) { /* A fresh view remains usable. */ }
  }
  window.addEventListener("pagehide", rememberPlace);
  document.addEventListener("click", event => {
    if (!event.target.closest("a, summary[data-task-detail]")) return;
    rememberPlace();
    const link = event.target.closest('.app-nav a[href="/"]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    try {
      const saved = new URL(sessionStorage.getItem(tabKey) || "/", location.origin);
      if (saved.origin === location.origin && (saved.pathname === "/" || saved.pathname.startsWith("/people/"))) link.href = saved.pathname + saved.search;
    } catch (_) { /* Use the default Today link. */ }
  }, {capture:true});
  requestAnimationFrame(restorePlace);

  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-copy-from]");
    if (!button) return;
    const source = document.getElementById(button.dataset.copyFrom);
    const value = source?.value ?? source?.textContent;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value.trim());
      toast("Copied. Ready to paste into Shortcuts.");
    } catch (_) {
      if (source?.select) { source.focus(); source.select(); }
      else if (source) { const range = document.createRange(); range.selectNodeContents(source); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); }
      toast("Selected for copying. Use your device's Copy command.");
    }
  });

  function setupForms(scope = document) {
    all("[data-at-time]", scope).forEach(at => {
      const unit = query('select[name="unit"]', at.closest("form"));
      if (!unit) return;
      const sync = () => { at.hidden = unit.value !== "days"; };
      unit.addEventListener("change", sync); sync();
    });
    all("[data-effort-switch]", scope).forEach(select => {
      const sync = () => all("[data-effort-for]", select.closest("form")).forEach(el => { el.hidden = el.dataset.effortFor !== select.value; });
      select.addEventListener("change", sync); sync();
    });
    all("select[data-due-switch]", scope).forEach(select => {
      const form = select.closest("form");
      const sync = () => {
        all("[data-due-for]", form).forEach(el => { el.hidden = !el.dataset.dueFor.split(" ").includes(select.value); });
        const hoursLabel = query("[data-hours-label]", form);
        if (hoursLabel) hoursLabel.textContent = select.value === "once" ? "Estimated total hours" : "Estimated hours per month";
      };
      select.addEventListener("change", sync);
      sync();
    });
    all(".sequence-editor", scope).forEach(editor => {
      const setupStep = step => {
        const choice = query("[data-step-choice]", step);
        const sync = () => {
          query("[data-step-name]", step).hidden = !!choice.value;
          query("[data-step-effort]", step).hidden = !!choice.value;
        };
        choice.addEventListener("change", sync); sync();
        const remove = query("[data-remove-step]", step);
        remove.hidden = false;
        remove.addEventListener("click", () => { step.closest("form").classList.add("dirty"); step.remove(); });
      };
      all("[data-sequence-step]", editor).forEach(setupStep);
      const add = query("[data-add-step]", editor);
      add.hidden = false;
      add.addEventListener("click", () => {
        const list = query("[data-sequence-steps]", editor);
        if (all("[data-sequence-step]", list).length >= 20) { toast("A sequence can have up to 20 follow-ups."); return; }
        const fragment = query("[data-sequence-template]", editor).content.cloneNode(true);
        list.appendChild(fragment);
        setupStep(list.lastElementChild);
        query("[name=step_name]", list.lastElementChild).focus();
        editor.closest("form").classList.add("dirty");
      });
    });
    all("[data-track-changes]", scope).forEach(form => {
      const fields = () => all("input,select,textarea", form).filter(input => input.type !== "hidden");
      const value = input => ["checkbox", "radio"].includes(input.type) ? input.checked : input.value;
      const initial = new Map(fields().map(input => [input, value(input)]));
      const changed = () => { const current = fields(); form.classList.toggle("dirty", current.length !== initial.size || current.some(input => value(input) !== initial.get(input))); };
      form.addEventListener("input", changed);
      form.addEventListener("change", changed);
      form.addEventListener("submit", () => form.classList.remove("dirty"));
    });
  }
  setupForms();
  window.addEventListener("beforeunload", event => {
    if (query("form.dirty")) { event.preventDefault(); event.returnValue = ""; }
  });

  // Keep workload previews based on all tasks, even when the table is filtered.
  let deal = query("form.deal");
  let managementSubmitting = false;
  function setupDeal() {
    if (!deal) return;
    const rows = all("[data-grid-row]", deal);
    const fields = row => all("input[name], select[name]", row).filter(input => input.type !== "hidden");
    const value = input => input.type === "checkbox" ? input.checked : input.value;
    const initial = new Map(rows.map(row => [row, new Map(fields(row).map(input => [input, value(input)]))]));
    const loads = JSON.parse(deal.dataset.loads);
    const workload = JSON.parse(deal.dataset.workload);
    const sequenceLinks = JSON.parse(deal.dataset.sequences);
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
      const totals = Object.fromEntries(Object.keys(loads).map(name => [name, 0]));
      const tasks = Object.fromEntries(workload.map(task => [task.id, task]));
      rows.forEach(row => {
        const dirty = fields(row).some(input => value(input) !== initial.get(row).get(input));
        row.classList.toggle("row-dirty", dirty);
        if (dirty) changed++;
        const current = state(row, false);
        row.classList.toggle("off", !current.active);
        query(".owner-sel", row).dataset.owner = current.owner;
        const originalTask = tasks[row.dataset.taskId];
        tasks[row.dataset.taskId] = { ...originalTask, ...current,
          minutes_each: current.hours === state(row, true).hours ? originalTask.minutes_each : null };
      });
      const recurringSteps = new Set();
      Object.entries(sequenceLinks).forEach(([rootId, steps]) => {
        if (tasks[rootId]?.active && tasks[rootId].frequency !== "once") steps.forEach(id => recurringSteps.add(String(id)));
      });
      const occurrences = item => {
        if (item.frequency === "selected") return (item.selected_days || "").split(",").filter(Boolean).length * 4.35;
        if (item.frequency === "interval") return (item.repeat_unit === "weeks" ? 4.35 : 1) / item.repeat_every;
        return { daily: 30.44, weekly: 4.35, yearly: 1 / 12 }[item.frequency] || 1;
      };
      Object.entries(tasks).forEach(([id, item]) => {
        if (!item.active || item.frequency === "once" || (item.frequency === "sequence" && !recurringSteps.has(id))) return;
        let count = occurrences(item);
        if (item.frequency === "sequence") count = Object.entries(sequenceLinks).reduce((sum, [rootId, steps]) =>
          sum + (tasks[rootId]?.active && tasks[rootId].frequency !== "once" && steps.some(step => String(step) === id) ? occurrences(tasks[rootId]) : 0), 0);
        const hours = item.minutes_each == null ? item.hours : item.minutes_each * count / 60;
        Object.keys(totals).forEach(name => { if (item.owner === name || item.owner === "shared") totals[name] += hours * (item.owner === "shared" ? 0.5 : 1); });
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
    deal.addEventListener("submit", () => { managementSubmitting = true; deal.classList.remove("dirty"); });
    query("[data-select-all]", deal).addEventListener("change", event => { all("[data-row-select]", deal).forEach(input => { input.checked = event.target.checked; }); updateDeal(); });
    query("[data-apply-owner]", deal).addEventListener("click", () => { rows.filter(row => query("[data-row-select]", row).checked).forEach(row => { query(".owner-sel", row).value = query("[data-bulk-owner]", deal).value; }); updateDeal(); });
    query("[data-discard]", deal).addEventListener("click", () => { initial.forEach(inputs => inputs.forEach((original, input) => { if (input.type === "checkbox") input.checked = original; else input.value = original; })); updateDeal(); });
    updateDeal();
  }
  setupDeal();

  // A full-page editor is always available. Wide desktops can open it beside the table.
  let editor = query("#task-editor");
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
    document.addEventListener("click", async event => {
      const close = event.target.closest("[data-close-editor]");
      if (close) { closeEditor(); return; }
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

  // Search stays on the current list, with only the newest response allowed to render.
  const searchForm = query("[data-task-search]");
  if (searchForm) {
    const input = query('input[name="q"]', searchForm);
    const clear = query("[data-search-clear]", searchForm);
    const panel = query("[data-search-panel]");
    const status = query("[data-search-status]");
    const launch = query("[data-open-search]");
    const cancel = query("[data-close-search]");
    const board = query(".dashboard");
    const listView = new URL(location.href).searchParams.get("view") || "today";
    let timer, controller, generation = 0;
    const words = () => input.value.trim().replace(/\s+/g, " ");
    function openSearch(focus = true) {
      panel.hidden = false;
      board.classList.add("search-open");
      if (focus) { input.focus(); input.scrollIntoView({block:"nearest", behavior:"instant"}); }
    }
    async function showResults(ticket = generation) {
      clearTimeout(timer);
      if (ticket !== generation) return false;
      if (savingTask) { timer = setTimeout(() => showResults(ticket), 200); return false; }
      const q = words();
      clear.hidden = !input.value;
      if (q === board.dataset.q) return true;
      const url = new URL(searchForm.action, location.href);
      if (listView !== "today") url.searchParams.set("view", listView);
      if (q) url.searchParams.set("q", q);
      controller?.abort();
      const request = controller = new AbortController();
      status.textContent = "Searching…";
      panel.setAttribute("aria-busy", "true");
      query("#tasks-region").setAttribute("aria-busy", "true");
      try {
        const response = await fetch(url, {cache:"no-store", credentials:"same-origin", signal:request.signal});
        if (!response.ok || response.redirected) throw new Error("search");
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        if (ticket !== generation || request.signal.aborted) return false;
        if (savingTask) { timer = setTimeout(() => showResults(ticket), 200); return false; }
        const next = query("#tasks-region", page), nextBoard = query(".dashboard", page);
        if (!next || !nextBoard) throw new Error("search");
        query("#tasks-region").replaceWith(next);
        paint(next); setupForms(next);
        board.dataset.view = nextBoard.dataset.view;
        board.dataset.q = nextBoard.dataset.q;
        const tabs = query(".period-tabs", page);
        if (tabs) query(".period-tabs")?.replaceWith(tabs);
        query("[data-current-list-label]").textContent = query("[data-current-list-label]", page).textContent;
        status.textContent = q ? query(".task-panel-heading > span", next).textContent : "";
        window.MoleMotion?.observe();
        history.replaceState(history.state, "", url.pathname + url.search);
        return true;
      } catch (error) {
        if (ticket === generation && error.name !== "AbortError") {
          status.textContent = "Couldn't search. Check your connection and try again.";
          toast(status.textContent, "error", () => showResults(generation), "Try again");
        }
        return false;
      } finally {
        if (ticket === generation) {
          panel.removeAttribute("aria-busy");
          query("#tasks-region")?.removeAttribute("aria-busy");
        }
      }
    }
    function scheduleSearch(immediate = false) {
      clearTimeout(timer); controller?.abort(); generation++;
      panel.removeAttribute("aria-busy");
      query("#tasks-region")?.removeAttribute("aria-busy");
      status.textContent = "";
      clear.hidden = !input.value;
      if (immediate) return showResults(generation);
      timer = setTimeout(() => showResults(generation), 200);
    }
    query("[data-search-submit]", searchForm).hidden = true;
    launch.hidden = cancel.hidden = false;
    panel.hidden = !board.dataset.q;
    if (board.dataset.q) openSearch(false);
    launch.addEventListener("click", () => openSearch());
    input.addEventListener("input", event => { if (!event.isComposing) scheduleSearch(); });
    input.addEventListener("compositionend", () => scheduleSearch());
    searchForm.addEventListener("submit", event => { event.preventDefault(); input.blur(); scheduleSearch(true); });
    clear.addEventListener("click", event => { event.preventDefault(); input.value = ""; scheduleSearch(true); input.focus(); });
    cancel.addEventListener("click", async () => {
      input.value = "";
      if (await scheduleSearch(true)) {
        panel.hidden = true; board.classList.remove("search-open"); launch.focus({preventScroll:true});
      }
    });
  }

  // Management filters are live too. A draft pauses filtering until it is saved or discarded.
  const filters = query("[data-live-filters]");
  if (filters) {
    const status = query("[data-filter-status]");
    const clear = query("[data-filter-clear]", filters);
    let timer, controller, generation = 0, pending = false;
    const address = () => {
      const url = new URL(location.pathname, location.href);
      for (const [key, value] of new FormData(filters)) if (value.trim()) url.searchParams.set(key, value.trim());
      return url;
    };
    async function filterResults(ticket = generation) {
      clearTimeout(timer);
      if (ticket !== generation || managementSubmitting) return;
      const url = address();
      clear.hidden = !url.search;
      all('input[name="return_to"], input[name="back"]', query("#management-results")).forEach(input => { input.value = url.pathname + url.search; });
      if (query("form.dirty", query("#management-results"))) {
        pending = true;
        status.textContent = "Save or discard your edits to update the results.";
        return;
      }
      pending = false;
      if (url.pathname + url.search === location.pathname + location.search) { status.textContent = ""; return; }
      editorController?.abort();
      controller?.abort();
      const request = controller = new AbortController();
      status.textContent = "Updating results…";
      query("#management-results").setAttribute("aria-busy", "true");
      try {
        const response = await fetch(url, {cache:"no-store", credentials:"same-origin", signal:request.signal});
        if (!response.ok || response.redirected) throw new Error("filters");
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        if (ticket !== generation || request.signal.aborted || managementSubmitting) return;
        if (query("form.dirty", query("#management-results"))) {
          pending = true; status.textContent = "Save or discard your edits to update the results."; return;
        }
        const next = query("#management-results", page);
        if (!next) throw new Error("filters");
        query("#management-results").replaceWith(next);
        deal = query("form.deal"); editor = query("#task-editor");
        setupDeal(); setupForms(next); paint(next);
        status.textContent = query("[data-results-count]", next).textContent;
        history.replaceState(history.state, "", url.pathname + url.search);
        const create = query('a[href*="/manage/tasks/new"]'), nextCreate = query('a[href*="/manage/tasks/new"]', page);
        if (create && nextCreate) create.href = nextCreate.href;
      } catch (error) {
        if (ticket === generation && error.name !== "AbortError") {
          status.textContent = "Couldn't update results. Check your connection and try again.";
          toast(status.textContent, "error", () => filterResults(generation), "Try again");
        }
      } finally { if (ticket === generation) query("#management-results")?.removeAttribute("aria-busy"); }
    }
    function scheduleFilters(immediate = false) {
      clearTimeout(timer); controller?.abort(); generation++;
      query("#management-results")?.removeAttribute("aria-busy");
      timer = setTimeout(() => filterResults(generation), immediate ? 0 : 200);
    }
    query("[data-filter-submit]", filters).hidden = true;
    filters.addEventListener("input", event => { if (!event.isComposing) scheduleFilters(); });
    filters.addEventListener("change", () => scheduleFilters(true));
    filters.addEventListener("compositionend", () => scheduleFilters());
    filters.addEventListener("submit", event => { event.preventDefault(); scheduleFilters(true); });
    clear.addEventListener("click", event => {
      event.preventDefault();
      all("input,select", filters).forEach(input => { input.value = ""; });
      scheduleFilters(true);
    });
    document.addEventListener("click", event => {
      if (pending && event.target.closest("[data-discard], [data-close-editor], [data-cancel-editor]")) scheduleFilters(true);
    });
    document.addEventListener("submit", event => {
      if (!event.defaultPrevented && event.target.closest("#task-editor")) managementSubmitting = true;
      if (managementSubmitting) { clearTimeout(timer); controller?.abort(); generation++; }
    });
  }

  const installed = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const installNudge = query("[data-install-nudge]");
  if (installNudge) installNudge.hidden = installed || !/iPhone|iPad/.test(navigator.userAgent) || setting("install-dismissed", "false") === "true";
  query("[data-dismiss-install]")?.addEventListener("click", () => { installNudge.hidden = true; saveSetting("install-dismissed", "true"); });
  const installedMessage = query("[data-installed-message]");
  if (installedMessage) installedMessage.hidden = !installed;

  function revealLinkedTask() {
    const match = location.hash.match(/^#(task|occurrence)-(\d+)$/);
    if (!match) return;
    const task = document.getElementById(match[1] + "-" + match[2]);
    if (!task) {
      // Old notifications used a dashboard anchor. Route them to the correct list.
      if (query(".dashboard")) location.replace((match[1] === "task" ? "/tasks/" : "/occurrences/") + match[2]);
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
    if ((needsRefresh || (hiddenAt && Date.now() - hiddenAt > 30000)) && navigator.onLine && !query("form.dirty") && !savingTask && !query("dialog[open]")) {
      if (query(".dashboard,#household-summary,#task-detail-region,#sequence-run-region,#occurrence-region")) {
        needsRefresh = false;
        refreshScheduledRegions(true).catch(() => { needsRefresh = true; });
      } else location.reload();
    }
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted && !query("form.dirty") && navigator.onLine) {
      if (query(".dashboard,#household-summary,#task-detail-region,#sequence-run-region,#occurrence-region")) refreshScheduledRegions(true).catch(() => { needsRefresh = true; });
      else location.reload();
    }
  });
  // Recheck the server when a waiting step becomes ready, including in the Home Screen app.
  setInterval(() => {
    if (document.hidden || savingTask || !navigator.onLine || query("form.dirty") || document.activeElement?.closest("form")) return;
    const livePage = query(".dashboard,#household-summary");
    if (all("[data-ready-at]").some(el => Date.parse(el.dataset.readyAt) <= Date.now()) || (livePage && Date.now() - lastLiveCheck >= 60000)) {
      lastLiveCheck = Date.now();
      refreshScheduledRegions(true).catch(() => { needsRefresh = true; });
    }
  }, 30000);

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
      const largeText = fixed && parseFloat(getComputedStyle(root).fontSize) > 24;
      navigation.classList.toggle("large-text", largeText);
      root.dataset.largeText = String(largeText);
      root.style.setProperty("--nav-height", fixed ? Math.ceil(navigation.getBoundingClientRect().height) + "px" : "0px");
    };
    sizeNavigation();
    let navigationFrame;
    const queueNavigationSize = () => {
      if (navigationFrame) return;
      navigationFrame = requestAnimationFrame(() => { navigationFrame = null; sizeNavigation(); });
    };
    if ("ResizeObserver" in window) new ResizeObserver(queueNavigationSize).observe(navigation);
    window.addEventListener("resize", queueNavigationSize);
  }
  requestAnimationFrame(() => document.body.classList.add("ready"));
})();
