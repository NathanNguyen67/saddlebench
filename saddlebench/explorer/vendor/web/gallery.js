/* First screen: every class as a card with a looping animation of its representative reaction. */
window.SM = window.SM || {};

SM.Gallery = class Gallery {
  constructor(root, store, renderer, onOpen) {
    Object.assign(this, { root, store, renderer, onOpen });
    this.query = "";
    this.active = new Set();
    this.sort = "reactions";
    this.barrierMax = 1;
    this.cards = new Map();
    this.visible = new Set();
    this.still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.running = false;
    this.observer = new IntersectionObserver((entries) => this.onVisibility(entries), { rootMargin: "120px" });
    this.build();
  }

  build() {
    const { h } = SM;
    const counts = new Map();
    this.store.classes.forEach((c) => c.tags.forEach((tag) => counts.set(tag, (counts.get(tag) || 0) + 1)));
    const order = ["Collective", "Aligned slide", "Extended", "Local", "Multi-product", "Multi-endpoint saddle", "Unclassified"];
    this.chips = order.filter((tag) => counts.has(tag)).map((tag) => {
      const chip = h("button", { type: "button", class: `chip ${SM.TAG_CLASS[tag]}`, "aria-pressed": "false", onclick: () => this.toggle(tag, chip) },
        tag, h("span", { class: "chip-count" }, String(counts.get(tag))));
      return chip;
    });
    this.score = this.store.score;
    this.onlyMissed = false;
    if (this.score) {
      this.missedChip = h("button", { type: "button", class: "chip tag-missed", "aria-pressed": "false", onclick: () => this.setMissed(!this.onlyMissed) },
        "Missed reference saddles", this.missedCount = h("span", { class: "chip-count" }));
      this.chips.unshift(this.missedChip);
      this.score.listeners.add(() => this.refreshScore());
    }
    this.summary = h("p", { class: "result-count", role: "status" });
    const maxBarrier = Math.max(...this.store.classes.map((c) => c.barrierMin), 1);
    this.barrierMax = maxBarrier;
    const barrierValue = h("output", {}, `${maxBarrier.toFixed(2)} eV`);
    this.grid = h("div", { class: "card-grid" });
    this.root.append(
      h("div", { class: "filters" },
        h("input", { type: "search", class: "search", placeholder: "Search class or tag", "aria-label": "Search classes", oninput: (e) => { this.query = e.target.value.trim().toLowerCase(); this.apply(); } }),
        h("label", { class: "field" }, "Sort by",
          h("select", { onchange: (e) => { this.sort = e.target.value; this.apply(); } },
            this.score ? h("option", { value: "missed" }, "Most missed") : null,
            h("option", { value: "reactions" }, "Most reactions"),
            h("option", { value: "barrier" }, "Lowest barrier"),
            h("option", { value: "moved" }, "Most atoms moved"),
            h("option", { value: "products" }, "Most products"),
            h("option", { value: "id" }, "Class ID"))),
        h("label", { class: "field" }, "Lowest barrier up to",
          h("input", { type: "range", min: 0, max: maxBarrier, step: 0.01, value: maxBarrier, oninput: (e) => { this.barrierMax = Number(e.target.value); barrierValue.textContent = `${this.barrierMax.toFixed(2)} eV`; this.apply(); } }),
          barrierValue)),
      h("div", { class: "chips", role: "group", "aria-label": "Filter by tag" }, this.chips),
      this.summary,
      this.grid,
    );
    this.apply();
  }

  setMissed(on) {
    this.onlyMissed = on;
    this.missedChip?.setAttribute("aria-pressed", String(on));
    this.apply();
  }

  /* Opened from a scoreboard "missed" link: filter to classes with misses and sort by them. */
  showMissed() {
    if (!this.score) return;
    this.sort = "missed";
    const select = this.root.querySelector(".filters select");
    if (select) select.value = "missed";
    this.setMissed(true);
  }

  refreshScore() {
    this.cards.forEach((card) => card.badge.replaceChildren(SM.scoreBadge(this.score.classCounts(card.cls)) || ""));
    this.apply();
  }

  toggle(tag, chip) {
    if (this.active.has(tag)) this.active.delete(tag); else this.active.add(tag);
    chip.setAttribute("aria-pressed", String(this.active.has(tag)));
    this.apply();
  }

  matches(c) {
    if (c.barrierMin > this.barrierMax + 1e-9) return false;
    if (this.onlyMissed && !this.score.classCounts(c).missed) return false;
    if (![...this.active].every((tag) => c.tags.includes(tag))) return false;
    return !this.query || c.id.toLowerCase().includes(this.query) || c.tags.some((tag) => tag.toLowerCase().includes(this.query));
  }

  sorted(list) {
    const keys = {
      reactions: (a, b) => b.reactions.length - a.reactions.length,
      barrier: (a, b) => a.barrierMin - b.barrierMin,
      moved: (a, b) => b.movedMedian - a.movedMedian,
      products: (a, b) => b.majorProductGroups - a.majorProductGroups || b.productMinima - a.productMinima,
      id: (a, b) => a.id.localeCompare(b.id),
      missed: (a, b) => this.score.classCounts(b).missed - this.score.classCounts(a).missed || b.reactions.length - a.reactions.length,
    };
    return [...list].sort((a, b) => keys[this.sort](a, b) || a.id.localeCompare(b.id));
  }

  apply() {
    if (this.score) this.missedCount.textContent = String(this.store.classes.filter((c) => this.score.classCounts(c).missed).length);
    const shown = this.sorted(this.store.classes.filter((c) => this.matches(c)));
    this.summary.textContent = `Showing ${shown.length} of ${SM.fmt.count(this.store.classes.length, "class")}`;
    this.grid.replaceChildren(...shown.map((c) => this.card(c)));
    if (!shown.length) this.grid.append(SM.h("p", { class: "empty" }, "No classes match these filters."));
  }

  card(c) {
    if (this.cards.has(c.id)) return this.cards.get(c.id).node;
    const { h, fmt } = SM;
    const canvas = h("canvas", { class: "card-canvas", "aria-hidden": "true" });
    const badge = h("span", { class: "card-tags card-score" }, this.score ? SM.scoreBadge(this.score.classCounts(c)) : null);
    const node = h("button", { type: "button", class: "card", onclick: () => this.onOpen(c.id) },
      canvas,
      h("span", { class: "card-head" }, h("b", {}, c.id), h("span", {}, fmt.count(c.reactions.length, "reaction"))),
      h("span", { class: "card-barrier" }, `${fmt.range(c.barrierMin, c.barrierMax)} eV`),
      h("span", { class: "card-facts", title: "Curvature along the final Search mode (not a Hessian eigenvalue)" }, `Curvature ${SM.classCurvature(this.store, c) ? `${SM.fmt.curvature(SM.classCurvature(this.store, c)[2])} median` : "–"}`),
      h("span", { class: "card-facts" }, `${fmt.count(c.productMinima, "product")} · ${fmt.count(c.movedMedian, "atom")} moved`),
      h("span", { class: "card-tags" }, SM.classTags(c)),
      SM.debug && c.alignedGroups ? h("span", { class: "card-facts" }, fmt.count(c.alignedGroups, "aligned group")) : null,
      badge);
    const card = { node, canvas, cls: c, viewer: null, phase: c.reactions.length * 0.37, badge };
    node.dataset.cls = c.id;
    this.cards.set(c.id, card);
    this.observer.observe(canvas);
    canvas.dataset.cls = c.id;
    return node;
  }

  onVisibility(entries) {
    entries.forEach((entry) => {
      const card = this.cards.get(entry.target.dataset.cls);
      if (entry.isIntersecting) {
        if (!card.viewer) {
          const settings = new SM.ViewSettings({ radius: 7.5, yaw: 0.55, pitch: -0.95, fade: 0.5 });
          card.viewer = new SM.Viewer(card.canvas, this.store, this.renderer, settings, { interactive: false, overlays: false });
          card.viewer.setReaction(card.cls.representative);
        }
        this.visible.add(card);
      } else this.visible.delete(card);
    });
    this.start();
  }

  start() {
    if (this.still || this.running) return;
    this.running = true;
    let cursor = 0;
    const tick = (time) => {
      if (!this.running) return;
      const cards = [...this.visible];
      const budget = Math.min(cards.length, 5);
      for (let i = 0; i < budget; i += 1) {
        const card = cards[(cursor + i) % cards.length];
        card.viewer.settings.t = Math.sin(time / 900 + card.phase);
        card.viewer.draw();
      }
      cursor += budget;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  pause() { this.running = false; }

  resume() {
    this.cards.forEach((card) => { if (card.viewer) card.viewer.draw(); });
    this.start();
  }
};
