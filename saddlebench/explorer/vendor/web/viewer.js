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
      /* focus clips to a sphere around the movers (gallery cards); the main view draws whole cells instead. */
      focus: true, radius: 12, focusAtom: null, supercell: 1, fade: 0,
      threshold: 0.2, colour: "highlight", vectors: new Set(), vectorScale: 1, vectorMin: 0.2,
      pinned: new Set(), labels: true, selected: null,
      /* atomScale multiplies drawn radii; keepView keeps the camera pivot and image wrapping when the reaction
         changes; compare maps reaction id -> colour slot, and those reactions' movers are drawn in that colour. */
      atomScale: 1, keepView: false, compare: new Map(),
    }, overrides);
    this.viewers = new Set();
  }

  redraw() { this.viewers.forEach((viewer) => viewer.draw()); }

  resetCamera() {
    Object.assign(this, { yaw: 0.5, pitch: -1.0, zoom: 1, panX: 0, panY: 0 });
    this.viewers.forEach((viewer) => { viewer.centre = null; });
    this.redraw();
  }

  camera(name) {
    const presets = { top: [0, 0], side: [0, -Math.PI / 2], bottom: [0, Math.PI], iso: [0.5, -1.0] };
    [this.yaw, this.pitch] = presets[name];
    this.redraw();
  }
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
    if (!this.centre) {
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
    if (s.colour === "diff") {
      const other = [...s.compare.keys()].find((id) => id !== this.reaction);
      if (other === undefined) return element;
      const diff = SM.frameDiff(this.store, this.reaction, 1, other, 1);
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
      this.buffer.set([x, y, z, SM.geo.RADII[store.numbers[atom]] * 0.92 * s.atomScale, colour[0], colour[1], colour[2], this.isMover(atom, sizes, primary) ? 1 : 0, alpha], cursor * SM.STRIDE);
      drawn.push({ atom, x, y, z, primary });
      cursor += 1;
    };
    if (s.focus) {
      for (let atom = 0; atom < store.n; atom += 1) {
        const i = atom * 3;
        lattice.imagesWithin(positions[i], positions[i + 1], positions[i + 2], centre, s.radius, (x, y, z, primary) => push(atom, x, y, z, primary));
      }
    } else {
      /* Whole cells: every atom in its image nearest the movers (so the reaction is never split by a cell edge),
         plus supercell images around that. */
      const wrap = this.wrapShifts(centre);
      const span = this.repeatAxes().map((p) => (p ? s.supercell : 1));
      const half = span.map((n) => Math.floor((n - 1) / 2));
      for (let u = -half[0]; u < span[0] - half[0]; u += 1) {
        for (let v = -half[1]; v < span[1] - half[1]; v += 1) {
          for (let w = -half[2]; w < span[2] - half[2]; w += 1) {
            const shift = lattice.cartesian(u, v, w);
            const home = u === 0 && v === 0 && w === 0;
            for (let atom = 0; atom < store.n; atom += 1) {
              const i = atom * 3;
              push(atom, positions[i] + wrap[i] + shift[0], positions[i + 1] + wrap[i + 1] + shift[1], positions[i + 2] + wrap[i + 2] + shift[2], home);
            }
          }
        }
      }
    }
    /* Ghosts: each compared reaction's movers at the same path position, translucent in its colour, so two
       reactions moving the same atoms (e.g. mirror-image saddles) show both paths. */
    if (!s.focus && s.compare.size) {
      const wrap = this.wrapShifts(centre);
      const span = this.repeatAxes().map((p) => (p ? s.supercell : 1));
      const half = span.map((n) => Math.floor((n - 1) / 2));
      const images = [];
      for (let u = -half[0]; u < span[0] - half[0]; u += 1)
        for (let v = -half[1]; v < span[1] - half[1]; v += 1)
          for (let w = -half[2]; w < span[2] - half[2]; w += 1) images.push(lattice.cartesian(u, v, w));
      this.ghostBuffer = this.ghostBuffer || new Float32Array(store.n * 3);
      for (const [reaction, slot] of s.compare) {
        if (reaction === this.reaction) continue;
        const ghost = store.path(reaction, s.t, this.ghostBuffer);
        const colour = palette.compare[slot % palette.compare.length];
        for (const atom of store.movers(reaction, s.threshold)) {
          const i = atom * 3;
          for (const shift of images) {
            if ((cursor + 1) * SM.STRIDE > this.buffer.length) {
              const bigger = new Float32Array(this.buffer.length * 2); bigger.set(this.buffer); this.buffer = bigger;
            }
            this.buffer.set([ghost[i] + wrap[i] + shift[0], ghost[i + 1] + wrap[i + 1] + shift[1], ghost[i + 2] + wrap[i + 2] + shift[2],
              SM.geo.RADII[store.numbers[atom]] * 0.92 * s.atomScale * 0.98, colour[0], colour[1], colour[2], 0, 0.55], cursor * SM.STRIDE);
            cursor += 1;
          }
        }
      }
    }
    this.drawn = drawn;
    this.lastCentre = centre;
    return { count: cursor, centre };
  }

  /* The camera frames a sphere of this radius around the pivot (movers); zoom out to see whole cells. */
  viewSpan() { return this.settings.radius * 1.02; }

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
    const R = SM.geo.rotation(s.yaw, s.pitch);
    const scale = (Math.min(width, height) / (2 * this.viewSpan())) * s.zoom;
    const pivot = centre;
    this.renderer.draw(ctx, width, height, ratio, { data: this.buffer, count, rotation: R, pivot, scale, panX: s.panX, panY: s.panY, fog: palette.fog, ring: palette.accent });
    this.view = { R, pivot, scale, width, height };
    if (!s.focus) this.drawCell(ctx);
    if (this.options.overlays !== false) {
      this.drawVectors(ctx);
      if (this.options.overlay) this.options.overlay(ctx, this);
      this.drawPins(ctx);
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
        if (!d.primary || this.store.fixed[d.atom]) return;
        const i = d.atom * 3;
        const v = [field[i] * gain, field[i + 1] * gain, field[i + 2] * gain];
        if (Math.hypot(v[0], v[1], v[2]) < (spec.unit ? 0.3 : s.vectorMin)) return;
        const from = this.project(d.x, d.y, d.z, this.view);
        const to = this.project(d.x + v[0] * s.vectorScale, d.y + v[1] * s.vectorScale, d.z + v[2] * s.vectorScale, this.view);
        this.arrow(ctx, from, to, colour);
      });
    });
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
    let last = null; let moved = 0; let panning = false;
    c.addEventListener("pointerdown", (e) => {
      last = [e.clientX, e.clientY]; moved = 0; panning = e.shiftKey || e.button === 2;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!last) return;
      const dx = e.clientX - last[0]; const dy = e.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      if (panning) { s.panX += dx; s.panY -= dy; } else {
        s.yaw += dx * 0.008;
        s.pitch = SM.wrapAngle(s.pitch + dy * 0.008);
      }
      last = [e.clientX, e.clientY];
      s.redraw();
    });
    c.addEventListener("pointerup", (e) => {
      if (last && moved < 5 && this.options.onPick) this.options.onPick(this.pick(e.clientX, e.clientY));
      last = null;
    });
    c.addEventListener("pointercancel", () => { last = null; });
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      s.zoom = Math.max(0.3, Math.min(8, s.zoom * Math.exp(-e.deltaY * 0.0012)));
      s.redraw();
    }, { passive: false });
    c.addEventListener("keydown", (e) => {
      const turn = { a: [-0.1, 0], d: [0.1, 0], w: [0, -0.1], s: [0, 0.1] }[e.key];
      if (!turn) return;
      s.yaw += turn[0];
      s.pitch = SM.wrapAngle(s.pitch + turn[1]);
      s.redraw();
    });
  }
};
