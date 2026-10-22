/**
 * The field behind every page: a code graph in 3D, drawn with raw WebGL2.
 *
 * Nodes are symbols, grouped into modules; short edges are calls inside a
 * module, the longer ones imports between modules. Light travels along a share
 * of the edges, the way candidates race through a run, and every few seconds a
 * blast radius opens from one symbol and passes over everything it reaches.
 * The page sets the tone (the console sets it from the live job), the scroll
 * position moves the camera down through the graph, and the pointer leans it.
 *
 * Budget, the same one the hero's replay keeps:
 *   - static geometry, uploaded once; all motion is uniforms and the shaders;
 *   - one program, two draw calls (lines, then points);
 *   - `low-power` GPU preference, device pixel ratio capped at 1.25;
 *   - 30 fps and paused in a hidden tab (Field.jsx);
 *   - a single still frame under prefers-reduced-motion;
 *   - silent failure without WebGL2: the page's CSS background shows instead.
 */

import { lookAt, mul4, perspective, rng } from "./gl";

const VERT = `#version 300 es
layout(location = 0) in vec3 aPos;
// x: position along the edge, 0..1, or -1 for a node; y: seed; z: 1 on an import edge
layout(location = 1) in vec3 aMeta;
uniform mat4 uMVP;
uniform float uDpr;
uniform float uTime;
uniform float uActivity;
uniform vec3 uFocus;
uniform float uRing;
uniform float uRingFade;
uniform float uGain;
uniform vec3 uBone;
uniform vec3 uRingCol;
out vec3 vCol;
out float vA;
out float vS;
flat out float vSeed;
flat out float vOn;
out float vFog;
void main() {
  vec4 p = uMVP * vec4(aPos, 1.0);
  gl_Position = p;
  // Depth fog: near structure is bright, far structure recedes but stays.
  float fog = clamp(1.0 - (p.w - 8.0) / 64.0, 0.12, 1.0);
  float d = distance(aPos, uFocus);
  float shell = exp(-pow((d - uRing) / 1.8, 2.0)) * uRingFade;
  float inside = smoothstep(uRing + 1.0, uRing - 3.0, d) * uRingFade;
  float lit = clamp(shell + inside * 0.25, 0.0, 1.0);
  vCol = mix(uBone, uRingCol, lit);
  vS = aMeta.x;
  vSeed = aMeta.y;
  vFog = fog * uGain;
  if (aMeta.x < 0.0) {
    vOn = 0.0;
    vA = (0.62 + 1.2 * shell) * vFog;
    gl_PointSize = (3.2 + 4.0 * shell) * uDpr * (22.0 / max(p.w, 1.0));
  } else {
    vOn = step(fract(aMeta.y * 13.7 + uTime * 0.035), uActivity);
    vA = (0.16 + 0.08 * aMeta.z + 0.6 * shell + 0.1 * inside) * vFog;
    gl_PointSize = 1.0;
  }
}`;

const FRAG = `#version 300 es
precision mediump float;
in vec3 vCol;
in float vA;
in float vS;
flat in float vSeed;
flat in float vOn;
in float vFog;
uniform float uPoints;
uniform float uPhase;
uniform vec3 uAccent;
out vec4 outColor;
void main() {
  vec3 col = vCol;
  float a = vA;
  if (uPoints > 0.5) {
    float r = length(gl_PointCoord - 0.5);
    float disc = smoothstep(0.5, 0.0, r);
    a *= disc * disc;
  } else if (vOn > 0.5) {
    // A head moving along the edge with a short tail behind it.
    float head = fract(uPhase * (0.35 + vSeed * 0.5) + vSeed * 7.0);
    float k = head - vS;
    float pulse = step(0.0, k) * smoothstep(0.2, 0.0, k);
    col = mix(col, uAccent, clamp(pulse * 1.6, 0.0, 1.0));
    a += 1.1 * pulse * vFog;
  }
  outColor = vec4(col * a, a);
}`;

const C = {
  bone: [0.93, 0.91, 0.86],
  green: [0.36, 0.88, 0.56],
  blue: [0.49, 0.72, 1.0],
  amber: [0.94, 0.71, 0.3],
  red: [1.0, 0.42, 0.37],
  violet: [0.72, 0.64, 1.0],
};

// What each tone does to the field: the colour of the travelling light, how
// fast it travels, and what share of the edges carry it. `run` is the busiest:
// candidates are racing.
export const TONES = {
  idle: { accent: C.green, flow: 0.2, activity: 0.24 },
  run: { accent: C.blue, flow: 0.42, activity: 0.5 },
  verified: { accent: C.green, flow: 0.2, activity: 0.32 },
  unverified: { accent: C.amber, flow: 0.18, activity: 0.26 },
  failed: { accent: C.red, flow: 0.14, activity: 0.2 },
  error: { accent: C.violet, flow: 0.14, activity: 0.18 },
};

const FOVY = 0.9;
const SPAN_Y = 34; // the camera travels from +SPAN_Y to -SPAN_Y as the page scrolls
const RING_EVERY = 6.5; // seconds between blast radii
const RING_GROW = 4.2; // seconds a ring takes to open and fade

function gaussian(r) {
  const u = Math.max(1e-6, r());
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * r());
}

// Modules scattered through a tall slab, symbols clustered in each, calls
// between near neighbours in a module and a few imports between modules.
function buildGraph() {
  const r = rng(20261022);
  const modules = Array.from({ length: 46 }, () => ({
    c: [(r() - 0.5) * 56, (r() - 0.5) * (SPAN_Y * 2 + 30), -6 - r() * 34],
    spread: 2.2 + r() * 1.8,
    nodes: [],
  }));
  const nodes = [];
  for (const m of modules) {
    const n = 18 + Math.floor(r() * 20);
    for (let i = 0; i < n; i++) {
      const p = [m.c[0] + gaussian(r) * m.spread, m.c[1] + gaussian(r) * m.spread, m.c[2] + gaussian(r) * m.spread];
      m.nodes.push(nodes.length);
      nodes.push(p);
    }
  }
  const d2 = (a, b) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;
  const edges = new Map();
  const add = (a, b, kind) => {
    if (a === b) return;
    const key = a < b ? `${a}-${b}` : `${b}-${a}`;
    if (!edges.has(key)) edges.set(key, [a, b, kind]);
  };
  for (const m of modules) {
    for (const i of m.nodes) {
      const near = m.nodes.filter((j) => j !== i).sort((a, b) => d2(nodes[i], nodes[a]) - d2(nodes[i], nodes[b]));
      add(i, near[0], 0);
      add(i, near[1], 0);
    }
  }
  for (const m of modules) {
    const others = modules.filter((o) => o !== m).sort((a, b) => d2(m.c, a.c) - d2(m.c, b.c));
    for (const o of others.slice(0, 2)) {
      add(m.nodes[Math.floor(r() * m.nodes.length)], o.nodes[Math.floor(r() * o.nodes.length)], 1);
    }
  }
  return { nodes, edges: [...edges.values()], r };
}

export function createField(canvas) {
  const gl = canvas.getContext("webgl2", {
    alpha: true,
    antialias: true,
    premultipliedAlpha: true,
    powerPreference: "low-power",
  });
  if (!gl) return null;

  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const vs = compile(gl.VERTEX_SHADER, VERT);
  const fs = compile(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;
  const prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
  gl.useProgram(prog);

  const u = (n) => gl.getUniformLocation(prog, n);
  const U = {
    mvp: u("uMVP"),
    dpr: u("uDpr"),
    time: u("uTime"),
    phase: u("uPhase"),
    activity: u("uActivity"),
    focus: u("uFocus"),
    ring: u("uRing"),
    ringFade: u("uRingFade"),
    gain: u("uGain"),
    bone: u("uBone"),
    ringCol: u("uRingCol"),
    accent: u("uAccent"),
    points: u("uPoints"),
  };

  const { nodes, edges, r } = buildGraph();
  const data = new Float32Array((edges.length * 2 + nodes.length) * 6);
  let o = 0;
  const put = (p, s, seed, kind) => {
    data.set([p[0], p[1], p[2], s, seed, kind], o);
    o += 6;
  };
  for (const [a, b, kind] of edges) {
    const seed = r();
    put(nodes[a], 0, seed, kind);
    put(nodes[b], 1, seed, kind);
  }
  for (const p of nodes) put(p, -1, r(), 0);
  const nLines = edges.length * 2;

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  gl.disable(gl.DEPTH_TEST);
  gl.uniform3fv(U.bone, C.bone);
  gl.uniform3fv(U.ringCol, C.violet);

  let w = 1;
  let h = 1;
  let dpr = 1;
  let gain = 1;
  let scroll = 0;
  let camY = SPAN_Y;
  let pointer = [0, 0];
  const lean = [0, 0];
  const tone = { ...TONES.idle, accent: [...TONES.idle.accent] };
  let target = TONES.idle;
  let phase = 0;
  let last = null;
  let ring = { at: -RING_EVERY, focus: nodes[0] };

  // The next blast radius opens from a symbol near where the camera is looking.
  const pickFocus = () => {
    const near = nodes.filter((p) => Math.abs(p[1] - camY) < 12 && p[2] > -34);
    const pool = near.length ? near : nodes;
    return pool[Math.floor(Math.random() * pool.length)];
  };

  function draw(seconds, still = false) {
    const dt = last === null ? 0 : Math.min(0.1, seconds - last);
    last = seconds;
    // Ease toward the tone, the scroll position and the pointer rather than
    // jump: a verdict arriving recolours the field over about a second.
    const ease = (rate) => (still ? 1 : 1 - Math.exp(-dt * rate));
    const k = ease(2.5);
    for (let i = 0; i < 3; i++) tone.accent[i] += (target.accent[i] - tone.accent[i]) * k;
    tone.flow += (target.flow - tone.flow) * k;
    tone.activity += (target.activity - tone.activity) * k;
    phase += dt * tone.flow;
    camY += (SPAN_Y - scroll * SPAN_Y * 2 - camY) * ease(4);
    lean[0] += (pointer[0] - lean[0]) * ease(3);
    lean[1] += (pointer[1] - lean[1]) * ease(3);

    if (!still && seconds - ring.at > RING_EVERY) ring = { at: seconds, focus: pickFocus() };
    const rt = still ? RING_GROW * 0.45 : seconds - ring.at;
    const grown = Math.min(1, rt / RING_GROW);

    const t = still ? 0 : seconds;
    const eye = [Math.sin(t * 0.045) * 5 + lean[0] * 6, camY + Math.cos(t * 0.03) * 1.5 - lean[1] * 4, 14];
    const at = [lean[0] * 2, camY - 3, -26];

    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniformMatrix4fv(U.mvp, false, mul4(perspective(FOVY, w / h, 0.1, 90), lookAt(eye, at)));
    gl.uniform1f(U.dpr, dpr);
    gl.uniform1f(U.time, t);
    gl.uniform1f(U.phase, still ? 0.4 : phase);
    gl.uniform1f(U.activity, tone.activity);
    gl.uniform3fv(U.accent, tone.accent);
    gl.uniform3fv(U.focus, ring.focus);
    gl.uniform1f(U.ring, 1 + 15 * (1 - (1 - grown) ** 3));
    gl.uniform1f(U.ringFade, grown < 1 ? Math.min(1, rt / 0.4) * (1 - grown) : 0);
    gl.uniform1f(U.gain, gain);

    gl.uniform1f(U.points, 0);
    gl.drawArrays(gl.LINES, 0, nLines);
    gl.uniform1f(U.points, 1);
    gl.drawArrays(gl.POINTS, nLines, nodes.length);
  }

  return {
    resize(width, height, ratio) {
      dpr = Math.min(ratio, 1.25);
      w = Math.max(1, width);
      h = Math.max(1, height);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    },
    setTone(name, opts = {}) {
      target = TONES[name] || TONES.idle;
      if (opts.gain !== undefined) gain = opts.gain;
    },
    setScroll(progress) {
      scroll = Math.max(0, Math.min(1, progress));
    },
    setPointer(x, y) {
      pointer = [x, y];
    },
    draw,
    destroy() {
      gl.deleteBuffer(buf);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(prog);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
    },
  };
}
