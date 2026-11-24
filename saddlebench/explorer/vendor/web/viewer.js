/* A 3D view of one reaction. Viewers sharing a ViewSettings object stay in sync. */
window.SM = window.SM || {};

SM.VECTORS = [
  { id: "sr", label: "Saddle − reactant", token: "--v1", field: (s, r) => s.difference(r, 1, 0) },
  { id: "ps", label: "Product − saddle", token: "--v2", field: (s, r) => s.difference(r, 2, 1) },
  { id: "pr", label: "Product − reactant", token: "--v3", field: (s, r) => s.difference(r, 2, 0) },
  { id: "mode", label: "Search final mode", token: "--v4", field: (s, r) => s.mode(r), unit: true },
];

/* Tilt turns all the way round (no clamp), so the underside of two-sided slabs can be seen. */
SM.wrapAngle = (a) => Math.atan2(Math.sin(a), Math.cos(a));

SM.ViewSettings = class ViewSettings {
  constructor(overrides = {}) {
    Object.assign(this, {
      t: 0, yaw: 0.5, pitch: -1.0, zoom: 1, panX: 0, panY: 0,
      /* focus clips to a sphere around the movers (legacy); box > 0 clips to a box shaped like the cell, box × the cell
         on each repeated axis, centred on the movers (main view and cards); box 0 draws whole supercells. */
      focus: true, radius: 12, focusAtom: null, supercell: 1, box: 0, fade: 0,
      threshold: 0.2, colour: "highlight", vectors: new Set(), vectorScale: 1, vectorMin: 0.2,
      pinned: new Set(), labels: true, selected: null,
      /* atomScale multiplies drawn radii; keepView keeps the camera pivot and image wrapping when the reaction
         changes; compare maps reaction id -> colour slot, and those reactions' movers are drawn in that colour. */
      atomScale: 1, keepView: false, compare: new Map(), roll: 0, bonds: false,
      /* orient: view rotation rows (screen x, y, depth in world coordinates). Built from yaw/pitch/roll, then every
         drag turns it about the current screen axes, so a spin never changes what a later drag does. */
      orient: null,
    }, overrides);
    this.viewers = new Set();
  }

  redraw() { this.viewers.forEach((viewer) => viewer.draw()); }

  resetCamera() {
    Object.assign(this, { yaw: 0.5, pitch: -1.0, roll: 0, orient: null, zoom: 1, panX: 0, panY: 0 });
    this.viewers.forEach((viewer) => { viewer.centre = null; });
    this.redraw();
  }

  camera(name) {
    const presets = { top: [0, 0], side: [0, -Math.PI / 2], bottom: [0, Math.PI], iso: [0.5, -1.0] };
    [this.yaw, this.pitch] = presets[name];
    this.roll = 0; this.orient = null;
    this.redraw();
  }

  /* Turn the view about a screen axis (0 horizontal, 1 vertical, 2 the screen normal). */
  turn(axis, angle) {
    const R = SM.viewRotation(this);
    const [i, j] = [[1, 2], [2, 0], [0, 1]][axis];
    const c = Math.cos(angle); const n = Math.sin(angle);
    const ri = R[i]; const rj = R[j];
    R[i] = ri.map((v, k) => c * v - n * rj[k]);
    R[j] = ri.map((v, k) => n * v + c * rj[k]);
    /* Re-orthonormalise so many small turns do not drift. */
    const unit = (v) => { const m = Math.hypot(v[0], v[1], v[2]) || 1; return v.map((x) => x / m); };
    const x = unit(R[0]); const d = x[0] * R[1][0] + x[1] * R[1][1] + x[2] * R[1][2];
    const y = unit(R[1].map((v, k) => v - d * x[k]));
    this.orient = [x, y, [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]];
  }
};

/* View rotation rows: yaw/pitch (trackball), then roll spins the picture in the screen plane. */
SM.viewRotation = (s) => {
  if (s.orient) return s.orient;
  const R = SM.geo.rotation(s.yaw, s.pitch);
  const c = Math.cos(s.roll || 0); const n = Math.sin(s.roll || 0);
  s.orient = [[c * R[0][0] - n * R[1][0], c * R[0][1] - n * R[1][1], c * R[0][2] - n * R[1][2]],
    [n * R[0][0] + c * R[1][0], n * R[0][1] + c * R[1][1], n * R[0][2] + c * R[1][2]], R[2].slice()];
  return s.orient;
};

SM.Viewer = class Viewer {
  constructor(canvas, store, renderer, settings, options = {}) {
    Object.assign(this, { canvas, store, renderer, settings, options });
    this.ctx = canvas.getContext("2d");
    this.lattice = store.lattice || (store.lattice = new SM.geo.Lattice(store.cell, store.pbc));
    this.reaction = null;
    this.buffer = new Float32Array(SM.STRIDE * 4096);
    this.positions = new Float32Array(store.n * 3);
    this.drawn = [];
    this.fields = new Map();
    settings.viewers.add(this);
    if (options.interactive !== false) this.bind();
  }

  dispose() { this.settings.viewers.delete(this); }

  setReaction(reaction) {
    this.reaction = reaction;
    this.fields.clear();
    if (!this.settings.keepView) this.centre = null;
    this.draw();
  }

  field(id) {
    if (!this.fields.has(id)) {
      const spec = SM.VECTORS.find((v) => v.id === id);
      this.fields.set(id, spec.field(this.store, this.reaction));
    }
    return this.fields.get(id);
  }

  focusCentre(positions) {
    const s = this.settings;
    if (s.focusAtom !== null) return [positions[s.focusAtom * 3], positions[s.focusAtom * 3 + 1], positions[s.focusAtom * 3 + 2]];
    /* anchor "cell": the cell's own centre (slab: middle of the atoms in z), the same for every reaction, so the
       drawn cell never shifts; anchor "movers" (cards, and the Reaction option) centres on this reaction. */
    if (s.anchor === "cell") {
      if (!this.cellCentre) {
        const c = this.lattice.cell; const axes = this.repeatAxes();
        const centre = [0, 1, 2].map((k) => 0.5 * (c[0][k] + c[1][k]) + (axes[2] ? 0.5 * c[2][k] : 0));
        if (!axes[2]) {
          let low = Infinity; let high = -Infinity;
          for (let i = 0; i < this.store.n; i += 1) { low = Math.min(low, this.store.base[i * 3 + 2]); high = Math.max(high, this.store.base[i * 3 + 2]); }
          centre[2] = 0.5 * (low + high);
        }
        this.cellCentre = centre;
      }
      return this.cellCentre;
    }
    /* Reaction centre: recomputed for each reaction (keepView keeps zoom and rotation, not a stale centre). */
    if (!this.centre || this.centreReaction !== this.reaction) {
      this.centreReaction = this.reaction;
      const saddle = this.store.frame(this.reaction, 1);
      let movers = this.store.movers(this.reaction, this.store.thresholds.moved_A);
      if (!movers.length) movers = this.store.movers(this.reaction, 0.05);
      if (!movers.length) movers = [0];
      this.centre = SM.geo.periodicCentre(this.lattice, saddle, movers);
    }
    return this.centre;
  }

  colourFor(atom, sizes, zRange, palette, primary) {
    const s = this.settings;
    const element = SM.geo.ELEMENT_RGB[this.store.numbers[atom]] || SM.geo.ELEMENT_RGB[0];
    if (s.colour === "group") {
      const groups = this.store.reactions[this.reaction].motion.groups || [];
      const index = groups.findIndex((g) => g.atoms.includes(atom));
      return index < 0 ? palette.neutral : SM.GROUP_COLOURS[index % SM.GROUP_COLOURS.length];
    }
    if (s.colour === "element") return element;
    /* "site": categorical colour per site type (debug page, s.atomSites). */
    if (s.colour === "site" && s.atomSites) return SM.GROUP_COLOURS[s.atomSites[atom] % SM.GROUP_COLOURS.length];
    /* "values": per-atom numbers set by the page (debug site map); zero stays neutral, the rest ramp to the maximum. */
    if (s.colour === "values" && s.atomValues) {
      const v = s.atomValues[atom];
      if (!(v > 0)) return this.store.fixed[atom] ? palette.fixed : palette.neutral;
      return SM.geo.ramp(0.15 + 0.85 * Math.min(1, v / Math.max(s.atomValuesMax || 1, 1e-9)));
    }
    if (s.colour === "diff") {
      const other = [...s.compare.keys()].find((id) => this.store.reactions[id].saddle !== this.store.reactions[this.reaction].saddle);
      if (other === undefined) return element;
      const diff = SM.frameDiff(this.store, this.reaction, 1, SM.matchRow(this.store, this.reaction, other), 1);
      return SM.geo.ramp(Math.min(1, diff.per[atom] / Math.max(diff.max, 0.2)));
    }
    if (s.colour === "displacement") return SM.geo.ramp(sizes[atom] / Math.max(zRange.maxSize, 0.5));
    if (s.colour === "height") return SM.geo.ramp((this.store.base[atom * 3 + 2] - zRange.low) / Math.max(zRange.high - zRange.low, 1));
    if (s.colour === "fixed") return this.store.fixed[atom] ? palette.fixed : palette.free;
    /* "highlight": element colours everywhere; movers keep full colour and get a ring (see isMover), the rest fade a little. */
    if (this.isMover(atom, sizes, primary)) return element;
    const compared = this.compareColour(atom, palette);
    if (compared) return compared;
    return [element[0] * 0.72 + palette.neutral[0] * 0.28, element[1] * 0.72 + palette.neutral[1] * 0.28, element[2] * 0.72 + palette.neutral[2] * 0.28];
  }

  /* Box size (in cells) whose shortest repeated edge spans 2 × radius, kept between 0.5 and 3 cells (card previews). */
  boxFor(radius) {
    const axes = this.repeatAxes();
    const short = this.lattice.cell.filter((v, k) => axes[k]).map((v) => Math.hypot(v[0], v[1], v[2]));
    if (!short.length) return 1;
    return Math.max(0.5, Math.min(3, (2 * radius) / Math.min(...short)));
  }

  /* Every drawn image as [atom, shift x, y, z, primary], cached per reaction, centre and box/supercell size.
     Box mode: an atom image is kept when its saddle position lies in [-box/2, box/2) cells from the centre on every
     repeated axis (membership from the saddle frame, so atoms do not pop across the box edge while animating);
     primary is its image in the home cell [-1/2, 1/2). Supercell mode: whole cells around the wrapped home cell. */
  imageList(centre) {
    const s = this.settings;
    const key = `${this.reaction}|${centre.join(",")}|${s.box}|${s.supercell}`;
    if (this.imageCache && this.imageCache.key === key) return this.imageCache.list;
    const { store, lattice } = this;
    const axes = this.repeatAxes();
    const list = [];
    if (s.box > 0) {
      const saddle = store.frame(this.reaction, 1); const h = s.box / 2;
      for (let atom = 0; atom < store.n; atom += 1) {
        const i = atom * 3;
        const f = lattice.fractional(saddle[i] - centre[0], saddle[i + 1] - centre[1], saddle[i + 2] - centre[2]);
        const ranges = f.map((v, k) => {
          if (!axes[k]) return [0];
          const out = [];
          for (let n = Math.ceil(-h - v); n + v < h; n += 1) out.push(n);
          return out;
        });
        for (const a of ranges[0]) for (const b of ranges[1]) for (const c of ranges[2]) {
          const shift = lattice.cartesian(a, b, c);
          const home = [a, b, c].every((n, k) => !axes[k] || (n + f[k] >= -0.5 && n + f[k] < 0.5));
          list.push([atom, shift[0], shift[1], shift[2], home]);
        }
      }
    } else {
      const wrap = this.wrapShifts(centre);
      const span = axes.map((p) => (p ? s.supercell : 1));
      const half = span.map((n) => Math.floor((n - 1) / 2));
      for (let u = -half[0]; u < span[0] - half[0]; u += 1)
        for (let v = -half[1]; v < span[1] - half[1]; v += 1)
          for (let w = -half[2]; w < span[2] - half[2]; w += 1) {
            const shift = lattice.cartesian(u, v, w);
            const home = u === 0 && v === 0 && w === 0;
            for (let atom = 0; atom < store.n; atom += 1) {
              const i = atom * 3;
              list.push([atom, wrap[i] + shift[0], wrap[i + 1] + shift[1], wrap[i + 2] + shift[2], home]);
            }
          }
    }
    this.imageCache = { key, list };
    return list;
  }

  /* Bonds between drawn atoms (all images): distance under 1.15 × the sum of covalent radii, found with a grid.
     Returns a flat array [x1,y1,z1,x2,y2,z2,...]. */
  bonds() {
    const R = SM.geo.RADII; const nums = this.store.numbers; const drawn = this.drawn;
    let maxR = 0;
    drawn.forEach((d) => { maxR = Math.max(maxR, R[nums[d.atom]] || 1); });
    const cell = 2.3 * maxR; const grid = new Map();
    const key = (x, y, z) => `${Math.floor(x / cell)},${Math.floor(y / cell)},${Math.floor(z / cell)}`;
    drawn.forEach((d, i) => { const k = key(d.x, d.y, d.z); if (!grid.has(k)) grid.set(k, []); grid.get(k).push(i); });
    const out = [];
    drawn.forEach((a, i) => {
      const gx = Math.floor(a.x / cell); const gy = Math.floor(a.y / cell); const gz = Math.floor(a.z / cell);
      const ra = R[nums[a.atom]] || 1;
      for (let u = -1; u <= 1; u += 1) for (let v = -1; v <= 1; v += 1) for (let w = -1; w <= 1; w += 1) {
        const list = grid.get(`${gx + u},${gy + v},${gz + w}`);
        if (!list) continue;
        for (const j of list) {
          if (j <= i) continue;
          const b = drawn[j];
          const limit = 1.15 * (ra + (R[nums[b.atom]] || 1));
          const dx = b.x - a.x; const dy = b.y - a.y; const dz = b.z - a.z;
          const d2 = dx * dx + dy * dy + dz * dz;
          if (d2 < limit * limit && d2 > 0.16) out.push(a.x, a.y, a.z, b.x, b.y, b.z);
        }
      }
    });
    return out;
  }

  /* Colour of the first compared reaction (other than the shown one) in which this atom moves, else null. */
  compareColour(atom, palette) {
    const s = this.settings;
    if (!s.compare.size || this.store.fixed[atom]) return null;
    for (const [reaction, slot] of s.compare) {
      if (reaction === this.reaction) continue;
      if (this.store.moveSizes(reaction)[atom] > s.threshold) return palette.compare[slot % palette.compare.length];
    }
    return null;
  }

  isMover(atom, sizes, primary) {
    /* Every supercell image is highlighted, not only the home cell. */
    return this.settings.colour === "highlight" && sizes[atom] > this.settings.threshold && !this.store.fixed[atom];
  }

  /* Atoms that never fade, in every supercell image: movers above the threshold, compared movers, pinned atoms and the
     selected atom. */
  emphasised(atom, sizes) {
    const s = this.settings;
    if (s.colour === "values" && s.atomValues) return s.atomValues[atom] > 0 || s.pinned.has(atom) || s.selected === atom;
    return (sizes[atom] > s.threshold && !this.store.fixed[atom]) || s.pinned.has(atom) || s.selected === atom
      || (s.compare.size > 0 && s.colour === "highlight" && this.compareColour(atom, this.lastPalette) !== null);
  }

  /* Per-atom lattice shift putting each atom's saddle position in the image nearest centre (fixed for the reaction). */
  wrapShifts(centre) {
    if (this.wrap && this.wrap.centre === centre && this.wrap.reaction === this.reaction) return this.wrap.shifts;
    const { store, lattice } = this;
    const axes = this.repeatAxes();
    const saddle = store.frame(this.reaction, 1);
    const shifts = new Float32Array(store.n * 3);
    for (let i = 0; i < store.n; i += 1) {
      const f = lattice.fractional(saddle[i * 3] - centre[0], saddle[i * 3 + 1] - centre[1], saddle[i * 3 + 2] - centre[2]);
      const n = f.map((value, k) => (axes[k] ? -Math.round(value) : 0));
      const v = lattice.cartesian(n[0], n[1], n[2]);
      shifts.set(v, i * 3);
    }
    this.wrap = { centre, reaction: this.reaction, shifts };
    return shifts;
  }

  /* Periodic axes that the supercell repeats: a and b when periodic; c only for bulk cells (atoms fill most of c). */
  repeatAxes() {
    if (this.axes) return this.axes;
    const { store } = this;
    let bulk = false;
    if (store.pbc[2]) {
      let low = Infinity; let high = -Infinity;
      for (let i = 0; i < store.n; i += 1) {
        const f = this.lattice.fractional(store.base[i * 3], store.base[i * 3 + 1], store.base[i * 3 + 2])[2];
        low = Math.min(low, f); high = Math.max(high, f);
      }
      bulk = high - low > 0.6;
    }
    this.axes = [store.pbc[0], store.pbc[1], store.pbc[2] && bulk];
    return this.axes;
  }

  palette() {
    const style = getComputedStyle(this.canvas);
    const read = (name) => {
      const hex = style.getPropertyValue(name).trim().replace("#", "");
      return [parseInt(hex.slice(0, 2), 16) / 255, parseInt(hex.slice(2, 4), 16) / 255, parseInt(hex.slice(4, 6), 16) / 255];
    };
    this.lastPalette = { accent: read("--accent"), neutral: read("--atom"), fog: read("--stage"), fixed: read("--fixed"), free: read("--free"),
      compare: (SM.FAN_COLOURS || []).map(read) };
    return this.lastPalette;
  }

  buildScene(palette) {
    const { store, settings: s, lattice } = this;
    const positions = store.path(this.reaction, s.t, this.positions);
    const sizes = store.moveSizes(this.reaction);
    let low = Infinity; let high = -Infinity; let maxSize = 0;
    for (let i = 0; i < store.n; i += 1) {
      low = Math.min(low, store.base[i * 3 + 2]); high = Math.max(high, store.base[i * 3 + 2]);
      if (sizes[i] > maxSize) maxSize = sizes[i];
    }
    const range = { low, high, maxSize };
    const drawn = [];
    const centre = this.focusCentre(positions);
    let cursor = 0;
    const push = (atom, x, y, z, primary) => {
      if ((cursor + 1) * SM.STRIDE > this.buffer.length) {
        const bigger = new Float32Array(this.buffer.length * 2);
        bigger.set(this.buffer);
        this.buffer = bigger;
      }
      const colour = this.colourFor(atom, sizes, range, palette, primary);
      const alpha = s.fade > 0 && !this.emphasised(atom, sizes, primary) ? 1 - s.fade : 1;
      this.buffer.set([x, y, z, SM.geo.RADII[store.numbers[atom]] * 0.92 * s.atomScale, colour[0], colour[1], colour[2], (this.isMover(atom, sizes, primary) ? 1 : 0) + (s.fixedMarks !== false && store.fixed[atom] ? 2 : 0), alpha], cursor * SM.STRIDE);
      drawn.push({ atom, x, y, z, primary });
      cursor += 1;
    };
    if (s.focus) {
      for (let atom = 0; atom < store.n; atom += 1) {
        const i = atom * 3;
        lattice.imagesWithin(positions[i], positions[i + 1], positions[i + 2], centre, s.radius, (x, y, z, primary) => push(atom, x, y, z, primary));
      }
    } else {
      /* Focus box or whole cells, every atom placed relative to the movers so the reaction is never split by an edge. */
      for (const [atom, sx, sy, sz, home] of this.imageList(centre)) {
        const i = atom * 3;
        push(atom, positions[i] + sx, positions[i + 1] + sy, positions[i + 2] + sz, home);
      }
    }
    /* Ghosts: each compared reaction's movers at the same path position, translucent in its colour, so two
       reactions moving the same atoms (e.g. mirror-image saddles) show both paths. */
    if (!s.focus && s.compare.size) {
      const images = this.imageList(centre);
      this.ghostBuffer = this.ghostBuffer || new Float32Array(store.n * 3);
      for (const [reaction, slot] of s.compare) {
        if (reaction === this.reaction) continue;
        const row = SM.matchRow ? SM.matchRow(store, this.reaction, reaction) : reaction;
        const ghost = store.path(row, s.t, this.ghostBuffer);
        const colour = palette.compare[slot % palette.compare.length];
        const movers = new Set(store.movers(row, s.threshold));
        for (const [atom, sx, sy, sz] of images) {
          if (!movers.has(atom)) continue;
          const i = atom * 3;
          if ((cursor + 1) * SM.STRIDE > this.buffer.length) {
            const bigger = new Float32Array(this.buffer.length * 2); bigger.set(this.buffer); this.buffer = bigger;
          }
          this.buffer.set([ghost[i] + sx, ghost[i + 1] + sy, ghost[i + 2] + sz,
            SM.geo.RADII[store.numbers[atom]] * 0.92 * s.atomScale * 0.98, colour[0], colour[1], colour[2], 0, 0.55], cursor * SM.STRIDE);
          cursor += 1;
        }
      }
    }
    this.drawn = drawn;
    this.lastCentre = centre;
    return { count: cursor, centre };
  }

  /* The camera frames a sphere of this radius around the pivot (movers); zoom out to see whole cells. Card previews
     (frameBox) frame the focus box instead. */
  viewSpan() {
    const s = this.settings;
    if ((this.options.frameBox || s.anchor === "cell") && s.box > 0) {
      const axes = this.repeatAxes();
      const half = this.lattice.cell.filter((v, k) => axes[k]).map((v) => (s.box * Math.hypot(v[0], v[1], v[2])) / 2);
      if (half.length) return Math.max(5, ...half) * 0.95;
    }
    return s.radius * 1.02;
  }

  project(x, y, z, view) {
    const { R, pivot, scale, width, height } = view;
    const dx = x - pivot[0]; const dy = y - pivot[1]; const dz = z - pivot[2];
    const s = this.settings;
    return [width / 2 + (R[0][0] * dx + R[0][1] * dy + R[0][2] * dz) * scale + s.panX,
      height / 2 - (R[1][0] * dx + R[1][1] * dy + R[1][2] * dz) * scale - s.panY,
      R[2][0] * dx + R[2][1] * dy + R[2][2] * dz];
  }

  draw() {
    if (this.reaction === null) return;
    const width = this.canvas.clientWidth; const height = this.canvas.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    if (this.canvas.width !== Math.round(width * ratio) || this.canvas.height !== Math.round(height * ratio)) {
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
    }
    const ctx = this.ctx;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const s = this.settings;
    const palette = this.palette();
    const { count, centre } = this.buildScene(palette);
    const R = SM.viewRotation(s);
    const scale = (Math.min(width, height) / (2 * this.viewSpan())) * s.zoom;
    const pivot = centre;
    const bonds = s.bonds ? this.bonds() : null;
    this.renderer.draw(ctx, width, height, ratio, { data: this.buffer, count, rotation: R, pivot, scale, panX: s.panX, panY: s.panY, fog: palette.fog, ring: palette.accent,
      bonds, bondColour: getComputedStyle(this.canvas).getPropertyValue("--muted").trim() });
    this.view = { R, pivot, scale, width, height };
    if (!s.focus && this.options.overlays !== false) this.drawCell(ctx);
    if (this.options.overlays !== false) {
      this.drawVectors(ctx);
      if (this.options.overlay) this.options.overlay(ctx, this);
      this.drawPins(ctx);
      this.drawMeasure(ctx);
      this.drawLegend(ctx);
    }
  }

  /* Home cell: the full box (all three lattice vectors, slabs included) plus labelled a, b, c arrows at its origin. */
  drawCell(ctx) {
    const L = this.lattice;
    const axes = this.repeatAxes();
    /* The drawn cell is centred on the movers along wrapped axes (see wrapShifts); c starts at 0 for slabs. */
    const c = this.lastCentre ? L.fractional(...this.lastCentre) : [0.5, 0.5, 0.5];
    const o = c.map((value, k) => (axes[k] ? value - 0.5 : 0));
    const p = (u, v, w) => {
      const x = L.cartesian(u + o[0], v + o[1], w + o[2]);
      return this.project(x[0], x[1], x[2], this.view);
    };
    const style = getComputedStyle(this.canvas);
    const muted = style.getPropertyValue("--muted").trim();
    ctx.strokeStyle = muted;
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    const edges = [[[0,0,0],[1,0,0]],[[0,1,0],[1,1,0]],[[0,0,1],[1,0,1]],[[0,1,1],[1,1,1]],[[0,0,0],[0,1,0]],[[1,0,0],[1,1,0]],
      [[0,0,1],[0,1,1]],[[1,0,1],[1,1,1]],[[0,0,0],[0,0,1]],[[1,0,0],[1,0,1]],[[0,1,0],[0,1,1]],[[1,1,0],[1,1,1]]];
    edges.forEach(([a, b]) => { const A = p(...a); const B = p(...b); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); });
    ctx.stroke();
    ctx.setLineDash([]);
    const origin = p(0, 0, 0);
    const ink = style.getPropertyValue("--ink").trim() || muted;
    ctx.font = "600 12px system-ui, sans-serif";
    [["a", [1, 0, 0]], ["b", [0, 1, 0]], ["c", [0, 0, 1]]].forEach(([name, f]) => {
      const end = p(...f);
      this.arrow(ctx, origin, end, muted, 1.6);
      const dx = end[0] - origin[0]; const dy = end[1] - origin[1]; const n = Math.hypot(dx, dy) || 1;
      ctx.fillStyle = ink;
      ctx.fillText(name, end[0] + (dx / n) * 8 - 3, end[1] + (dy / n) * 8 + 4);
    });
  }

  arrow(ctx, from, to, colour, width = 2) {
    const dx = to[0] - from[0]; const dy = to[1] - from[1];
    const length = Math.hypot(dx, dy);
    if (length < 2) return;
    const head = Math.min(9, length * 0.45);
    const ux = dx / length; const uy = dy / length;
    ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = width; ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(from[0], from[1]);
    ctx.lineTo(to[0] - ux * head * 0.6, to[1] - uy * head * 0.6);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(to[0], to[1]);
    ctx.lineTo(to[0] - ux * head - uy * head * 0.5, to[1] - uy * head + ux * head * 0.5);
    ctx.lineTo(to[0] - ux * head + uy * head * 0.5, to[1] - uy * head - ux * head * 0.5);
    ctx.closePath();
    ctx.fill();
  }

  drawVectors(ctx) {
    const s = this.settings;
    if (!s.vectors.size) return;
    const style = getComputedStyle(this.canvas);
    s.vectors.forEach((id) => {
      const spec = SM.VECTORS.find((v) => v.id === id);
      const field = this.field(id);
      if (!field) return;
      const colour = style.getPropertyValue(spec.token).trim();
      const gain = spec.unit ? 2.2 : 1;
      this.drawn.forEach((d) => {
        if (this.store.fixed[d.atom]) return;   /* every supercell image, not only the home cell */
        const i = d.atom * 3;
        const v = [field[i] * gain, field[i + 1] * gain, field[i + 2] * gain];
        if (Math.hypot(v[0], v[1], v[2]) < (spec.unit ? 0.3 : s.vectorMin)) return;
        const from = this.project(d.x, d.y, d.z, this.view);
        const to = this.project(d.x + v[0] * s.vectorScale, d.y + v[1] * s.vectorScale, d.z + v[2] * s.vectorScale, this.view);
        this.arrow(ctx, from, to, colour);
      });
    });
  }

  /* While comparing: which atoms are the selected reaction and which are ghosts. */
  drawLegend(ctx) {
    const s = this.settings;
    const others = [...s.compare].filter(([id]) => id !== this.reaction);
    if (s.focus || !others.length) return;
    const style = getComputedStyle(this.canvas);
    const r = this.store.reactions[this.reaction];
    const lines = [[style.getPropertyValue("--ink").trim(), `Solid: selected ${r.saddle.slice(4, 10)} (${this.store.label(r.reactant)} → ${this.store.label(r.product)})`]];
    others.forEach(([id, slot]) => {
      const m = this.store.reactions[SM.matchRow(this.store, this.reaction, id)];
      lines.push([style.getPropertyValue(SM.FAN_COLOURS[slot % 8]).trim(), `Ghost: ${m.saddle.slice(4, 10)} (${this.store.label(m.reactant)} → ${this.store.label(m.product)})`]);
    });
    ctx.font = "600 12px system-ui, sans-serif";
    const w = Math.max(...lines.map(([, t]) => ctx.measureText(t).width)) + 28;
    ctx.fillStyle = style.getPropertyValue("--panel").trim();
    ctx.globalAlpha = 0.86; ctx.fillRect(8, 8, w, lines.length * 18 + 8); ctx.globalAlpha = 1;
    lines.forEach(([colour, text], i) => {
      ctx.fillStyle = colour; ctx.beginPath(); ctx.arc(18, 21 + i * 18, 5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = style.getPropertyValue("--ink").trim(); ctx.fillText(text, 28, 25 + i * 18);
    });
  }

  /* Drawn atoms whose centre is not hidden behind a nearer atom, front first, with screen position and radius
     (for marks drawn on top of the scene: fixed-atom crosses, site counts). */
  visibleDrawn() {
    const view = this.view;
    const list = this.drawn.map((d) => {
      const p = this.project(d.x, d.y, d.z, view);
      return { ...d, sx: p[0], sy: p[1], depth: p[2], r: SM.geo.RADII[this.store.numbers[d.atom]] * 0.92 * this.settings.atomScale * view.scale };
    }).sort((a, b) => b.depth - a.depth);
    const cell = Math.max(8, 2 * Math.max(...list.map((d) => d.r), 1));
    const grid = new Map(); const out = [];
    const key = (x, y) => `${Math.floor(x / cell)},${Math.floor(y / cell)}`;
    for (const d of list) {
      let hidden = false;
      for (let u = -1; u <= 1 && !hidden; u += 1) for (let w = -1; w <= 1 && !hidden; w += 1) {
        const near = grid.get(`${Math.floor(d.sx / cell) + u},${Math.floor(d.sy / cell) + w}`);
        if (near) hidden = near.some((n) => Math.hypot(n.sx - d.sx, n.sy - d.sy) < n.r * 0.8);
      }
      if (!hidden) out.push(d);
      const k = key(d.sx, d.sy);
      if (!grid.has(k)) grid.set(k, []);
      grid.get(k).push(d);
    }
    return out;
  }

  /* Distance between the last two pinned atoms (and the angle for the last three), at the current path position,
     shortest periodic image, shown at the bottom like ASE. */
  drawMeasure(ctx) {
    const atoms = [...this.settings.pinned].slice(-3);
    if (atoms.length < 2) return;
    const p = this.positions; const L = this.lattice; const sym = (a) => `${SM.geo.SYMBOLS[this.store.numbers[a]]}${a}`;
    const vec = (a, b) => L.mic(p[b * 3] - p[a * 3], p[b * 3 + 1] - p[a * 3 + 1], p[b * 3 + 2] - p[a * 3 + 2]);
    const len = (v) => Math.hypot(v[0], v[1], v[2]);
    const [a, b] = atoms.slice(-2);
    let text = `${sym(a)} – ${sym(b)}: ${len(vec(a, b)).toFixed(3)} Å`;
    if (atoms.length === 3) {
      const [x, y, z] = atoms;
      const u = vec(y, x); const w = vec(y, z);
      const cos = (u[0] * w[0] + u[1] * w[1] + u[2] * w[2]) / Math.max(len(u) * len(w), 1e-9);
      text = `${sym(x)} – ${sym(y)}: ${len(u).toFixed(3)} Å · ${sym(y)} – ${sym(z)}: ${len(w).toFixed(3)} Å · angle ${sym(x)}-${sym(y)}-${sym(z)}: ${(Math.acos(Math.max(-1, Math.min(1, cos))) * 180 / Math.PI).toFixed(1)}°`;
    }
    const style = getComputedStyle(this.canvas);
    ctx.save();
    ctx.font = "600 12.5px system-ui, sans-serif";
    const w = ctx.measureText(text).width + 16;
    ctx.globalAlpha = 0.9; ctx.fillStyle = style.getPropertyValue("--panel").trim();
    ctx.fillRect(8, this.view.height - 32, w, 24); ctx.globalAlpha = 1;
    ctx.fillStyle = style.getPropertyValue("--ink").trim();
    ctx.fillText(text, 16, this.view.height - 15);
    ctx.restore();
  }

  drawPins(ctx) {
    const s = this.settings;
    if (!s.pinned.size && s.selected == null) return;
    const style = getComputedStyle(this.canvas);
    ctx.font = "600 12px system-ui, sans-serif";
    this.drawn.forEach((d) => {
      if (!d.primary || !(s.pinned.has(d.atom) || s.selected === d.atom)) return;
      const p = this.project(d.x, d.y, d.z, this.view);
      const radius = SM.geo.RADII[this.store.numbers[d.atom]] * 0.92 * this.settings.atomScale * this.view.scale;
      ctx.strokeStyle = style.getPropertyValue("--ink").trim();
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(p[0], p[1], radius + 2, 0, Math.PI * 2);
      ctx.stroke();
      if (!s.labels) return;
      const text = `${SM.geo.SYMBOLS[this.store.numbers[d.atom]]} ${d.atom}`;
      const w = ctx.measureText(text).width + 8;
      ctx.fillStyle = style.getPropertyValue("--ink").trim();
      ctx.fillRect(p[0] - w / 2, p[1] - radius - 22, w, 17);
      ctx.fillStyle = style.getPropertyValue("--panel").trim();
      ctx.textAlign = "center";
      ctx.fillText(text, p[0], p[1] - radius - 9);
      ctx.textAlign = "start";
    });
  }

  pick(clientX, clientY) {
    const box = this.canvas.getBoundingClientRect();
    const x = clientX - box.left; const y = clientY - box.top;
    let best = null;
    this.drawn.forEach((d) => {
      const p = this.project(d.x, d.y, d.z, this.view);
      const radius = SM.geo.RADII[this.store.numbers[d.atom]] * 0.92 * this.settings.atomScale * this.view.scale;
      if (Math.hypot(p[0] - x, p[1] - y) <= radius && (!best || p[2] > best.depth)) best = { atom: d.atom, depth: p[2] };
    });
    return best ? best.atom : null;
  }

  bind() {
    const c = this.canvas; const s = this.settings;
    let last = null; let moved = 0; let panning = false; let rolling = false;
    c.addEventListener("pointerdown", (e) => {
      last = [e.clientX, e.clientY]; moved = 0; panning = e.shiftKey || e.button === 2;
      rolling = e.altKey || e.ctrlKey || e.metaKey;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!last) return;
      const dx = e.clientX - last[0]; const dy = e.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      if (rolling) {
        /* Spin about the screen normal: angle swept around the canvas centre. */
        const box = c.getBoundingClientRect();
        const cx = box.left + box.width / 2; const cy = box.top + box.height / 2;
        const before = Math.atan2(last[1] - cy, last[0] - cx); const after = Math.atan2(e.clientY - cy, e.clientX - cx);
        let delta = after - before;
        if (delta > Math.PI) delta -= 2 * Math.PI; if (delta < -Math.PI) delta += 2 * Math.PI;
        s.turn(2, -delta);
      } else if (panning) { s.panX += dx; s.panY -= dy; } else {
        /* Trackball in the current screen frame (as in ASE): right drag turns about screen vertical, down about horizontal. */
        if (dx) s.turn(1, dx * 0.008);
        if (dy) s.turn(0, dy * 0.008);
      }
      last = [e.clientX, e.clientY];
      s.redraw();
    });
    c.addEventListener("pointerup", (e) => {
      /* End the drag first, so an error in the click handler can never leave the view stuck rotating. */
      const click = last && moved < 5;
      last = null;
      if (c.hasPointerCapture && c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
      if (click && this.options.onPick) this.options.onPick(this.pick(e.clientX, e.clientY));
    });
    c.addEventListener("pointercancel", () => { last = null; });
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      s.zoom = Math.max(0.3, Math.min(8, s.zoom * Math.exp(-e.deltaY * 0.0012)));
      s.redraw();
    }, { passive: false });
    c.addEventListener("keydown", (e) => {
      if (e.key === "q" || e.key === "e") { s.turn(2, e.key === "q" ? 0.1 : -0.1); s.redraw(); return; }
      const turn = { a: [1, -0.1], d: [1, 0.1], w: [0, -0.1], s: [0, 0.1] }[e.key];
      if (!turn) return;
      s.turn(turn[0], turn[1]);
      s.redraw();
    });
  }
};
