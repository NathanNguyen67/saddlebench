/* Class workspace: reaction and product lists, the main viewer with its tools, and the inspector. */
window.SM = window.SM || {};

SM.FAN_COLOURS = ["--p1", "--p2", "--p3", "--p4", "--p5", "--p6", "--p7", "--p8"];

SM.Workspace = class Workspace {
  constructor(root, store, renderer, callbacks) {
    Object.assign(this, { root, store, renderer, callbacks });
    this.settings = new SM.ViewSettings({ radius: 11, focus: false, box: 1, fade: 0, keepView: true, anchor: "cell" });
    /* #...&cmp=12,40 (shareable links, the flags page) is applied by the router via setCompare. */
    this.refFilter = "all";
    if (store.score) store.score.listeners.add(() => { if (this.cls) { this.renderSide(); this.inspector.render(); } });
    this.tab = "reactions";
    this.scope = "saddle";
    this.overlay = false;
    this.sortKey = "barrier";
    this.motionFilter = "all";
    this.fan = [];
    this.fanLimit = 8;
    this.playing = false;
    this.bySaddle = new Map();
    store.reactions.forEach((r) => { if (!this.bySaddle.has(r.saddle)) this.bySaddle.set(r.saddle, []); this.bySaddle.get(r.saddle).push(r); });
    this.build();
  }

  build() {
    const { h } = SM;
    const s = this.settings;
    this.title = h("h2", { class: "class-title" });
    this.titleTags = h("div", { class: "card-tags" });
    this.subtitle = h("p", { class: "muted" });
    this.tabs = {
      reactions: h("button", { type: "button", role: "tab", onclick: () => this.setTab("reactions") }, "Reactions"),
      products: h("button", { type: "button", role: "tab", onclick: () => this.setTab("products") }, "Products"),
    };
    this.sidebar = h("div", { class: "side-body" });
    this.canvas = h("canvas", { class: "main-canvas", tabIndex: 0, "aria-label": "Reaction viewer. Drag to rotate, scroll to zoom, shift-drag to pan, click an atom to see its element and motion. W/S and A/D also rotate." });
    this.viewer = new SM.Viewer(this.canvas, this.store, this.renderer, s, { onPick: (atom) => this.pick(atom), overlay: (ctx, viewer) => this.drawOverlay(ctx, viewer) });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "energy-strip");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Schematic energy profile of reactant, saddle and product");
    this.energy = new SM.EnergyStrip(svg);
    this.play = h("button", { type: "button", class: "play", onclick: () => this.togglePlay() }, "Play");
    this.slider = h("input", { type: "range", min: -100, max: 100, value: 0, class: "path-slider", "aria-label": "Position from reactant through the saddle to product", oninput: (e) => { this.stop(); this.setT(Number(e.target.value) / 100); } });
    this.position = h("output", { class: "position" }, "Saddle");
    this.moverCount = h("output", {});
    const value = (text) => h("output", {}, text);
    const thresholdValue = value(`${s.threshold.toFixed(2)} Å`); const scaleValue = value("1.0×");
    const fadeValue = value(`${Math.round(s.fade * 100)}%`);
    const sizeValue = value("1.00×");
    this.sizeValue = sizeValue;
    /* Focus box: shaped like the cell, box × the cell on each repeated axis, centred on the movers (1 = one cell,
       3 = a 3×3(×3) supercell; under 1 crops to the reaction). */
    this.boxValue = value("");
    this.boxSlider = h("input", { type: "range", min: 0.3, max: 3, step: 0.1, value: s.box, "aria-label": "Focus box size in cells",
      oninput: (e) => this.setBox(Number(e.target.value)) });
    this.pinCount = h("output", {});
    this.pinInput = h("input", { type: "text", class: "pin-input", placeholder: "e.g. 12, 40-45", "aria-label": "Pin atoms by index", spellcheck: false,
      onkeydown: (e) => { if (e.key === "Enter") this.pinFromText(e.target.value); } });
    const vectorBoxes = SM.VECTORS.map((v) => h("label", { class: "check" },
      h("input", { type: "checkbox", onchange: (e) => { if (e.target.checked) s.vectors.add(v.id); else s.vectors.delete(v.id); s.redraw(); } }),
      h("i", { class: "swatch", style: `background:var(${v.token})` }), v.label));
    this.fanSection = h("section", { class: "panel fan-panel", hidden: true });
    this.inspectorRoot = h("aside", { class: "inspector panel" });
    this.inspector = new SM.Inspector(this.inspectorRoot, this.store, s, {
      focusAtom: (atom) => this.focusAtom(atom), currentClass: () => this.cls,
      togglePin: (atom) => this.togglePin(atom), clearPins: () => this.clearPins(), selectReaction: (id) => this.select(id),
    });

    const tool = (label, ...children) => h("div", { class: "tool" }, h("span", { class: "tool-label" }, label), h("div", { class: "tool-body" }, children));
    this.root.append(
      h("div", { class: "work-head" },
        h("button", { type: "button", class: "back", onclick: () => this.callbacks.back() }, "← All classes"),
        h("div", { class: "work-title" }, this.title, this.titleTags),
        this.subtitle),
      h("div", { class: "work-grid" },
        h("aside", { class: "side panel" }, h("div", { class: "tabs", role: "tablist" }, Object.values(this.tabs)), this.sidebar),
        h("div", { class: "centre" },
          h("section", { class: "panel viewer-panel" },
            this.canvas,
            h("div", { class: "dock" }, this.play, h("div", { class: "slider-wrap" }, this.slider,
              h("div", { class: "ticks" }, h("span", {}, "Reactant"), h("span", {}, "Saddle"), h("span", {}, "Product"))), this.position),
            svg,
            h("p", { class: "note" }, "Stored reactant, saddle and product frames joined by straight lines; the path between them is schematic. Drag rotates, Alt/Ctrl-drag or Q/E spins in the screen plane, Shift-drag pans, B toggles bonds, click atoms to pin them (two pinned: distance at the bottom; three: angle). Crosses mark fixed atoms."),
            h("div", { class: "tools" },
              tool("View",
                h("label", { class: "field-inline", title: "Cell: the view stays on the cell centre for every reaction (widen Box and zoom to look across an edge). Reaction: centre on this reaction's moving atoms." }, "Centre",
                  h("select", { onchange: (e) => { s.anchor = e.target.value; this.viewer.centre = null; s.redraw(); } },
                    h("option", { value: "cell", selected: s.anchor === "cell" }, "Cell"), h("option", { value: "movers", selected: s.anchor !== "cell" }, "Reaction"))),
                h("label", { class: "field-inline", title: "Box shaped like the cell around the centre. 1 = one cell, 3 = 3×3 supercell, under 1 crops." }, "Box", this.boxSlider, this.boxValue),
                h("label", { class: "field-inline" }, "Fade others",
                  h("input", { type: "range", min: 0, max: 0.92, step: 0.02, value: s.fade, "aria-label": "Fade atoms that are not moving, pinned or selected", oninput: (e) => { s.fade = Number(e.target.value); fadeValue.textContent = `${Math.round(s.fade * 100)}%`; s.redraw(); } }), fadeValue),
                h("label", { class: "field-inline" }, "Atom size",
                  this.sizeSlider = h("input", { type: "range", min: 0.2, max: 1.5, step: 0.05, value: s.atomScale, "aria-label": "Atom radius scale", oninput: (e) => { s.atomScale = Number(e.target.value); sizeValue.textContent = `${s.atomScale.toFixed(2)}×`; s.redraw(); } }), sizeValue),
                h("label", { class: "check", title: "Bonds (B): covalent radii × 1.15. Shrink Atom size for ball-and-stick." },
                  this.bondBox = h("input", { type: "checkbox", onchange: (e) => { s.bonds = e.target.checked; s.redraw(); } }), "Bonds")),
              tool("Highlight",
                h("input", { type: "range", min: 0.05, max: 2, step: 0.05, value: s.threshold, "aria-label": "Highlight atoms moving more than this distance", oninput: (e) => { s.threshold = Number(e.target.value); thresholdValue.textContent = `${s.threshold.toFixed(2)} Å`; this.updateMovers(); s.redraw(); } }), thresholdValue, this.moverCount,
                h("label", { class: "field-inline" }, "Colour", h("select", { onchange: (e) => { s.colour = e.target.value; s.redraw(); } },
                  h("option", { value: "highlight" }, "Element, ring movers"), SM.debug ? h("option", { value: "group" }, "Motion groups") : null, h("option", { value: "element" }, "Element"), h("option", { value: "diff" }, "Difference vs compared (pinned frame, else saddle)"), h("option", { value: "displacement" }, "Displacement"), h("option", { value: "height" }, "Height"), h("option", { value: "fixed" }, "Fixed or free")))),
              tool("Vectors", vectorBoxes,
                h("label", { class: "field-inline" }, "Scale", h("input", { type: "range", min: 0.25, max: 4, step: 0.25, value: 1, oninput: (e) => { s.vectorScale = Number(e.target.value); scaleValue.textContent = `${s.vectorScale.toFixed(1)}×`; s.redraw(); } }), scaleValue)),
              tool("Camera", ["top", "side", "bottom", "iso"].map((name) => h("button", { type: "button", onclick: () => s.camera(name) }, name[0].toUpperCase() + name.slice(1))),
                h("button", { type: "button", onclick: () => s.resetCamera() }, "Reset"),
                h("label", { class: "check", title: "Keep the camera centre when switching reactions (Reset recentres on the current reaction)" },
                  h("input", { type: "checkbox", checked: s.keepView, onchange: (e) => { s.keepView = e.target.checked; } }), "Keep view")),
              tool("Pin atoms", this.pinInput, h("button", { type: "button", onclick: () => this.pinFromText(this.pinInput.value) }, "Pin"),
                h("button", { type: "button", onclick: () => this.clearPins() }, "Clear"), this.pinCount))),
          this.fanSection),
        this.inspectorRoot),
    );
    this.setTabState();
    this.setBox(s.box);
    document.addEventListener("keydown", (e) => this.onKey(e));
    window.addEventListener("resize", () => s.redraw());
  }

  open(classId, reaction) {
    const cls = this.store.classById.get(classId);
    if (!cls) return false;
    if (this.cls !== cls) {
      this.cls = cls;
      this.title.textContent = cls.id;
      this.titleTags.replaceChildren(...SM.classTags(cls));
      this.subtitle.textContent = `${SM.fmt.count(cls.reactions.length, "reaction")} · ${SM.fmt.count(cls.saddles, "saddle")} · barrier ${SM.fmt.range(cls.barrierMin, cls.barrierMax)} eV`;
      this.motionFilter = "all";
    }
    const target = cls.reactions.includes(reaction) ? reaction : cls.representative;
    this.select(target);
    return true;
  }

  rows() {
    const score = this.store.score;
    const list = this.cls.reactions.map((i) => this.store.reactions[i])
      .filter((r) => this.motionFilter === "all" || r.motion.label === this.motionFilter)
      .filter((r) => !score || this.refFilter === "all" || (this.refFilter === "reference" ? score.status(r.id) : score.status(r.id) === "missed"));
    const keys = {
      barrier: (a, b) => a.barrier - b.barrier,
      moved: (a, b) => b.motion.moved - a.motion.moved,
      endpoints: (a, b) => b.saddleMinima - a.saddleMinima || a.barrier - b.barrier,
      energy: (a, b) => a.reactionEnergy - b.reactionEnergy,
    };
    return list.sort((a, b) => keys[this.sortKey](a, b) || a.id - b.id);
  }

  renderReactions() {
    const { h, fmt } = SM;
    const rows = this.rows();
    const labels = [...new Set(this.cls.reactions.map((i) => this.store.reactions[i].motion.label))];
    SM.fill(this.sidebar,
      h("div", { class: "list-tools" },
        h("label", { class: "field-inline" }, "Sort", h("select", { onchange: (e) => { this.sortKey = e.target.value; this.renderSide(); } },
          [["barrier", "Barrier"], ["moved", "Atoms moved"], ["endpoints", "Most endpoints"], ["energy", "Reaction energy"]].map(([v, t]) => h("option", { value: v, selected: v === this.sortKey }, t)))),
        labels.length > 1 ? h("label", { class: "field-inline" }, "Show", h("select", { onchange: (e) => { this.motionFilter = e.target.value; this.renderSide(); } },
          h("option", { value: "all" }, "All"), labels.map((l) => h("option", { value: l, selected: l === this.motionFilter }, SM.MOTION_NAMES[l])))) : null,
        this.store.score ? h("label", { class: "field-inline" }, "Reference", h("select", { onchange: (e) => { this.refFilter = e.target.value; this.renderSide(); } },
          [["all", "All reactions"], ["reference", "Reference only"], ["missed", "Missed only"]].map(([v, t]) => h("option", { value: v, selected: v === this.refFilter }, t)))) : null),
      rows.length ? null : h("p", { class: "muted" }, "No reactions match these filters."),
      this.compareBar(),
      h("div", { class: "rows" }, rows.map((r) => h("div", { class: "row-wrap" }, this.compareToggle(r), h("button", { type: "button", class: `row${r.id === this.reaction ? " on" : ""}`, "data-reaction": r.id, onclick: () => this.select(r.id) },
        h("span", { class: "row-main" }, h("b", {}, `${fmt.ev(r.barrier)} eV`), this.store.score ? this.store.score.pill(r.id) : null, h("span", {}, `→ ${this.store.label(r.product)}`)),
        h("span", { class: "row-sub" }, `${r.saddle.slice(4, 10)} · ${fmt.count(r.motion.moved, "atom")} · ${fmt.signed(r.reactionEnergy)} eV${r.curvature === null || r.curvature === undefined ? "" : ` · κ ${r.curvature.toFixed(2)}`}`),
        r.saddleMinima > 2 ? h("span", { class: "row-flag" }, `${r.saddleMinima} minima`) : null,
        this.store.score?.nearNull(r.id) ? h("span", { class: "row-flag", title: "The product is barely distinct from the seed" }, "near-null") : null)))),
    );
    this.sidebar.querySelector(".row.on")?.scrollIntoView({ block: "nearest" });
  }

  /* Compared saddles as removable chips (kept across classes, so they stay visible here). */
  compareBar() {
    const { h } = SM;
    const map = this.settings.compare;
    if (!map.size) return null;
    const changed = () => { this.renderSide(); this.inspector.render(); this.settings.redraw(); };
    return h("div", { class: "compare-bar" },
      h("span", { class: "muted" }, "Comparing (ghosts in the viewer):"),
      h("div", { class: "compare-chips" }, [...map].map(([id, slot]) => {
        const r = this.store.reactions[id];
        return h("span", { class: "compare-chip" }, h("i", { class: "swatch", style: `background:var(${SM.FAN_COLOURS[slot % 8]})` }),
          h("button", { type: "button", class: "linkish", title: "Open this reaction", onclick: () => this.goto(id) }, `${r.saddle.slice(4, 10)}${r.classes[0] !== this.cls.id ? ` (${r.classes[0]})` : ""}`),
          h("select", { class: "ghost-frame", title: "Frame this ghost shows: follow the main slider, or pin it at its reactant, saddle or product",
            "aria-label": "Ghost frame", onchange: (e) => { const v = e.target.value; if (v === "") this.settings.ghostT.delete(id); else this.settings.ghostT.set(id, Number(v)); this.inspector.render(); this.settings.redraw(); } },
            [["", "slider"], ["-1", "reactant"], ["0", "saddle"], ["1", "product"]].map(([v, t]) => h("option", { value: v, selected: String(this.settings.ghostT.get(id) ?? "") === v }, t))),
          h("button", { type: "button", class: "chip-x", "aria-label": "Stop comparing", onclick: () => { map.delete(id); this.settings.ghostT.delete(id); changed(); } }, "×"));
      })),
      h("button", { type: "button", onclick: () => { map.clear(); this.settings.ghostT.clear(); changed(); } }, "Clear"));
  }

  setBox(x) {
    const s = this.settings;
    s.box = Math.max(0.3, Math.min(3, x));
    if (this.boxSlider) this.boxSlider.value = s.box;
    const axes = this.viewer.repeatAxes();
    const edges = this.viewer.lattice.cell.filter((v, k) => axes[k]).map((v) => (s.box * Math.hypot(v[0], v[1], v[2])).toFixed(0));
    if (this.boxValue) this.boxValue.textContent = `${s.box.toFixed(1)}× cell${edges.length ? ` (${edges.join(" × ")} Å)` : ""}`;
    s.redraw();
  }

  setAtomScale(x) {
    this.settings.atomScale = Math.max(0.2, Math.min(1.5, x));
    if (this.sizeSlider) this.sizeSlider.value = this.settings.atomScale;
    if (this.sizeValue) this.sizeValue.textContent = `${this.settings.atomScale.toFixed(2)}×`;
    this.settings.redraw();
  }

  /* Open a reaction, switching class if needed. */
  goto(id) {
    const r = this.store.reactions[id];
    if (r.classes[0] !== this.cls.id) this.open(r.classes[0], id); else this.select(id);
  }

  /* Replace the comparison set (from the URL or the flags page). */
  setCompare(ids) {
    this.settings.compare.clear();
    ids.forEach((id, k) => this.settings.compare.set(id, k));
    if (this.cls) { this.renderSide(); this.inspector.render(); }
    this.settings.redraw();
  }

  /* Toggle a reaction into the comparison set; each gets the next free colour slot. */
  compareToggle(r) {
    const { h } = SM;
    const map = this.settings.compare;
    const slot = map.get(r.id);
    return h("button", { type: "button", class: "cmp", "aria-pressed": String(slot !== undefined),
      title: slot !== undefined ? "Stop comparing this reaction" : "Compare: colour this reaction's movers in the viewer",
      style: slot !== undefined ? `background:var(${SM.FAN_COLOURS[slot % 8]});border-color:var(${SM.FAN_COLOURS[slot % 8]})` : null,
      onclick: () => {
        if (map.has(r.id)) map.delete(r.id);
        else { const used = new Set(map.values()); let k = 0; while (used.has(k)) k += 1; map.set(r.id, k); }
        this.renderSide(); this.inspector.render(); this.settings.redraw();
      } }, slot !== undefined ? "" : "+");
  }

  saddleReactions() { return this.bySaddle.get(this.store.reactions[this.reaction].saddle) || []; }

  classProducts() {
    const groups = new Map();
    this.cls.reactions.map((i) => this.store.reactions[i]).filter((r) => r.seedConnected).forEach((r) => {
      if (!groups.has(r.productGroup)) groups.set(r.productGroup, []);
      groups.get(r.productGroup).push(r);
    });
    return [...groups.values()].sort((a, b) => b.length - a.length).map((list) => ({ r: [...list].sort((a, b) => a.barrier - b.barrier)[0], n: list.length }));
  }

  fanItems() {
    if (this.scope === "saddle") {
      const seeds = this.store.seeds;
      return this.saddleReactions().map((r) => ({ r, n: r.nObs }))
        .sort((a, b) => (seeds.has(b.r.reactant) || seeds.has(b.r.product)) - (seeds.has(a.r.reactant) || seeds.has(a.r.product)) || b.n - a.n);
    }
    return this.classProducts();
  }

  renderProducts() {
    const { h, fmt } = SM;
    const items = this.fanItems();
    const segment = (scope, text) => h("button", { type: "button", "aria-pressed": String(this.scope === scope), onclick: () => { this.scope = scope; this.overlay = false; this.fanLimit = 8; this.renderSide(); this.renderFan(); this.settings.redraw(); } }, text);
    const runs = this.scope === "saddle" ? this.saddleReactions().flatMap((r) =>
      (r.doubleMins || []).map((run) => ({ r, run }))).sort((a, b) => a.run.observation - b.run.observation) : [];
    SM.fill(this.sidebar,
      h("div", { class: "segment" }, segment("saddle", "This saddle"), segment("class", "Whole class")),
      this.scope === "saddle" ? SM.endpointMap(this.store, this.saddleReactions(), this.reaction, (id) => this.select(id)) : null,
      this.compareBar(),
      h("p", { class: "muted" }, this.scope === "saddle" ? `${fmt.count(items.length, "endpoint pair")} from this saddle (distinct pairs; each DoubleMin run is under "All runs").` : `${fmt.count(items.length, "product type")} with matching energy and motion.`),
      h("div", { class: "list-tools" },
        this.scope === "saddle" && items.length > 1 ? h("label", { class: "check" }, h("input", { type: "checkbox", checked: this.overlay, onchange: (e) => { this.overlay = e.target.checked; this.settings.redraw(); } }), "Overlay all paths") : null,
        items.length > 1 ? h("button", { type: "button", title: "Compare every listed pair (up to 8) against the selected one", onclick: () => this.setCompare(items.slice(0, 8).map(({ r }) => r.id).filter((id) => id !== this.reaction)) }, "Compare all") : null),
      h("div", { class: "rows" }, items.map(({ r, n }, index) => h("div", { class: "row-wrap" }, this.compareToggle(r), h("button", { type: "button", class: `row${r.id === this.reaction ? " on" : ""}`, onclick: () => this.select(r.id) },
        h("span", { class: "row-main" }, h("i", { class: "swatch", style: `background:var(${SM.FAN_COLOURS[index % 8]})` }), h("b", {}, `${this.store.label(r.reactant)} → ${this.store.label(r.product)}`)),
        h("span", { class: "row-sub" }, `${fmt.ev(r.barrier)} eV barrier · ${fmt.signed(r.reactionEnergy)} eV · ${this.scope === "saddle" ? fmt.count(n, "DoubleMin") : fmt.count(n, "reaction")}`))))),
      runs.length ? h("details", { class: "runs" }, h("summary", {}, `All DoubleMin runs (${runs.length})`),
        h("div", { class: "rows" }, runs.map(({ r, run }, index) => h("button", { type: "button", class: "row", onclick: () => this.select(r.id) },
          h("span", { class: "row-main" }, `Run ${index + 1}: ${this.store.label(run.reactant)} ↔ ${this.store.label(run.product)}`),
          h("span", { class: "row-sub" }, `${run.seedConnected ? "Seed-connected" : "Unconnected"} · ${fmt.signed(run.eR)} ↔ ${fmt.signed(run.eP)} eV · observation ${run.observation}`))))) : null,
    );
  }

  renderSide() { if (this.tab === "reactions") this.renderReactions(); else this.renderProducts(); }

  renderFan() {
    const { h, fmt } = SM;
    this.fan.forEach((viewer) => viewer.dispose());
    this.fan = [];
    this.fanSection.hidden = this.tab !== "products";
    if (this.tab !== "products") return;
    const items = this.fanItems();
    const cells = items.slice(0, this.fanLimit).map(({ r }, index) => {
      const canvas = h("canvas", { class: "fan-canvas", "aria-hidden": "true" });
      const viewer = new SM.Viewer(canvas, this.store, this.renderer, this.settings, { overlays: false });
      this.fan.push({ viewer, r });
      return h("button", { type: "button", class: `fan-cell${r.id === this.reaction ? " on" : ""}`, onclick: () => this.select(r.id) }, canvas,
        h("span", { class: "fan-caption" }, h("i", { class: "swatch", style: `background:var(${SM.FAN_COLOURS[index % 8]})` }), `${this.store.label(r.reactant)} → ${this.store.label(r.product)}`),
        h("span", { class: "row-sub" }, `Barrier ${fmt.ev(r.barrier)} · ${fmt.signed(r.reactionEnergy)} eV`));
    });
    SM.fill(this.fanSection,
      h("h3", {}, "Product fan"),
      h("div", { class: "fan-grid" }, cells),
      items.length > this.fanLimit ? h("button", { type: "button", class: "more", onclick: () => { this.fanLimit = items.length; this.renderFan(); } }, `Show all ${items.length}`) : null,
    );
    this.fan = this.fan.map(({ viewer, r }) => { viewer.setReaction(r.id); return viewer; });
  }

  drawOverlay(ctx, viewer) {
    if (!this.overlay || this.tab !== "products" || this.scope !== "saddle") return;
    const { store, settings: s } = this;
    const style = getComputedStyle(this.canvas);
    const current = store.path(this.reaction, s.t);
    const saddle = store.frame(this.reaction, 1);
    this.saddleReactions().forEach((r, index) => {
      const path = store.difference(r.id, 2, 1);
      const colour = style.getPropertyValue(SM.FAN_COLOURS[index % 8]).trim();
      viewer.drawn.forEach((d) => {
        if (!d.primary || store.fixed[d.atom]) return;
        const i = d.atom * 3;
        if (Math.hypot(path[i], path[i + 1], path[i + 2]) < s.threshold) return;
        const base = [d.x + saddle[i] - current[i], d.y + saddle[i + 1] - current[i + 1], d.z + saddle[i + 2] - current[i + 2]];
        viewer.arrow(ctx, viewer.project(base[0], base[1], base[2], viewer.view), viewer.project(base[0] + path[i], base[1] + path[i + 1], base[2] + path[i + 2], viewer.view), colour, 2.5);
      });
    });
  }

  setTabState() {
    Object.entries(this.tabs).forEach(([name, button]) => button.setAttribute("aria-selected", String(name === this.tab)));
  }

  setTab(tab) {
    this.tab = tab;
    this.setTabState();
    this.renderSide();
    this.renderFan();
    this.settings.redraw();
  }

  select(reaction) {
    this.reaction = reaction;
    this.viewer.setReaction(reaction);
    this.energy.set(this.store.reactions[reaction]);
    this.energy.render(this.settings.t);
    this.updateMovers();
    this.inspector.show(reaction);
    this.renderSide();
    this.renderFan();
    this.callbacks.changed(this.cls.id, reaction);
  }

  updateMovers() {
    const n = this.store.movers(this.reaction, this.settings.threshold).length;
    this.moverCount.textContent = SM.fmt.count(n, "atom");
  }

  /* Click an atom: pin it (or unpin it) and show it in the inspector. Click empty space to clear the selection. */
  pick(atom) {
    if (atom === null) { this.inspector.selectAtom(null); return; }
    const s = this.settings;
    if (s.pinned.has(atom) && this.inspector.atom === atom) s.pinned.delete(atom); else s.pinned.add(atom);
    this.updatePins();
    this.inspector.selectAtom(atom);
  }

  togglePin(atom) {
    const s = this.settings;
    if (s.pinned.has(atom)) s.pinned.delete(atom); else s.pinned.add(atom);
    this.updatePins(); this.inspector.render(); s.redraw();
  }

  clearPins() { this.settings.pinned.clear(); this.updatePins(); this.inspector.render(); this.settings.redraw(); }

  /* "12, 40-45 7" -> pins; indices start at 0. Out-of-range entries are ignored. */
  pinFromText(text) {
    const s = this.settings;
    (text.match(/\d+\s*-\s*\d+|\d+/g) || []).forEach((part) => {
      const [a, b] = part.split("-").map((x) => Number(x.trim()));
      for (let i = a; i <= (b === undefined ? a : b) && i - a < 2000; i += 1) if (i < this.store.n) s.pinned.add(i);
    });
    this.pinInput.value = "";
    this.updatePins(); this.inspector.render(); s.redraw();
  }

  updatePins() {
    const n = this.settings.pinned.size;
    this.pinCount.textContent = n ? SM.fmt.count(n, "pinned atom") : "";
  }

  focusAtom(atom) {
    const s = this.settings;
    s.focusAtom = s.focusAtom === atom ? null : atom;
    this.viewer.centre = null;
    this.inspector.render();
    s.redraw();
  }

  setT(t) {
    const s = this.settings;
    s.t = t;
    this.slider.value = Math.round(t * 100);
    this.position.textContent = t === 0 ? "Saddle" : t === -1 ? "Reactant" : t === 1 ? "Product" : `${Math.round(Math.abs(t) * 100)}% to ${t < 0 ? "reactant" : "product"}`;
    this.energy.render(t);
    if (SM.frameOfT(t) !== null || this._lastFrameT !== null) this.inspector.render();
    this._lastFrameT = SM.frameOfT(t);
    s.redraw();
  }

  togglePlay() {
    if (this.playing) { this.stop(); return; }
    this.playing = true;
    this.play.textContent = "Pause";
    let start = null;
    const loop = (time) => {
      if (!this.playing) return;
      if (start === null) start = time;
      this.setT(Math.round(Math.sin((time - start) / 1100) * 100) / 100);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  stop() { this.playing = false; this.play.textContent = "Play"; }

  step(delta) {
    const list = this.tab === "reactions" ? this.rows().map((r) => r.id) : this.fanItems().map((item) => item.r.id);
    const index = list.indexOf(this.reaction);
    if (list.length) this.select(list[(index + delta + list.length) % list.length]);
  }

  onKey(e) {
    if (this.root.hidden || ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); this.step(1); }
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); this.step(-1); }
    else if (e.key === " " && e.target.tagName !== "BUTTON") { e.preventDefault(); this.togglePlay(); }
    else if (e.key === "Escape") this.callbacks.back();
    else if ((e.key === "b" || e.key === "B") && !e.altKey) {
      e.preventDefault();
      this.settings.bonds = !this.settings.bonds;
      if (this.bondBox) this.bondBox.checked = this.settings.bonds;
      this.settings.redraw();
    }
  }

  show() { this.root.hidden = false; this.settings.redraw(); }

  hide() { this.stop(); this.root.hidden = true; }
};
