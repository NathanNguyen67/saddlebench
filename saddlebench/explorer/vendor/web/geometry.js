/* Elements, periodic geometry and camera maths. No DOM access. */
window.SM = window.SM || {};

SM.geo = (() => {
  const SYMBOLS = ("X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba "
    + "La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi").split(" ");
  const RADII = [0.7, 0.31, 0.28, 1.28, 0.96, 0.84, 0.76, 0.71, 0.66, 0.57, 0.58, 1.66, 1.41, 1.21, 1.11, 1.07, 1.05, 1.02, 1.06, 2.03, 1.76,
    1.7, 1.6, 1.53, 1.39, 1.39, 1.32, 1.26, 1.24, 1.32, 1.22, 1.22, 1.2, 1.19, 1.2, 1.2, 1.16, 2.2, 1.95, 1.9, 1.75, 1.64, 1.54, 1.47, 1.46,
    1.42, 1.39, 1.45, 1.44, 1.42, 1.39, 1.39, 1.38, 1.39, 1.4, 2.44, 2.15, 2.07, 2.04, 2.03, 2.01, 1.99, 1.98, 1.98, 1.96, 1.94, 1.92, 1.92,
    1.89, 1.9, 1.87, 1.87, 1.75, 1.7, 1.62, 1.51, 1.44, 1.41, 1.36, 1.36, 1.32, 1.45, 1.46, 1.48];
  const COLORS = ("808080 f2f2f2 d9ffff cc80ff c2ff00 ffb5b5 5a6068 3050f8 ff2f20 90e050 b3e3f5 ab5cf2 8aff00 b4bcc4 c9a97a ff8000 e8c830 1ff01f 80d1e3 8f40d4 3dff00 "
    + "e6e6e6 9aa2ab a6a6ab 8a99c7 9c7ac7 e06633 f090a0 50d050 c88033 7d80b0 c28f8f 668f8f bd80e3 ffa100 a62929 5cb8d1 702eb0 00ff00 94ffff 94e0e0 73c2c9 54b5b5 3b9e9e "
    + "248f8f 0a7d8c 006985 c0c0c0 ffd98f a67573 668080 9e63b5 d47a00 940094 429eb0 57178f 00c900 70d4ff ffffc7 d9ffc7 c7ffc7 a3ffc7 8fffc7 61ffc7 45ffc7 30ffc7 1fffc7 "
    + "00ff9c 00e675 00d452 00bf38 00ab24 4dc2ff 4da6ff 2194d6 267dab 266696 175487 d0d0e0 ffd123 b8b8d0 a6544d 575961 9e4fb5").split(" ");

  const rgb = (hex) => [parseInt(hex.slice(0, 2), 16) / 255, parseInt(hex.slice(2, 4), 16) / 255, parseInt(hex.slice(4, 6), 16) / 255];
  const ELEMENT_RGB = COLORS.map(rgb);

  function invert(m) {
    const [a, b, c] = m[0]; const [d, e, f] = m[1]; const [g, h, i] = m[2];
    const A = e * i - f * h; const B = c * h - b * i; const C = b * f - c * e;
    const det = a * A + d * B + g * C;
    return [[A / det, B / det, C / det], [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
      [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]];
  }

  /* Periodic cell helper. Rows of `cell` are lattice vectors. */
  class Lattice {
    constructor(cell, pbc) {
      this.cell = cell;
      this.pbc = pbc;
      this.inv = invert(cell);
      const ranges = pbc.map((p) => (p ? [-1, 0, 1] : [0]));
      this.neighbours = [];
      ranges[0].forEach((i) => ranges[1].forEach((j) => ranges[2].forEach((k) => this.neighbours.push([i, j, k]))));
    }

    fractional(x, y, z) {
      const m = this.inv;
      return [x * m[0][0] + y * m[1][0] + z * m[2][0], x * m[0][1] + y * m[1][1] + z * m[2][1], x * m[0][2] + y * m[1][2] + z * m[2][2]];
    }

    cartesian(u, v, w) {
      const c = this.cell;
      return [u * c[0][0] + v * c[1][0] + w * c[2][0], u * c[0][1] + v * c[1][1] + w * c[2][1], u * c[0][2] + v * c[1][2] + w * c[2][2]];
    }

    /* Shortest periodic image of a vector, including skewed cells. */
    mic(x, y, z) {
      const f = this.fractional(x, y, z).map((value, k) => (this.pbc[k] ? value - Math.round(value) : value));
      let best = null; let bestSize = Infinity;
      for (const n of this.neighbours) {
        const v = this.cartesian(f[0] + n[0], f[1] + n[1], f[2] + n[2]);
        const size = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
        if (size < bestSize) { bestSize = size; best = v; }
      }
      return best;
    }

    /* Every periodic image of (x,y,z) within radius of centre: calls visit(px,py,pz,primary). */
    imagesWithin(x, y, z, centre, radius, visit) {
      const dx = x - centre[0]; const dy = y - centre[1]; const dz = z - centre[2];
      const f = this.fractional(dx, dy, dz);
      const reach = this.pbc.map((p, k) => (p ? Math.ceil(radius * Math.hypot(...this.inv.map((row) => row[k]))) + 1 : 0));
      const base = f.map((value, k) => (this.pbc[k] ? Math.round(value) : 0));
      let nearest = Infinity;
      const found = [];
      for (let i = -reach[0]; i <= reach[0]; i += 1) {
        for (let j = -reach[1]; j <= reach[1]; j += 1) {
          for (let k = -reach[2]; k <= reach[2]; k += 1) {
            const v = this.cartesian(f[0] - base[0] + i, f[1] - base[1] + j, f[2] - base[2] + k);
            const size = Math.hypot(v[0], v[1], v[2]);
            if (size <= radius) found.push([v[0] + centre[0], v[1] + centre[1], v[2] + centre[2], size]);
            if (size < nearest) nearest = size;
          }
        }
      }
      found.forEach((p) => visit(p[0], p[1], p[2], p[3] === nearest));
    }
  }

  /* Centre of a set of atoms that may straddle a periodic boundary. */
  function periodicCentre(lattice, positions, atoms) {
    if (!atoms.length) return null;
    const a = atoms[0] * 3;
    const ref = [positions[a], positions[a + 1], positions[a + 2]];
    const sum = [0, 0, 0];
    atoms.forEach((atom) => {
      const i = atom * 3;
      const d = lattice.mic(positions[i] - ref[0], positions[i + 1] - ref[1], positions[i + 2] - ref[2]);
      sum[0] += d[0]; sum[1] += d[1]; sum[2] += d[2];
    });
    return [ref[0] + sum[0] / atoms.length, ref[1] + sum[1] / atoms.length, ref[2] + sum[2] / atoms.length];
  }

  /* Rows of the view rotation: yaw about z, then pitch about the screen x axis. */
  function rotation(yaw, pitch) {
    const cy = Math.cos(yaw); const sy = Math.sin(yaw); const cp = Math.cos(pitch); const sp = Math.sin(pitch);
    return [[cy, -sy, 0], [cp * sy, cp * cy, -sp], [sp * sy, sp * cy, cp]];
  }

  /* Perceptual blue-to-yellow ramp for displacement colouring. */
  function ramp(t) {
    const stops = [[0.27, 0.32, 0.55], [0.13, 0.57, 0.55], [0.37, 0.79, 0.38], [0.99, 0.87, 0.15]];
    const x = Math.max(0, Math.min(0.9999, t)) * (stops.length - 1);
    const i = Math.floor(x); const u = x - i;
    return stops[i].map((value, k) => value + (stops[i + 1][k] - value) * u);
  }

  return { SYMBOLS, RADII, ELEMENT_RGB, Lattice, periodicCentre, rotation, ramp };
})();
