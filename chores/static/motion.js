// Small, optional enhancements. Native controls own activation and scrolling.
(function () {
  "use strict";
  const root = document.documentElement;
  const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const animations = new Set();
  const watched = new Set();
  let blinkTimer, blinkEnd, pressed, pressStart, reactTimer;
  const enabled = () => !reduced.matches && root.dataset.quiet !== "true";
  function animate(el, frames, options) {
    if (!enabled() || !el?.animate) return;
    const animation = el.animate(frames, options);
    animations.add(animation);
    animation.finished.catch(() => {}).finally(() => animations.delete(animation));
  }
  const observer = "IntersectionObserver" in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => entry.target.classList.toggle("is-visible", entry.isIntersecting));
  }, {threshold: .1}) : null;
  function observe(scope = document) {
    watched.forEach(el => { if (!el.isConnected) { observer?.unobserve(el); watched.delete(el); } });
    all("[data-mascot]", scope).forEach(el => {
      if (watched.has(el)) return;
      watched.add(el);
      if (observer) observer.observe(el); else el.classList.add("is-visible");
    });
  }
  function scheduleBlink() {
    clearTimeout(blinkTimer);
    if (!enabled() || document.hidden) return;
    blinkTimer = setTimeout(() => {
      const candidates = all("[data-mascot].is-visible").filter(el => el.querySelector(".mole-lid") && el.dataset.pose !== "celebrate");
      const mascot = candidates.find(el => el.id === "hero-mole") || candidates[0];
      if (mascot) {
        mascot.classList.add("is-blinking");
        blinkEnd = setTimeout(() => mascot.classList.remove("is-blinking"), 220);
      }
      scheduleBlink();
    }, 5000 + Math.random() * 6500);
  }
  function preferencesChanged() {
    root.dataset.motion = enabled() ? "on" : "off";
    animations.forEach(animation => animation.cancel());
    clearTimeout(blinkEnd);
    all(".is-blinking,.is-cheering,.is-nodding").forEach(el => el.classList.remove("is-blinking", "is-cheering", "is-nodding"));
    scheduleBlink();
  }
  reduced.addEventListener("change", preferencesChanged);
  document.addEventListener("visibilitychange", () => {
    root.dataset.motionPaused = String(document.hidden);
    if (document.hidden) animations.forEach(animation => animation.cancel());
    scheduleBlink();
  });

  function capture(row) {
    const visible = all(".task[data-row-key]").filter(el => {
      const r = el.getBoundingClientRect();
      return r.height && r.bottom > 0 && r.top < innerHeight;
    });
    const anchor = visible.find(el => el !== row && !row?.contains(el));
    return {positions: new Map(visible.map(el => [el.dataset.rowKey, el.getBoundingClientRect().top])),
            anchor: anchor?.dataset.rowKey, anchorTop: anchor?.getBoundingClientRect().top,
            waiting: new Set(all(".task.waiting").map(el => el.dataset.rowKey)),
            bars: all(".bar > span").map(el => el.dataset.pct)};
  }
  async function confirm(row, done) {
    if (!enabled() || !row) return;
    row.classList.remove("saving");
    row.classList.add(done ? "completion-confirmed" : "undo-confirmed");
    // Feedback follows server confirmation; there is no speculative completion.
    await new Promise(resolve => setTimeout(resolve, done ? 240 : 100));
  }
  function restore(snapshot) {
    observe();
    if (!snapshot) return;
    const anchor = snapshot.anchor && document.getElementById(snapshot.anchor);
    if (anchor?.getClientRects().length) {
      const difference = anchor.getBoundingClientRect().top - snapshot.anchorTop;
      if (Math.abs(difference) > 1) scrollBy({top: difference, behavior: "instant"});
    }
    if (!enabled()) return;
    all(".task[data-row-key]").forEach(el => {
      const previous = snapshot.positions.get(el.dataset.rowKey);
      const current = el.getBoundingClientRect();
      if (!current.height || current.bottom < 0 || current.top > innerHeight) return;
      if (snapshot.waiting.has(el.dataset.rowKey) && !el.matches(".waiting,.done")) {
        animate(el, [{backgroundColor: getComputedStyle(root).getPropertyValue("--soft").trim()}, {backgroundColor: "transparent"}], {duration: 900, easing: "ease-out"});
      }
      if (previous !== undefined && Math.abs(previous - current.top) > 1 && Math.abs(previous - current.top) < 500) {
        animate(el, [{transform: `translateY(${previous - current.top}px)`}, {transform: "translateY(0)"}], {duration: 240, easing: "cubic-bezier(.2,.8,.2,1)"});
      }
    });
    all(".bar > span").forEach((el, i) => {
      if (snapshot.bars[i] !== undefined) animate(el, [{width: snapshot.bars[i] + "%"}, {width: el.dataset.pct + "%"}], {duration: 380, easing: "ease-out"});
    });
  }
  function react(done, allDone) {
    const hero = document.getElementById("hero-mole");
    if (!hero) return;
    hero.dataset.pose = allDone ? "celebrate" : "default";
    all("[data-mole-state]", hero).forEach(el => { el.hidden = el.dataset.moleState !== (allDone ? "celebrate" : "working"); });
    hero.classList.remove("is-cheering", "is-nodding");
    clearTimeout(reactTimer);
    if (done && enabled()) {
      requestAnimationFrame(() => {
        if (enabled()) hero.classList.add(allDone ? "is-cheering" : "is-nodding");
        reactTimer = setTimeout(() => hero.classList.remove("is-cheering", "is-nodding"), 1100);
      });
    }
  }

  const clearPress = () => { pressed?.classList.remove("is-pressed"); pressed = null; };
  document.addEventListener("pointerdown", event => {
    clearPress();
    if (!event.isPrimary || event.button !== 0) return;
    pressed = event.target.closest("button:not(:disabled),.btn,.app-nav a,.day-choice");
    if (!pressed) return;
    pressStart = {x: event.clientX, y: event.clientY};
    pressed.classList.add("is-pressed");
  }, {passive: true});
  document.addEventListener("pointermove", event => {
    if (pressed && Math.hypot(event.clientX - pressStart.x, event.clientY - pressStart.y) > 10) clearPress();
  }, {passive: true});
  ["pointerup", "pointercancel"].forEach(name => document.addEventListener(name, clearPress, {passive: true}));
  window.addEventListener("blur", clearPress);

  // Layout viewport and visual viewport differ when an iPhone keyboard opens.
  const viewport = window.visualViewport;
  function sizeViewport() {
    const editing = document.activeElement?.matches("textarea,select,input:not([type=checkbox]):not([type=radio]),[contenteditable=true]");
    const inset = viewport ? Math.max(0, innerHeight - viewport.height - viewport.offsetTop) : 0;
    const keyboard = !!editing && inset > 120 && viewport.scale < 1.1;
    root.dataset.keyboard = String(keyboard);
    root.style.setProperty("--keyboard-inset", keyboard ? Math.round(inset) + "px" : "0px");
    if (keyboard) {
      const field = document.activeElement;
      const r = field.getBoundingClientRect();
      if (r.bottom > viewport.offsetTop + viewport.height - 24) field.scrollIntoView({block: "center", behavior: "instant"});
    }
  }
  ["resize", "scroll"].forEach(name => viewport?.addEventListener(name, sizeViewport, {passive: true}));
  ["focusin", "focusout"].forEach(name => document.addEventListener(name, () => requestAnimationFrame(sizeViewport)));
  window.addEventListener("resize", sizeViewport, {passive: true});
  function focusControl(el) {
    if (!el) return;
    // Undo can restore a task into a run the user previously collapsed.
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    el.focus({preventScroll: true});
    requestAnimationFrame(() => {
      const r = el.getBoundingClientRect();
      const nav = document.querySelector(".app-nav");
      const navTop = nav && getComputedStyle(nav).position === "fixed" && nav.getClientRects().length ? nav.getBoundingClientRect().top : innerHeight;
      const bottom = Math.min(navTop, viewport ? viewport.offsetTop + viewport.height : innerHeight);
      const top = viewport?.offsetTop || 0;
      if (r.bottom > bottom - 12) scrollBy({top: r.bottom - bottom + 20, behavior: "instant"});
      else if (r.top < top + 12) scrollBy({top: r.top - top - 20, behavior: "instant"});
    });
  }
  sizeViewport();
  observe();
  window.MoleMotion = {enabled, preferencesChanged, capture, confirm, restore, react, observe, focusControl};
})();
