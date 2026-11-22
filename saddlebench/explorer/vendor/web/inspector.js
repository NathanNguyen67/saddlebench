/* Right-hand panel: facts about the selected reaction and the clicked atom. */
window.SM = window.SM || {};

SM.Inspector = class Inspector {
  constructor(root, store, settings, actions) {
    Object.assign(this, { root, store, settings, actions });
    this.reaction = null;
    this.atom = null;
  }

  show(reaction) { this.reaction = reaction; this.render(); }

  selectAtom(atom) {
    this.atom = atom;
    this.settings.selected = atom;
    this.render();
    this.settings.redraw();
  }

  /* For each compared reaction: how its saddle, reactant and product differ from this reaction's, atom by atom. */
  comparePanel() {
    const { h, fmt } = SM;
    const others = [...this.settings.compare].filter(([id]) => id !== this.reaction);
    if (!others.length) return null;
    const r = this.store.reactions[this.reaction];
    const atomName = (i) => `${SM.geo.SYMBOLS[this.store.numbers[i]]} ${i}`;
    return h("section", { class: "inspect-block" },
      h("h3", {}, "Compared with"),
      others.map(([id, slot]) => {
        const o = this.store.reactions[id];
        const saddle = SM.frameDiff(this.store, this.reaction, 1, id, 1);
        const reactant = SM.frameDiff(this.store, this.reaction, 0, id, 0);
        const product = SM.frameDiff(this.store, this.reaction, 2, id, 2);
        const same = (x) => (x.max <= 0.2 ? "same (≤ 0.2 Å)" : `differ by ${x.max.toFixed(2)} Å`);
        return h("div", { class: "compare-item" },
          h("p", {}, h("i", { class: "swatch", style: `background:var(${SM.FAN_COLOURS[slot % 8]})` }), " ",
            h("b", {}, o.saddle.slice(4, 10)), ` · ${fmt.ev(o.barrier)} eV → ${this.store.label(o.product)}`),
          h("dl", { class: "facts" },
            this.row("Saddles", `${saddle.max.toFixed(3)} Å max`),
            this.row("Reactants", same(reactant)),
            this.row("Products", o.product === r.product ? "same minimum" : same(product)),
            this.row("Saddle energy", `${fmt.signed(o.eS - r.eS)} eV`)),
          h("p", { class: "muted" }, "Atoms differing most between the saddles (drift removed, as in SaddleBench):"),
          h("div", { class: "neighbours" }, saddle.top.map(([atom, dist]) => h("button", { type: "button", title: "Select and pin this atom", onclick: () => this.actions.togglePin(atom) },
            h("b", {}, atomName(atom)), ` ${dist.toFixed(2)} Å`))));
      }),
      h("p", { class: "note" }, "Colour → \"Difference vs compared saddle\" shades every atom by this difference. Above 0.2 Å the benchmark treats the saddles as different."));
  }

  row(label, value) {
    return [SM.h("dt", {}, label), SM.h("dd", {}, value)];
  }

  alignment(id) {
    const { store } = this;
    const mode = store.mode(id);
    if (!mode) return SM.h("p", { class: "muted" }, "No search mode was saved for this reaction.");
    const rows = [["Saddle − reactant", store.difference(id, 1, 0)], ["Product − saddle", store.difference(id, 2, 1)], ["Product − reactant", store.difference(id, 2, 0)]];
    return SM.h("div", { class: "bars" }, rows.map(([label, field]) => {
      const value = Math.abs(store.cosine(mode, field) ?? 0);
      return SM.h("div", { class: "bar-row" }, SM.h("span", {}, label),
        SM.h("span", { class: "bar" }, SM.h("i", { style: `width:${(value * 100).toFixed(0)}%` })),
        SM.h("b", {}, value.toFixed(2)));
    }));
  }

  neighbours(atom) {
    const { store } = this;
    const lattice = store.lattice;
    const p = store.path(this.reaction, this.settings.t);
    const list = [];
    for (let i = 0; i < store.n; i += 1) {
      if (i === atom) continue;
      const d = lattice.mic(p[i * 3] - p[atom * 3], p[i * 3 + 1] - p[atom * 3 + 1], p[i * 3 + 2] - p[atom * 3 + 2]);
      list.push([Math.hypot(d[0], d[1], d[2]), i]);
    }
    return list.sort((a, b) => a[0] - b[0]).slice(0, 6);
  }

  atomPanel() {
    const { h } = SM;
    const { store, settings: s } = this;
    if (this.atom === null) return h("p", { class: "muted" }, "Click an atom to pin it and see its index and motion. Click it again to unpin.");
    const atom = this.atom; const id = this.reaction;
    const vector = (to, from) => { const a = store.frame(id, to); const b = store.frame(id, from); return Math.hypot(a[atom * 3] - b[atom * 3], a[atom * 3 + 1] - b[atom * 3 + 1], a[atom * 3 + 2] - b[atom * 3 + 2]); };
    const mode = store.mode(id);
    const amplitude = mode ? Math.hypot(mode[atom * 3], mode[atom * 3 + 1], mode[atom * 3 + 2]) : null;
    const pinned = s.pinned.has(atom);
    return h("div", {},
      h("p", { class: "atom-title" }, h("b", {}, `Atom ${atom}`), ` ${SM.geo.SYMBOLS[store.numbers[atom]]} · ${store.fixed[atom] ? "fixed" : "free"}`),
      h("dl", { class: "facts" },
        this.row("Saddle to reactant", `${vector(1, 0).toFixed(3)} Å`),
        this.row("Saddle to product", `${vector(2, 1).toFixed(3)} Å`),
        this.row("Reactant to product", `${vector(2, 0).toFixed(3)} Å`),
        this.row("Mode amplitude", amplitude === null ? "–" : amplitude.toFixed(3))),
      h("p", { class: "label" }, "Nearest neighbours"),
      h("div", { class: "neighbours" }, this.neighbours(atom).map(([distance, other]) => h("button", { type: "button", onclick: () => this.selectAtom(other) }, h("b", {}, String(other)), ` ${distance.toFixed(2)} Å`))),
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => this.actions.togglePin(atom) }, pinned ? "Unpin" : "Pin"),
        h("button", { type: "button", onclick: () => this.actions.focusAtom(atom) }, s.focusAtom === atom ? "Centre on movers" : "Centre here")));
  }

  pinnedPanel() {
    const { h } = SM;
    const { settings: s } = this;
    if (!s.pinned.size) return null;
    const list = [...s.pinned].sort((a, b) => a - b).join(", ");
    return h("section", { class: "inspect-block" },
      h("h3", {}, "Pinned atoms"),
      h("p", { class: "pinned-list" }, list),
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: (e) => { navigator.clipboard?.writeText(list); e.target.textContent = "Copied"; } }, "Copy indices"),
        h("button", { type: "button", onclick: () => this.actions.clearPins() }, "Clear")));
  }

  render() {
    if (this.reaction === null) return;
    const { h, fmt } = SM;
    const r = this.store.reactions[this.reaction];
    const m = r.motion;
    const score = this.store.score;
    let bench = null;
    if (score) {
      const state = score.status(this.reaction);
      const group = score.group(this.reaction);
      const info = group ? score.data.reference[group] : null;
      const partners = group ? score.partners(group) : [];
      const near = score.nearNull(this.reaction);
      const short = (sid) => sid.slice(4, 10);
      const foundSet = score.foundSet();
      bench = h("section", { class: `inspect-block bench-${state || "outside"}` },
        h("h3", {}, "Benchmark"),
        state ? h("p", {}, score.pill(this.reaction), ` by ${score.label()} at ${score.cutoff} Å`)
          : h("p", { class: "muted" }, `Not scored: ${score.outsideReason(this.reaction)}.`),
        info && Math.abs(info.barrier - r.barrier) >= 0.005
          ? h("p", { class: "note" }, `Scored barrier ${fmt.ev(info.barrier)} eV, from the seed energy. The ${fmt.ev(r.barrier)} eV above is from this reaction's DoubleMin endpoint.`) : null,
        partners.length ? h("p", { class: "note" }, `Symmetry family of ${partners.length + 1}: `,
          partners.map((g, i) => [i ? ", " : "", h("b", {}, short(g)), ` (${foundSet.has(g) ? "found" : "missed"})`]),
          `. Distinct saddles related by symmetries of the seed${info.via ? ` (this one by ${info.via})` : ""}: one mechanism, which the benchmark counts once per saddle.`) : null,
        near ? h("p", { class: "note" }, `Near-null: the product is only ${near.maxdev.toFixed(2)} Å from the seed (${fmt.signed(near.dE)} eV), just past the 0.2 Å identity cutoff. It may be the seed itself.`) : null);
    }
    const cls = this.actions.currentClass ? this.actions.currentClass() : null;
    const classCurvature = cls ? SM.classCurvature(this.store, cls) : null;
    SM.fill(this.root,
      bench,
      this.comparePanel(),
      h("section", { class: "inspect-block" },
        h("h3", {}, "Reaction"),
        h("dl", { class: "facts" },
          this.row("Barrier", `${fmt.ev(r.barrier)} eV`),
          this.row("Reverse barrier", `${fmt.ev(r.reverseBarrier)} eV`),
          this.row("Reaction energy", `${fmt.signed(r.reactionEnergy)} eV`),
          this.row("Endpoints", `${this.store.label(r.reactant)} → ${this.store.label(r.product)}`),
          this.row("Curvature", SM.fmt.curvature(r.curvature)),
          classCurvature ? this.row("Class curvature", `${classCurvature[2].toFixed(3)} median`) : null,
          classCurvature ? this.row("Class range", SM.fmt.curvatureRange(classCurvature[0], classCurvature[1])) : null,
          this.row("Found by", r.source),
          this.row("DoubleMins", String(r.nObs)),
          this.row("Hessian", "Unverified")),
        h("p", { class: "note" }, "Curvature is along the final Search mode, not a Hessian eigenvalue. DoubleMin endpoints are unverified; some stored Al minima are first-order saddles, so extra endpoints may be optimizer artifacts.")),
      h("section", { class: "inspect-block" },
        h("h3", {}, "Motion"),
        h("p", {}, SM.tag(SM.MOTION_NAMES[m.label])),
        h("dl", { class: "facts" },
          this.row("Atoms moved", `${m.moved} (${(m.fraction * 100).toFixed(1)}% of free)`),
          this.row("Largest move", `${m.max_A.toFixed(2)} Å`),
          SM.debug ? this.row("Direction alignment", m.resultant.toFixed(2)) : null,
          this.row("Saddle reaches", fmt.count(r.saddleMinima, "minimum")))),
      !SM.debug ? null : h("section", { class: "inspect-block" },
        h("h3", {}, "Motion groups"),
        h("p", { class: "muted" }, "Atoms moving > 0.5 Å, linked within 3.3 Å when pair distances change < 0.15 Å. Atom indices start at 0."),
        h("p", {}, fmt.count((m.groups || []).filter((g) => g.kind === "aligned").length, "aligned group")),
        (m.groups || []).map((g, i) => h("div", { class: "group-detail" },
          h("b", { style: `color:rgb(${SM.GROUP_COLOURS[i % SM.GROUP_COLOURS.length].map((v) => Math.round(v * 255)).join(",")})` },
            `Group ${i + 1}: ${g.kind === "aligned" ? "Aligned slide" : g.kind === "rotation" ? "Rigid rotation" : "Single-atom hop"}`),
          h("p", {}, `${fmt.count(g.size, "atom")} · mean ${g.meanMove_A.toFixed(2)} Å · alignment ${g.resultant.toFixed(2)}`),
          h("p", {}, `Direction [${g.direction.map((v) => v.toFixed(2)).join(", ")}]`),
          h("p", { class: "pinned-list" }, `Atoms: ${g.atoms.join(", ")}`)))),
      h("section", { class: "inspect-block" },
        h("h3", {}, "Mode alignment"),
        this.alignment(this.reaction)),
      h("section", { class: "inspect-block" }, h("h3", {}, "Atom"), this.atomPanel()),
      this.pinnedPanel(),
    );
  }
};
