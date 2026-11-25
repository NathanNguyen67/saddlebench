/* One shared sphere renderer. Each viewer draws into it, then copies the image to its own canvas. */
window.SM = window.SM || {};
SM.STRIDE = 9; /* x, y, z, radius, r, g, b, rim, alpha */

SM.Renderer = class Renderer {
  constructor() {
    this.canvas = document.createElement("canvas");
    const gl = this.canvas.getContext("webgl2", { alpha: true, antialias: true, premultipliedAlpha: true, preserveDrawingBuffer: true });
    this.gl = gl;
    if (!gl) return;
    const vs = `#version 300 es
      in vec2 corner; in vec3 centre; in float radius; in vec3 colour; in float rim; in float alpha;
      uniform mat3 rot; uniform vec3 pivot; uniform float scale; uniform vec2 view; uniform vec2 pan; uniform float depth;
      out vec2 uv; out vec3 tint; out float z; out float r; out float ring; out float a;
      void main() {
        vec3 p = rot * (centre - pivot);
        uv = corner; tint = colour; z = p.z; r = radius; ring = rim; a = alpha;
        vec2 s = (p.xy * scale + pan + corner * radius * scale) / (view * 0.5);
        gl_Position = vec4(s, -p.z / depth, 1.0);
      }`;
    const fs = `#version 300 es
      precision highp float;
      in vec2 uv; in vec3 tint; in float z; in float r; in float ring; in float a;
      uniform float depth; uniform vec3 fog; uniform vec2 fogRange; uniform vec3 ringColour;
      out vec4 pixel;
      void main() {
        float d = dot(uv, uv);
        if (d > 1.0) discard;
        float nz = sqrt(1.0 - d);
        vec3 n = vec3(uv, nz);
        float diffuse = max(dot(n, normalize(vec3(-0.4, 0.6, 0.72))), 0.0);
        float shine = pow(max(dot(n, normalize(vec3(-0.18, 0.3, 1.0))), 0.0), 42.0);
        vec3 c = tint * (0.46 + 0.6 * diffuse) + vec3(0.2) * shine;
        c *= 1.0 - 0.32 * smoothstep(0.8, 1.0, sqrt(d));
        /* rim: +1 mover ring, +2 fixed atom (an ASE-style cross drawn on the sphere, so it is hidden exactly as much
           as the atom is). */
        if (mod(ring, 2.0) > 0.5) c = mix(c, ringColour, smoothstep(0.66, 0.74, sqrt(d)));
        if (ring > 1.5 && d < 0.55 && min(abs(uv.x - uv.y), abs(uv.x + uv.y)) < 0.13) c = mix(c, vec3(0.1), 0.7);
        /* No depth fog: atoms keep their true colour at every depth (crisp, Nathan 2026-11-25). */
        pixel = vec4(c * a, a);
        gl_FragDepth = 0.5 - (z + nz * r) / depth * 0.5;
      }`;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    this.u = {};
    ["rot", "pivot", "scale", "view", "pan", "depth", "fog", "fogRange", "ringColour"].forEach((n) => { this.u[n] = gl.getUniformLocation(program, n); });
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, "corner");
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);
    this.instances = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instances);
    [["centre", 3, 0], ["radius", 1, 12], ["colour", 3, 16], ["rim", 1, 28], ["alpha", 1, 32]].forEach(([name, size, offset]) => {
      const location = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, SM.STRIDE * 4, offset);
      gl.vertexAttribDivisor(location, 1);
    });
    gl.enable(gl.DEPTH_TEST);
  }

  /* scene: { data: Float32Array [x,y,z,r,cr,cg,cb,rim]*, count, rotation, pivot, scale, panX, panY, fog: [r,g,b], ring: [r,g,b] } */
  draw(ctx, width, height, ratio, scene) {
    ctx.clearRect(0, 0, width, height);
    if (!scene.count) return;
    if (!this.gl) { if (scene.bonds && scene.bonds.length) SM.drawBonds(ctx, width, height, scene); this.drawFlat(ctx, width, height, scene); return; }
    const gl = this.gl;
    const w = Math.max(1, Math.round(width * ratio));
    const h = Math.max(1, Math.round(height * ratio));
    if (this.canvas.width < w || this.canvas.height < h) {
      this.canvas.width = Math.max(this.canvas.width, w);
      this.canvas.height = Math.max(this.canvas.height, h);
    }
    gl.viewport(0, this.canvas.height - h, w, h);
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(0, this.canvas.height - h, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const R = scene.rotation;
    let near = -Infinity; let far = Infinity;
    for (let i = 0; i < scene.count; i += 1) {
      const x = scene.data[i * SM.STRIDE] - scene.pivot[0]; const y = scene.data[i * SM.STRIDE + 1] - scene.pivot[1]; const z = scene.data[i * SM.STRIDE + 2] - scene.pivot[2];
      const depth = R[2][0] * x + R[2][1] * y + R[2][2] * z;
      if (depth > near) near = depth;
      if (depth < far) far = depth;
    }
    gl.uniformMatrix3fv(this.u.rot, false, new Float32Array([R[0][0], R[1][0], R[2][0], R[0][1], R[1][1], R[2][1], R[0][2], R[1][2], R[2][2]]));
    gl.uniform3fv(this.u.pivot, scene.pivot);
    gl.uniform1f(this.u.scale, scene.scale);
    gl.uniform2f(this.u.view, width, height);
    gl.uniform2f(this.u.pan, scene.panX, scene.panY);
    gl.uniform1f(this.u.depth, Math.max(Math.abs(near), Math.abs(far)) * 2 + 12);
    gl.uniform3fv(this.u.fog, scene.fog);
    gl.uniform3fv(this.u.ringColour, scene.ring || [0.9, 0.3, 0.1]);
    gl.uniform2f(this.u.fogRange, far, near);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instances);
    /* Opaque atoms first with depth writes, then faded atoms back to front with blending and no depth writes. */
    const split = SM.splitByAlpha(scene.data, scene.count, R, scene.pivot);
    gl.disable(gl.BLEND);
    gl.depthMask(true);
    if (split.opaqueCount) {
      gl.bufferData(gl.ARRAY_BUFFER, split.opaque, gl.DYNAMIC_DRAW);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, split.opaqueCount);
    }
    if (split.fadedCount) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      gl.bufferData(gl.ARRAY_BUFFER, split.faded, gl.DYNAMIC_DRAW);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, split.fadedCount);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
    }
    gl.finish();
    if (scene.bonds && scene.bonds.length) SM.drawBonds(ctx, width, height, scene);
    ctx.drawImage(this.canvas, 0, 0, w, h, 0, 0, width, height);
  }

  /* Fallback when WebGL2 is unavailable. */
  drawFlat(ctx, width, height, scene) {
    const R = scene.rotation;
    const order = [];
    for (let i = 0; i < scene.count; i += 1) {
      const x = scene.data[i * SM.STRIDE] - scene.pivot[0]; const y = scene.data[i * SM.STRIDE + 1] - scene.pivot[1]; const z = scene.data[i * SM.STRIDE + 2] - scene.pivot[2];
      order.push([R[2][0] * x + R[2][1] * y + R[2][2] * z, R[0][0] * x + R[0][1] * y, R[1][0] * x + R[1][1] * y + R[1][2] * z, i]);
    }
    order.sort((a, b) => a[0] - b[0]);
    ctx.strokeStyle = "rgba(0,0,0,.25)";
    order.forEach(([, x, y, i]) => {
      const c = [scene.data[i * SM.STRIDE + 4], scene.data[i * SM.STRIDE + 5], scene.data[i * SM.STRIDE + 6]].map((v) => Math.round(v * 255));
      ctx.globalAlpha = scene.data[i * SM.STRIDE + 8];
      ctx.fillStyle = `rgb(${c.join(",")})`;
      ctx.beginPath();
      ctx.arc(width / 2 + x * scene.scale + scene.panX, height / 2 - y * scene.scale - scene.panY, scene.data[i * SM.STRIDE + 3] * scene.scale, 0, Math.PI * 2);
      ctx.fill();
      if (scene.data[i * SM.STRIDE + 7] > 0.5 && scene.ring) { ctx.save(); ctx.lineWidth = 3; ctx.strokeStyle = `rgb(${scene.ring.map((v) => Math.round(v * 255)).join(",")})`; ctx.stroke(); ctx.restore(); } else ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }
};

/* Split instances into opaque (alpha 1) and faded ones; faded ones are sorted far to near for blending. */
SM.splitByAlpha = function splitByAlpha(data, count, R, pivot) {
  const W = SM.STRIDE;
  let faded = 0;
  for (let i = 0; i < count; i += 1) if (data[i * W + 8] < 0.999) faded += 1;
  if (!faded) return { opaque: data.subarray(0, count * W), opaqueCount: count, faded: null, fadedCount: 0 };
  const opaque = new Float32Array((count - faded) * W);
  const order = [];
  let o = 0;
  for (let i = 0; i < count; i += 1) {
    if (data[i * W + 8] < 0.999) {
      const x = data[i * W] - pivot[0]; const y = data[i * W + 1] - pivot[1]; const z = data[i * W + 2] - pivot[2];
      order.push([R[2][0] * x + R[2][1] * y + R[2][2] * z, i]);
    } else { opaque.set(data.subarray(i * W, i * W + W), o * W); o += 1; }
  }
  order.sort((a, b) => a[0] - b[0]);
  const out = new Float32Array(faded * W);
  order.forEach(([, i], k) => out.set(data.subarray(i * W, i * W + W), k * W));
  return { opaque, opaqueCount: o, faded: out, fadedCount: faded };
};

/* Bonds as lines under the atoms (drawn before the atom image, so atoms cover their ends). */
SM.drawBonds = (ctx, width, height, scene) => {
  const R = scene.rotation; const p = scene.pivot; const b = scene.bonds;
  const px = (i) => {
    const dx = b[i] - p[0]; const dy = b[i + 1] - p[1]; const dz = b[i + 2] - p[2];
    return [width / 2 + (R[0][0] * dx + R[0][1] * dy + R[0][2] * dz) * scene.scale + scene.panX,
      height / 2 - (R[1][0] * dx + R[1][1] * dy + R[1][2] * dz) * scene.scale - scene.panY];
  };
  ctx.save();
  ctx.strokeStyle = scene.bondColour || "#888";
  ctx.lineWidth = Math.max(1.2, Math.min(7, 0.16 * scene.scale));
  ctx.lineCap = "round";
  ctx.beginPath();
  for (let i = 0; i < b.length; i += 6) { const a = px(i); const c = px(i + 3); ctx.moveTo(a[0], a[1]); ctx.lineTo(c[0], c[1]); }
  ctx.stroke();
  ctx.restore();
};
