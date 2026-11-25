/* Small DOM helpers and two reusable drawings: the energy strip and the endpoint map. */
window.SM = window.SM || {};

SM.h = function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  });
  SM.fill(node, ...children);
  return node;
};

/* Replace a node's children, skipping empty values and flattening nested lists. */
SM.fill = function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  return node;
};

SM.plural = (word) => (/(s|x|ch|sh)$/.test(word) ? `${word}es` : word === "minimum" ? "minima" : `${word}s`);

SM.fmt = {
  ev: (x, digits = 3) => (x === null || x === undefined ? "–" : x.toFixed(digits)),
  signed: (x, digits = 3) => (x === null || x === undefined ? "–" : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`),
  range: (a, b) => (Math.abs(a - b) < 0.0005 ? a.toFixed(3) : `${a.toFixed(3)}–${b.toFixed(3)}`),
  count: (n, word) => `${n.toLocaleString("en-US")} ${n === 1 ? word : SM.plural(word)}`,
};

SM.TAG_CLASS = {
  Local: "tag-local", Extended: "tag-extended", Collective: "tag-collective", "Aligned slide": "tag-aligned",
  "Multi-product": "tag-product", "Multi-endpoint saddle": "tag-endpoint", Unclassified: "tag-none",
};

SM.tag = (name) => SM.h("span", { class: `tag ${SM.TAG_CLASS[name] || ""}` }, name);

SM.classTags = (cls) => cls.tags.map((name) => name === "Multi-endpoint saddle"
  ? SM.h("span", { class: "tag tag-endpoint" }, `${cls.multiEndpointSaddles} of ${cls.saddles} saddles hit > 2 minima`)
  : SM.tag(name));

SM.GROUP_COLOURS = [[0.90,0.30,0.16], [0.10,0.60,0.72], [0.63,0.30,0.77], [0.20,0.65,0.32], [0.82,0.59,0.08], [0.90,0.35,0.62], [0.24,0.42,0.85], [0.45,0.60,0.13]];

SM.MOTION_NAMES = { local: "Local", extended: "Extended", collective: "Collective", aligned: "Aligned slide" };

/* Schematic reactant-saddle-product energy strip with a marker at the slider position. */
SM.EnergyStrip = class EnergyStrip {
  constructor(svg) { this.svg = svg; this.values = null; }

  set(reaction) {
    this.values = [0, reaction.barrier, reaction.reactionEnergy];
    this.render(0);
  }

  station(index, box) {
    const [low, high] = box.range;
    return [box.x[index], box.bottom - ((this.values[index] - low) / Math.max(high - low, 1e-6)) * (box.bottom - box.top)];
  }

  point(t, box) {
    const end = t < 0 ? 0 : 2;
    const a = this.station(1, box); const b = this.station(end, box);
    const u = Math.abs(t);
    return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * (1 - Math.cos(Math.PI * u)) / 2];
  }

  render(t) {
    if (!this.values) return;
    const NS = "http://www.w3.org/2000/svg";
    const svg = this.svg;
    svg.replaceChildren();
    svg.setAttribute("viewBox", "0 0 420 74");
    const box = { x: [42, 210, 378], top: 18, bottom: 54, range: [Math.min(...this.values), Math.max(...this.values)] };
    const add = (tag, attrs, text) => {
      const node = document.createElementNS(NS, tag);
      Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
      if (text !== undefined) node.textContent = text;
      svg.append(node);
      return node;
    };
    let d = "";
    for (let i = -40; i <= 40; i += 1) {
      const p = this.point(i / 40, box);
      d += `${i === -40 ? "M" : "L"}${p[0].toFixed(1)} ${p[1].toFixed(1)} `;
    }
    add("path", { d, fill: "none", stroke: "var(--muted)", "stroke-width": 2 });
    ["R", "S", "P"].forEach((name, i) => {
      const p = this.station(i, box);
      add("circle", { cx: p[0], cy: p[1], r: 3.5, fill: "var(--panel)", stroke: "var(--ink)", "stroke-width": 1.5 });
      add("text", { x: p[0], y: i === 1 ? p[1] - 8 : p[1] + 16, "text-anchor": "middle", "font-size": 11, fill: "var(--ink)" }, `${name} ${SM.fmt.signed(this.values[i])} eV`);
    });
    const marker = this.point(t, box);
    add("circle", { cx: marker[0], cy: marker[1], r: 5.5, fill: "var(--accent)", stroke: "var(--panel)", "stroke-width": 2 });
  }
};

/* Graph of the minima one saddle reached, with one edge per observed endpoint pair. */
SM.endpointMap = function endpointMap(store, reactions, selected, onPick) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 340 280");
  svg.setAttribute("class", "endpoint-map");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Minima reached from this saddle and the pairs observed together");
  const add = (tag, attrs, text) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text !== undefined) node.textContent = text;
    svg.append(node);
    return node;
  };
  const ids = [];
  const energy = {};
  reactions.forEach((r) => {
    [[r.reactant, r.eR], [r.product, r.eP]].forEach(([id, e]) => {
      if (!ids.includes(id)) ids.push(id);
      energy[id] = e;
    });
  });
  ids.sort((a, b) => Number(store.seeds.has(b)) - Number(store.seeds.has(a)));
  const place = {};
  ids.forEach((id, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / ids.length;
    place[id] = [170 + 88 * Math.cos(angle), 138 + 88 * Math.sin(angle), angle];
  });
  reactions.forEach((r) => {
    const a = place[r.reactant]; const b = place[r.product]; const on = r.id === selected;
    const line = add("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: on ? "var(--accent)" : "var(--muted)", "stroke-width": (on ? 4 : 1.6) + Math.min(r.nObs, 10) * 0.25, "stroke-linecap": "round", opacity: on ? 1 : 0.65 });
    line.style.cursor = "pointer";
    line.addEventListener("click", () => onPick(r.id));
  });
  add("rect", { x: 163, y: 131, width: 14, height: 14, transform: "rotate(45 170 138)", fill: "var(--panel)", stroke: "var(--ink)", "stroke-width": 1.5 });
  ids.forEach((id) => {
    const [x, y, angle] = place[id];
    const seed = store.seeds.has(id);
    add("circle", { cx: x, cy: y, r: seed ? 9 : 7, fill: seed ? "var(--ink)" : "var(--panel)", stroke: "var(--ink)", "stroke-width": 1.5 });
    const c = Math.cos(angle); const s = Math.sin(angle);
    const lx = x + 18 * c; const ly = y + 18 * s + (s < -0.5 ? -8 : s > 0.5 ? 12 : 3);
    const anchor = c > 0.3 ? "start" : c < -0.3 ? "end" : "middle";
    add("text", { x: lx, y: ly, "text-anchor": anchor, "font-size": 12, "font-weight": 600, fill: "var(--ink)" }, store.label(id));
    add("text", { x: lx, y: ly + 13, "text-anchor": anchor, "font-size": 10.5, fill: "var(--muted)" }, `${SM.fmt.signed(energy[id])} eV`);
  });
  return svg;
};

/* Page options injected by the builder: mode ("motif" keeps debugging panels, "bench" is the SaddleBench view),
   literature notes and an optional benchmark score overlay. */
SM.config = (() => {
  const node = document.getElementById("page-config");
  let config = {};
  try { config = node ? JSON.parse(node.textContent) : {}; } catch (error) { config = {}; }
  return Object.assign({ mode: "motif", brand: "SaddleMotif Explorer", lit: null, score: null, links: {} }, config);
})();
SM.debug = SM.config.mode !== "bench";

SM.fmt.curvature = (x) => (x === null || x === undefined ? "–" : `${x.toFixed(3)} eV/Å²`);
SM.fmt.curvatureRange = (a, b) => (a === null ? "–" : Math.abs(a - b) < 0.0005 ? `${a.toFixed(3)} eV/Å²` : `${a.toFixed(3)} to ${b.toFixed(3)} eV/Å²`);

/* Search-mode curvature range over a class's reactions (null when no curvature was stored). */
SM.classCurvature = (store, cls) => {
  if (cls._curvature !== undefined) return cls._curvature;
  const values = cls.reactions.map((i) => store.reactions[i].curvature).filter((v) => v !== null && v !== undefined);
  values.sort((a, b) => a - b);
  const mid = values.length >> 1;
  /* [lowest, highest, median]; a few unconverged Search modes give large outliers, so cards show the median. */
  cls._curvature = values.length ? [values[0], values[values.length - 1], values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2] : null;
  return cls._curvature;
};

/* Benchmark overlay. Reference groups are keyed by their representative saddle id; members are every campaign
   saddle id in the group, so explorer reactions (campaign ids) map to groups without geometry work in the browser. */
SM.Score = class Score {
  constructor(store, data) {
    this.store = store;
    this.data = data;
    this.methods = data.methods;
    this.cutoffs = data.cutoffs || ["0.20", "0.10", "0.05"];
    this.method = this.methods[0]?.id;
    this.cutoff = this.cutoffs[0];
    this.groupOf = new Map();
    Object.entries(data.reference).forEach(([group, info]) => (info.members || [group]).forEach((sid) => this.groupOf.set(sid, group)));
    this.excludedOf = new Map(Object.entries(data.excluded || {}));
    /* Optional symmetry families: groups related by a symmetry operation of the seed (one physical mechanism). */
    this.familyOf = new Map();
    Object.entries(data.reference).forEach(([group, info]) => { if (info.family) this.familyOf.set(group, info.family); });
    this.familySize = new Map();
    this.familyOf.forEach((family) => this.familySize.set(family, (this.familySize.get(family) || 0) + 1));
    this.seeds = new Set(data.seeds || []);
    this.listeners = new Set();
    this.found = new Map();
  }

  set(method, cutoff) {
    if (method) this.method = method;
    if (cutoff) this.cutoff = cutoff;
    this.listeners.forEach((fn) => fn());
  }

  label(method = this.method) { return this.methods.find((m) => m.id === method)?.label || method; }

  foundSet() {
    const key = `${this.method}|${this.cutoff}`;
    if (!this.found.has(key)) this.found.set(key, new Set(this.data.found[this.method]?.[this.cutoff] || []));
    return this.found.get(key);
  }

  group(reaction) { return this.groupOf.get(this.store.reactions[reaction].saddle) || null; }

  /* "found", "missed", or null for reactions whose saddle is not a reference saddle. */
  status(reaction) {
    const group = this.group(reaction);
    if (!group) return null;
    return this.foundSet().has(group) ? "found" : "missed";
  }

  /* Family of a reference group (its own id when it has no symmetry partner). */
  family(group) { return this.familyOf.get(group) || group; }

  /* Reference groups in the same symmetry family, excluding the group itself. */
  partners(group) {
    const family = this.familyOf.get(group);
    if (!family) return [];
    return Object.keys(this.data.reference).filter((g) => g !== group && this.familyOf.get(g) === family);
  }

  /* A missed group whose symmetry-equivalent partner was found: same mechanism, other image. */
  imageFound(group) { return this.partners(group).some((g) => this.foundSet().has(g)); }

  pill(reaction) {
    const state = this.status(reaction);
    if (state === "missed" && this.imageFound(this.group(reaction))) return SM.h("span", { class: "pill-missed pill-image", title: "Missed, but a symmetry-equivalent saddle (same mechanism, mirrored or rotated) was found" }, "Missed · image found");
    return SM.statusPill(state);
  }

  nearNull(reaction) { const g = this.group(reaction); return g ? this.data.reference[g].nearNull || null : null; }

  /* Why a saddle is outside the reference (shown in the inspector). */
  outsideReason(reaction) {
    const r = this.store.reactions[reaction];
    const excluded = this.excludedOf.get(r.saddle);
    if (excluded) return excluded;
    if (!r.seedConnected) return "Not connected to the seed";
    if (this.seeds.size && !this.seeds.has(r.reactant) && !this.seeds.has(r.product))
      return `Starts from another seed minimum (${r.reactant.slice(0, 10)}), which is not a benchmark case; this page scores only ${[...this.seeds].map((s) => s.slice(0, 10)).join(", ")}`;
    if (r.barrier >= 1) return "Barrier at or above 1 eV";
    return "Not a reference group (see the dataset notes)";
  }

  classCounts(cls) {
    const groups = new Set();
    cls.reactions.forEach((i) => { const g = this.group(i); if (g) groups.add(g); });
    const found = this.foundSet();
    let missed = 0; let images = 0;
    groups.forEach((g) => { if (!found.has(g)) { missed += 1; if (this.imageFound(g)) images += 1; } });
    return { reference: groups.size, missed, found: groups.size - missed, images };
  }

  totals() {
    const all = Object.keys(this.data.reference);
    const found = this.foundSet();
    const hit = all.filter((g) => found.has(g)).length;
    const families = new Set(all.map((g) => this.family(g)));
    const familiesFound = new Set(all.filter((g) => found.has(g)).map((g) => this.family(g)));
    return { reference: all.length, found: hit, missed: all.length - hit,
      families: this.familyOf.size ? families.size : null, familiesFound: familiesFound.size };
  }
};

SM.scoreBadge = (counts) => {
  if (!counts || !counts.reference) return null;
  if (!counts.missed) return SM.h("span", { class: "tag tag-found" }, `All ${counts.reference} reference found`);
  return SM.h("span", { class: "tag tag-missed", title: counts.images ? "Missed saddles whose mirror or rotated partner was found count as image found" : null },
    `${counts.missed} of ${counts.reference} reference missed${counts.images ? ` (${counts.images} image found)` : ""}`);
};

SM.statusPill = (status) => (status === "missed" ? SM.h("span", { class: "pill-missed" }, "Missed")
  : status === "found" ? SM.h("span", { class: "pill-found" }, "Found") : null);

/* Literature block for the header: one citation line, the intended reaction in a closed details element. */
SM.literature = (lit) => {
  if (!lit) return null;
  const { h } = SM;
  const links = (lit.links || []).map((link) => h("a", { href: link.href, target: "_blank", rel: "noopener" }, link.label));
  const body = [
    lit.mechanism ? h("p", {}, h("b", {}, "Intended reaction: "), lit.mechanism) : null,
    lit.expect ? h("p", {}, h("b", {}, "What to look for: "), lit.expect) : null,
    lit.system || lit.construction ? h("p", {}, h("b", {}, "Model: "), [lit.system, lit.construction].filter(Boolean).join(". ")) : null,
    lit.caveat ? h("p", {}, h("b", {}, "Caveat: "), lit.caveat) : null,
    lit.source ? h("p", { class: "muted" }, lit.source) : null,
  ];
  return h("details", { class: "lit" },
    h("summary", {}, h("span", { class: "lit-cite" }, links.length ? links : lit.citation || "Literature"),
      lit.mechanism ? h("span", { class: "lit-mech" }, ` · ${lit.mechanism}`) : null),
    h("div", { class: "lit-body" }, body));
};

/* Per-atom difference between frame fa of reaction a and frame fb of reaction b, the way SaddleBench measures
   identity (canonical core-drift maxdev): minimum-image displacement, subtract the mean displacement of the atoms
   at or below the median, minimum image again. Returns { per: Float32Array, max, top: [[atom, distance], ...] }. */
SM.frameDiff = (store, a, fa, b, fb) => {
  const key = `${a}:${fa}|${b}:${fb}`;
  store._diffs = store._diffs || new Map();
  if (store._diffs.has(key)) return store._diffs.get(key);
  const L = store.lattice || (store.lattice = new SM.geo.Lattice(store.cell, store.pbc));
  const A = store.frame(a, fa); const B = store.frame(b, fb);
  const n = store.n;
  const d = new Float64Array(n * 3); const mag = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const v = L.mic(B[i * 3] - A[i * 3], B[i * 3 + 1] - A[i * 3 + 1], B[i * 3 + 2] - A[i * 3 + 2]);
    d.set(v, i * 3); mag[i] = Math.hypot(v[0], v[1], v[2]);
  }
  const median = [...mag].sort((x, y) => x - y)[(n - 1) >> 1];
  const mean = [0, 0, 0]; let count = 0;
  for (let i = 0; i < n; i += 1) if (mag[i] <= median) { mean[0] += d[i * 3]; mean[1] += d[i * 3 + 1]; mean[2] += d[i * 3 + 2]; count += 1; }
  mean.forEach((v, k) => { mean[k] = v / Math.max(count, 1); });
  const per = new Float32Array(n); let max = 0;
  for (let i = 0; i < n; i += 1) {
    const v = L.mic(d[i * 3] - mean[0], d[i * 3 + 1] - mean[1], d[i * 3 + 2] - mean[2]);
    per[i] = Math.hypot(v[0], v[1], v[2]); if (per[i] > max) max = per[i];
  }
  const top = [...per.keys()].sort((x, y) => per[y] - per[x]).slice(0, 6).map((i) => [i, per[i]]);
  let sq = 0; for (let i = 0; i < n; i += 1) sq += per[i] * per[i];
  const out = { per, max, top, norm: Math.sqrt(sq) };
  if (store._diffs.size > 200) store._diffs.delete(store._diffs.keys().next().value);
  store._diffs.set(key, out);
  return out;
};

/* Rows (reactions) of one saddle; a saddle has one row per endpoint pair. */
SM.saddleRows = (store, saddle) => {
  if (!store._bySaddle) {
    store._bySaddle = new Map();
    store.reactions.forEach((r) => { if (!store._bySaddle.has(r.saddle)) store._bySaddle.set(r.saddle, []); store._bySaddle.get(r.saddle).push(r); });
  }
  return store._bySaddle.get(saddle) || [];
};

/* The row of reaction `other`'s saddle that best matches reaction `selected`: the same endpoint pair, else the same
   product, else the same reactant, else `other` itself. Compares like with like when saddles reach several minima. */
/* Pinned-ghost helpers (compare): frame index 0 reactant / 1 saddle / 2 product for a path position, or null between. */
SM.frameOfT = (t) => (t === -1 ? 0 : t === 0 ? 1 : t === 1 ? 2 : null);
SM.firstPinned = (settings, selected) => {
  for (const [id] of settings.compare) if (settings.ghostT && settings.ghostT.has(id)) return id;
  return null;
};

SM.matchRow = (store, selected, other) => {
  const a = store.reactions[selected]; const o = store.reactions[other];
  if (!a || !o || a.saddle === o.saddle) return other;
  const rows = SM.saddleRows(store, o.saddle);
  const key = (r) => [r.reactant, r.product].sort().join("|");
  return (rows.find((r) => key(r) === key(a)) || rows.find((r) => r.product === a.product)
    || rows.find((r) => r.reactant === a.reactant) || o).id;
};

/* Minima reached by each of two saddles: shared, only in A, only in B. */
SM.endpointSets = (store, saddleA, saddleB) => {
  const reach = (s) => new Set(SM.saddleRows(store, s).flatMap((r) => [r.reactant, r.product]));
  const A = reach(saddleA); const B = reach(saddleB);
  return { shared: [...A].filter((x) => B.has(x)), onlyA: [...A].filter((x) => !B.has(x)), onlyB: [...B].filter((x) => !A.has(x)) };
};

/* Data-quality flags for one structure, computed in the page from the payload. Thresholds are display choices. */
SM.FLAG_RULES = {
  at_endpoint: { title: "Saddle at or below an endpoint", why: "Barrier or reverse barrier under 0.01 eV. A first-order saddle should lie above both minima it connects; this usually means an unconverged endpoint or a flat region. maxdev and norm (all atoms, drift removed as in SaddleBench) are between the saddle and that low-barrier endpoint: near zero means the saddle sits on the endpoint." },
  near_null: { title: "Near-null reaction", why: "Reactant and product differ by less than 0.30 Å (maxdev, drift removed). Just past the 0.2 Å identity cutoff; possibly the same minimum." },
  below_seed: { title: "Lower minimum right next to the seed", why: "A seed reaction whose product is under 0.30 Å from the seed but more than 0.05 eV below it. The seed may not be the bottom of its basin." },
  seed_energy: { title: "Seed energy mismatch", why: "DoubleMin endpoints matched to the seed differ from its stored energy by more than 0.02 eV (spread shown). Tight seed convergence or precision differences." },
  multi_endpoint: { title: "Saddle reaching 3 or more minima", why: "DoubleMins from one saddle group scattered into several minima: a flat or poorly converged saddle, or loose DoubleMins. Such groups never reach the five-agree skip, so they collect many DoubleMins." },
  twins: { title: "Twin saddles", why: "Different saddle groups with a shared endpoint pair, saddle energies within 2 meV and saddles within 0.4 Å: possibly one saddle converged to slightly different points." },
  iso_products: { title: "Isoenergetic minima from one saddle", why: "One saddle whose DoubleMins reached two different minima (more than 0.2 Å apart) within 2 meV of each other, e.g. an H rotating about an O. Possibly one flat basin split by the 0.2 Å identity cutoff. Shows the maxdev between the two minima and the atoms that differ most." },
  new_slide: { title: "Slide caught by the new rule (Nov 22)", why: "Excluded as an aligned slide only since the Nov 22 rule: an infinite (periodic) group of at least 6 atoms translating together by 0.2 Å or more (the old rule needed 0.5 Å). Rows marked SUSPECT come first: a small chain (under 25% of free atoms) while something else moves more than 4× as far, which may be lattice relaxation around a real local event wrapping a small cell." },
  slide_local: { title: "Aligned slide + local event", why: "An infinite (periodic) group moves coherently (a slide) AND at least one atom moves more than 0.5 Å differently from the slide's mean displacement, e.g. a carbon flipping while the graphene layer shifts, or a row slide that pushes atoms into a pentagon near the adatom. Excluded from scoring like other slides (Nathan, 2026-11-24); listed so the local part can be reviewed. Largest deviations first." },
  fragment_shift: { title: "Whole fragment or layer moving together (not flagged as a slide)", why: "A connected fragment or layer of at least 6 atoms moves as a unit (coherence ≥ 0.9) but the reaction is not labelled an aligned slide. The slide test only builds rigid groups from atoms moving more than 0.5 Å, so a sheet shifting 0.2 to 0.5 Å is missed. Check whether the fragment is infinite (periodic) and should be excluded." },
};

SM.computeFlags = (store) => {
  if (store._flags) return store._flags;
  const seeds = store.seeds;
  const out = Object.fromEntries(Object.keys(SM.FLAG_RULES).map((k) => [k, []]));
  store.reactions.forEach((r) => {
    if (r.barrier < 0.01 || r.reverseBarrier < 0.01) {
      const side = r.barrier <= r.reverseBarrier ? 0 : 2;
      const near = SM.frameDiff(store, r.id, 1, r.id, side);
      out.at_endpoint.push({ reactions: [r.id], sort: Math.min(r.barrier, r.reverseBarrier),
        text: `barrier ${SM.fmt.ev(r.barrier)} / reverse ${SM.fmt.ev(r.reverseBarrier)} eV; saddle ↔ ${side ? "product" : "reactant"}: maxdev ${near.max.toFixed(3)} Å, norm ${near.norm.toFixed(3)} Å` });
    }
    const d = SM.frameDiff(store, r.id, 0, r.id, 2).max;
    if (d < 0.3) out.near_null.push({ reactions: [r.id], text: `reactant ↔ product ${d.toFixed(2)} Å, ΔE ${SM.fmt.signed(r.reactionEnergy)} eV`, sort: d });
    if (seeds.has(r.reactant) && d < 0.3 && r.eP < -0.05) out.below_seed.push({ reactions: [r.id], text: `product ${store.label(r.product)} ${d.toFixed(2)} Å from the seed, ${SM.fmt.signed(r.eP)} eV`, sort: r.eP });
  });
  const atSeed = store.reactions.filter((r) => seeds.has(r.reactant));
  const off = atSeed.filter((r) => Math.abs(r.eR) > 0.02);
  if (off.length) {
    const e = atSeed.map((r) => r.eR);
    out.seed_energy.push({ reactions: off.sort((a, b) => Math.abs(b.eR) - Math.abs(a.eR)).slice(0, 12).map((r) => r.id),
      text: `${off.length} of ${atSeed.length} seed endpoints off by > 0.02 eV; range ${SM.fmt.signed(Math.min(...e))} to ${SM.fmt.signed(Math.max(...e))} eV`, sort: -off.length });
  }
  const bySaddle = new Map();
  store.reactions.forEach((r) => { if (!bySaddle.has(r.saddle)) bySaddle.set(r.saddle, []); bySaddle.get(r.saddle).push(r); });
  bySaddle.forEach((rows) => {
    const n = rows[0].saddleMinima;
    if (n >= 3) out.multi_endpoint.push({ reactions: [...rows].sort((a, b) => b.nObs - a.nObs).slice(0, 8).map((r) => r.id),
      text: `${n} minima, ${rows.length} endpoint pairs, ${rows.reduce((t, r) => t + r.nObs, 0)} DoubleMins`, sort: -n });
  });
  const byPair = new Map();
  store.reactions.forEach((r) => { const k = [r.reactant, r.product].sort().join("|"); if (!byPair.has(k)) byPair.set(k, []); byPair.get(k).push(r); });
  const seen = new Set();
  byPair.forEach((rows) => {
    for (let i = 0; i < rows.length; i += 1) for (let j = i + 1; j < rows.length; j += 1) {
      const a = rows[i]; const b = rows[j];
      if (a.saddle === b.saddle || Math.abs(a.eS - b.eS) >= 0.002) continue;
      const key = [a.saddle, b.saddle].sort().join("|");
      if (seen.has(key)) continue;
      const d = SM.frameDiff(store, a.id, 1, b.id, 1).max;
      if (d > 0.4) continue;
      seen.add(key);
      out.twins.push({ reactions: [a.id, b.id], text: `${a.saddle.slice(4, 10)} vs ${b.saddle.slice(4, 10)}: saddles ${d.toFixed(2)} Å apart, ΔE ${(1000 * (b.eS - a.eS)).toFixed(1)} meV, both ${store.label(a.reactant)} → ${store.label(a.product)}`, sort: d });
    }
  });
  bySaddle.forEach((rows) => {
    /* Distinct minima reached from this saddle, with energies relative to the seed; compare each pair once. */
    const minima = new Map();
    rows.forEach((r) => {
      if (!minima.has(r.reactant)) minima.set(r.reactant, { row: r.id, side: 0, e: r.eR });
      if (!minima.has(r.product)) minima.set(r.product, { row: r.id, side: 2, e: r.eP });
    });
    const list = [...minima.entries()];
    for (let i = 0; i < list.length; i += 1) for (let j = i + 1; j < list.length; j += 1) {
      const [ma, a] = list[i]; const [mb, b] = list[j];
      if (Math.abs(a.e - b.e) >= 0.002) continue;
      const diff = SM.frameDiff(store, a.row, a.side, b.row, b.side);
      if (diff.max <= 0.2) continue;
      const atoms = diff.top.filter(([, v]) => v > 0.2).slice(0, 4).map(([k, v]) => `${SM.geo.SYMBOLS[store.numbers[k]]}${k} ${v.toFixed(2)}`).join(", ");
      const ids = [...new Set([a.row, b.row])];
      out.iso_products.push({ reactions: ids, sort: -diff.max,
        text: `saddle ${rows[0].saddle.slice(4, 10)}: ${store.label(ma)} vs ${store.label(mb)}, ΔE ${(1000 * (b.e - a.e)).toFixed(1)} meV, maxdev ${diff.max.toFixed(2)} Å (${atoms})` });
    }
  });
  const seenSlide = new Set();
  store.reactions.forEach((r) => {
    const slide = (r.motion?.groups || []).filter((x) => x.via === "translation").sort((a, b) => b.size - a.size)[0];
    if (!slide || seenSlide.has(r.saddle)) return;
    seenSlide.add(r.saddle);
    const inside = new Set(slide.atoms); const sizes = store.moveSizes(r.id);
    let other = 0; for (let i = 0; i < store.n; i += 1) if (!inside.has(i) && !store.fixed[i] && sizes[i] > other) other = sizes[i];
    const share = slide.size / store.nFree;
    const suspect = share < 0.25 && other > 4 * slide.meanMove_A;
    out.new_slide.push({ reactions: [r.id], sort: (suspect ? -1000 : 0) - other / slide.meanMove_A,
      text: `${suspect ? "SUSPECT · " : ""}${slide.size}/${store.nFree} free atoms slide ${slide.meanMove_A.toFixed(2)} Å; largest other move ${other.toFixed(2)} Å; barrier ${SM.fmt.ev(r.barrier)} eV` });
  });
  const seenLocal = new Set();
  store.reactions.forEach((r) => {
    const slide = (r.motion?.groups || []).filter((x) => x.kind === "aligned" && x.periodic).sort((a, b) => b.size - a.size)[0];
    if (!slide || seenLocal.has(r.saddle)) return;
    seenLocal.add(r.saddle);
    const d = store.difference(r.id, 2, 0); const mean = [0, 0, 0];
    slide.atoms.forEach((a) => { mean[0] += d[a * 3]; mean[1] += d[a * 3 + 1]; mean[2] += d[a * 3 + 2]; });
    mean.forEach((v, k) => { mean[k] = v / slide.atoms.length; });
    const dev = [];
    for (let i = 0; i < store.n; i += 1) {
      if (store.fixed[i]) continue;
      const x = Math.hypot(d[i * 3] - mean[0], d[i * 3 + 1] - mean[1], d[i * 3 + 2] - mean[2]);
      if (x > 0.5 && Math.hypot(d[i * 3], d[i * 3 + 1], d[i * 3 + 2]) > 0.2) dev.push([x, i]);
    }
    if (!dev.length) return;
    dev.sort((a, b) => b[0] - a[0]);
    const sym = (i) => `${SM.geo.SYMBOLS ? SM.geo.SYMBOLS[store.numbers[i]] || store.numbers[i] : store.numbers[i]}${i}`;
    out.slide_local.push({ reactions: [r.id], sort: -dev[0][0],
      text: `${slide.size}/${store.nFree} free atoms slide ${Math.hypot(...mean).toFixed(2)} Å; ${dev.length} atom${dev.length > 1 ? "s" : ""} off the slide (${dev.slice(0, 3).map(([x, i]) => `${sym(i)} ${x.toFixed(2)} Å`).join(", ")}); barrier ${SM.fmt.ev(r.barrier)} eV` });
  });
  store.reactions.forEach((r) => {
    const m = r.motion || {}; const g = m.group;
    const aligned = (m.groups || []).some((x) => x.kind === "aligned");
    if (!g || aligned || g.atoms < 6 || g.fraction < 0.9 || m.resultant < 0.9) return;
    out.fragment_shift.push({ reactions: [r.id], sort: -g.atoms,
      text: `${g.atoms}-atom ${g.kind} moves together (coherence ${m.resultant.toFixed(2)}, largest move ${(m.max_A ?? 0).toFixed(2)} Å), barrier ${SM.fmt.ev(r.barrier)} eV, labelled ${m.label}` });
  });
  Object.values(out).forEach((list) => list.sort((x, y) => x.sort - y.sort));
  store._flags = out;
  return out;
};
