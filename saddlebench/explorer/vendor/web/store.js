/* Data access. Views read structures, reactions and frames only through this module. */
window.SM = window.SM || {};

SM.Store = class Store {
  constructor(payload) {
    this.payload = payload;
    const s = payload.structure;
    this.structure = s;
    this.provenance = payload.provenance;
    this.thresholds = payload.thresholds;
    this.classes = payload.classes;
    this.reactions = payload.reactions;
    this.n = s.numbers.length;
    this.numbers = Uint8Array.from(s.numbers);
    this.cell = s.cell;
    this.pbc = s.pbc;
    this.fixed = new Uint8Array(this.n);
    s.fixed.forEach((i) => { this.fixed[i] = 1; });
    this.nFree = this.n - s.fixed.length;
    this.base = new Float32Array(this.n * 3);
    s.positions.forEach((p, i) => this.base.set(p, i * 3));
    this.scale = payload.encoding.displacementA;
    this.modeUnit = payload.encoding.modeUnit;
    const b = payload.buffers;
    this.frameIndex = new Uint16Array(Store.bytes(b.frameIndex));
    this.frameValues = new Int16Array(Store.bytes(b.frameValues));
    this.modeIndex = new Uint16Array(Store.bytes(b.modeIndex));
    this.modeValues = new Int16Array(Store.bytes(b.modeValues));
    payload.buffers = null;
    this.classById = new Map(this.classes.map((c) => [c.id, c]));
    this.frames = new Map();
    this.sizes = new Map();
    this.seeds = new Set(s.seeds);
  }

  static bytes(text) {
    const raw = atob(text);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
    return out.buffer;
  }

  /* which: 0 reactant, 1 saddle, 2 product */
  frame(reaction, which) {
    const key = reaction * 3 + which;
    const hit = this.frames.get(key);
    if (hit) {
      this.frames.delete(key);
      this.frames.set(key, hit);
      return hit;
    }
    const out = this.base.slice();
    const [start, count] = this.reactions[reaction].frames[which];
    for (let k = 0; k < count; k += 1) {
      const atom = this.frameIndex[start + k] * 3;
      const v = (start + k) * 3;
      out[atom] += this.frameValues[v] * this.scale;
      out[atom + 1] += this.frameValues[v + 1] * this.scale;
      out[atom + 2] += this.frameValues[v + 2] * this.scale;
    }
    this.frames.set(key, out);
    if (this.frames.size > 240) this.frames.delete(this.frames.keys().next().value);
    return out;
  }

  /* t: -1 reactant, 0 saddle, +1 product (straight-line display blend) */
  path(reaction, t, out) {
    const saddle = this.frame(reaction, 1);
    const end = this.frame(reaction, t < 0 ? 0 : 2);
    const u = Math.abs(t);
    const target = out || new Float32Array(this.n * 3);
    for (let i = 0; i < target.length; i += 1) target[i] = saddle[i] + (end[i] - saddle[i]) * u;
    return target;
  }

  mode(reaction) {
    const slot = this.reactions[reaction].mode;
    if (!slot) return null;
    const out = new Float32Array(this.n * 3);
    for (let k = 0; k < slot[1]; k += 1) {
      const atom = this.modeIndex[slot[0] + k] * 3;
      const v = (slot[0] + k) * 3;
      out[atom] = this.modeValues[v] * this.modeUnit;
      out[atom + 1] = this.modeValues[v + 1] * this.modeUnit;
      out[atom + 2] = this.modeValues[v + 2] * this.modeUnit;
    }
    return out;
  }

  /* to - from for two stored frames, as a flat array */
  difference(reaction, to, from) {
    const a = this.frame(reaction, to);
    const b = this.frame(reaction, from);
    const out = new Float32Array(a.length);
    for (let i = 0; i < a.length; i += 1) out[i] = a[i] - b[i];
    return out;
  }

  /* per-atom reactant -> product distance */
  moveSizes(reaction) {
    let sizes = this.sizes.get(reaction);
    if (sizes) return sizes;
    const d = this.difference(reaction, 2, 0);
    sizes = new Float32Array(this.n);
    for (let i = 0; i < this.n; i += 1) sizes[i] = Math.hypot(d[i * 3], d[i * 3 + 1], d[i * 3 + 2]);
    if (this.sizes.size > 400) this.sizes.delete(this.sizes.keys().next().value);
    this.sizes.set(reaction, sizes);
    return sizes;
  }

  /* Primary atom: the free atom moving most from reactant to saddle (cached). */
  primaryAtom(reaction) {
    if (!this.primaries) this.primaries = new Map();
    if (this.primaries.has(reaction)) return this.primaries.get(reaction);
    const a = this.frame(reaction, 1); const b = this.frame(reaction, 0);
    let best = 0; let size = -1;
    for (let i = 0; i < this.n; i += 1) {
      if (this.fixed[i]) continue;
      const m = Math.hypot(a[i * 3] - b[i * 3], a[i * 3 + 1] - b[i * 3 + 1], a[i * 3 + 2] - b[i * 3 + 2]);
      if (m > size) { size = m; best = i; }
    }
    this.primaries.set(reaction, best);
    return best;
  }

  movers(reaction, threshold) {
    const sizes = this.moveSizes(reaction);
    const out = [];
    for (let i = 0; i < this.n; i += 1) if (sizes[i] > threshold && !this.fixed[i]) out.push(i);
    return out;
  }

  /* cosine over free atoms; null when either field is empty */
  cosine(a, b) {
    if (!a || !b) return null;
    let ab = 0; let aa = 0; let bb = 0;
    for (let i = 0; i < this.n; i += 1) {
      if (this.fixed[i]) continue;
      for (let k = 0; k < 3; k += 1) {
        const x = a[i * 3 + k]; const y = b[i * 3 + k];
        ab += x * y; aa += x * x; bb += y * y;
      }
    }
    return aa > 0 && bb > 0 ? ab / Math.sqrt(aa * bb) : null;
  }

  label(minimum) {
    if (!minimum) return "–";
    return this.seeds.has(minimum) ? "seed" : minimum.slice(4, 10);
  }
};
