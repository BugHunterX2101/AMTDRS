/**
 * The recorded run as a fork tree in 3D, drawn with raw WebGL2.
 *
 * Layout: the green baseline (C0) on the left, a trunk to the planner's hub, one
 * node per planned task on a ring, and from each task one branch per attempt.
 * A branch grows while its attempt runs, snaps to full length in green when its
 * tests pass, and freezes where it stopped otherwise: dimmed when it was
 * cancelled because a sibling won, red when a gate rejected it. Once the patches
 * pass integration, the winning tips converge on the merge node on the right.
 *
 * Budget, the same one the old lattice backdrop kept:
 *   - one program, two draw calls (lines, then points), one preallocated buffer;
 *   - `low-power` GPU preference, device pixel ratio capped at 1.5;
 *   - 30 fps, paused off-screen and in a hidden tab (RunReplay.jsx);
 *   - a single still frame under prefers-reduced-motion;
 *   - silent failure without WebGL2: the page's CSS background shows instead.
 */

import { stateAt } from "./model";

const VERT = `#version 300 es
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec4 aCol;
layout(location = 2) in float aSize;
uniform mat4 uMVP;
uniform float uDpr;
uniform vec2 uShift;
out vec4 vCol;
void main() {
  vec4 p = uMVP * vec4(aPos, 1.0);
  p.xy += uShift * p.w;
  gl_Position = p;
  gl_PointSize = aSize * uDpr * (6.0 / max(p.w, 0.6));
  vCol = aCol;
}`;

const FRAG = `#version 300 es
precision mediump float;
in vec4 vCol;
uniform float uPoints;
uniform float uFade;
out vec4 outColor;
void main() {
  float a = vCol.a * uFade;
  if (uPoints > 0.5) {
    float r = length(gl_PointCoord - 0.5);
    float disc = smoothstep(0.5, 0.0, r);
    a *= disc * disc;
  }
  outColor = vec4(vCol.rgb * a, a);
}`;

const STRIDE = 8; // x y z  r g b a  size
const MAX_VERTS = 6000;

const C = {
  bone: [0.93, 0.91, 0.86],
  green: [0.36, 0.92, 0.58],
  blue: [0.5, 0.74, 1.0],
  red: [1.0, 0.4, 0.34],
  violet: [0.74, 0.64, 1.0],
};

export const SPEED = 8; // the run's clock runs 8x; the HUD shows the real elapsed time
export const HOLD = 7; // seconds the finished tree stays up before the replay loops

const C0 = [-2.4, 0, 0];
const HUB = [-1.1, 0, 0];
const MERGE = [3.1, 0, 0];

const FOVY = 0.62;
// The tree's half-extent on screen, in world units, at the widest the camera's
// sway shows it: the baseline-to-merge span across, the attempt ring up and down.
const HALF_W = 3.3;
const HALF_H = 2.2;

function mul4(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
  }
  return o;
}

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
}

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};

function lookAt(eye, at) {
  const z = norm(sub(eye, at));
  const x = norm(cross([0, 1, 0], z));
  const y = cross(z, x);
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
  ]);
}

function bezier(p0, p1, p2, s) {
  const u = 1 - s;
  return [
    u * u * p0[0] + 2 * u * s * p1[0] + s * s * p2[0],
    u * u * p0[1] + 2 * u * s * p1[1] + s * s * p2[1],
    u * u * p0[2] + 2 * u * s * p1[2] + s * s * p2[2],
  ];
}

// Deterministic pseudo-random, so the dust field is the same on every load.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function taskPoint(k, n) {
  const a = (k / n) * Math.PI * 2 + 0.5;
  return [0.05, 1.15 * Math.cos(a), 1.15 * Math.sin(a)];
}

function attemptTip(k, n, i, m) {
  const a = (k / n) * Math.PI * 2 + 0.5 + (i - (m - 1) / 2) * 0.36;
  const p = taskPoint(k, n);
  return [p[0] + 1.75, p[1] + 0.85 * Math.cos(a), p[2] + 0.85 * Math.sin(a)];
}

// How far a branch has grown, in run-time seconds since its attempt started.
const grow = (dt) => 0.94 * (1 - Math.exp(-dt / 16));

export function createReplay(canvas, run) {
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

  const u = (n) => gl.getUniformLocation(prog, n);
  const uMVP = u("uMVP");
  const uDpr = u("uDpr");
  const uShift = u("uShift");
  const uPoints = u("uPoints");
  const uFade = u("uFade");

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, MAX_VERTS * STRIDE * 4, gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 3, gl.FLOAT, false, STRIDE * 4, 0);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 4, gl.FLOAT, false, STRIDE * 4, 12);
  gl.enableVertexAttribArray(2);
  gl.vertexAttribPointer(2, 1, gl.FLOAT, false, STRIDE * 4, 28);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  gl.disable(gl.DEPTH_TEST);

  const lineData = new Float32Array(MAX_VERTS * STRIDE);
  const pointData = new Float32Array(MAX_VERTS * STRIDE);
  const r = rng(20261022);
  const dust = Array.from({ length: 520 }, () => {
    const th = r() * Math.PI * 2;
    const ph = Math.acos(2 * r() - 1);
    const rad = 5 + r() * 7;
    return [rad * Math.sin(ph) * Math.cos(th) + 0.4, rad * Math.cos(ph) * 0.6, rad * Math.sin(ph) * Math.sin(th), 0.08 + r() * 0.22, 0.6 + r() * 1.2];
  });
  const planAt = run.events.find((e) => e.k === "plan")?.t ?? Infinity;
  const integrationAt = run.events.find((e) => e.k === "integration")?.t ?? Infinity;
  const taskIndex = new Map(run.tasks.map((t, i) => [t.id, i]));
  const nTasks = run.tasks.length;

  let pointer = [0, 0];
  let dpr = 1;
  let shift = [0, 0];
  let dist = 8.2;

  function draw(runT, wall, fade) {
    const st = stateAt(run, runT);
    let nl = 0;
    let np = 0;

    const seg = (a, b, col, alpha) => {
      if (nl + 2 > MAX_VERTS) return;
      lineData.set([a[0], a[1], a[2], col[0], col[1], col[2], alpha, 1, b[0], b[1], b[2], col[0], col[1], col[2], alpha, 1], nl * STRIDE);
      nl += 2;
    };
    const dotAt = (p, col, alpha, size) => {
      if (np + 1 > MAX_VERTS) return;
      pointData.set([p[0], p[1], p[2], col[0], col[1], col[2], alpha, size], np * STRIDE);
      np += 1;
    };
    // A quadratic curve drawn from its start to fraction `upto` of its length.
    const curve = (p0, p1, p2, upto, col, alpha, beads) => {
      const steps = Math.max(1, Math.round(22 * upto));
      let prev = p0;
      for (let i = 1; i <= steps; i++) {
        const p = bezier(p0, p1, p2, (i / steps) * upto);
        seg(prev, p, col, alpha);
        if (beads && i % 3 === 0) dotAt(p, col, alpha * 0.9, 1.3);
        prev = p;
      }
      return prev;
    };

    for (const d of dust) dotAt(d, C.bone, d[3], d[4]);

    // The green baseline everything forks from. It breathes until it is ready.
    const breathe = 0.65 + 0.35 * Math.sin(wall * 2.2);
    dotAt(C0, C.violet, st.baseline ? 1 : breathe, st.baseline ? 9 : 6);
    dotAt(C0, C.violet, 0.18, 26);

    if (st.baseline) {
      curve(C0, [-1.75, 0.25, 0], HUB, 1, C.violet, 0.55, true);
      dotAt(HUB, C.bone, 0.85, 5);
    }

    const planGrow = Math.min(1, Math.max(0, (runT - planAt) / 10));
    const tips = [];
    for (const task of st.tasks) {
      if (planGrow <= 0) break;
      const k = taskIndex.get(task.id);
      const P = taskPoint(k, nTasks);
      curve(HUB, [(HUB[0] + P[0]) / 2, P[1] * 0.35, P[2] * 0.35], P, planGrow, C.bone, 0.42, false);
      if (planGrow < 1) continue;
      dotAt(P, task.settled ? C.green : C.bone, task.settled ? 1 : 0.7, task.settled ? 7 : 4.5);
      if (task.settled) dotAt(P, C.green, 0.16, 22);

      const m = Math.max(3, task.attempts.length);
      task.attempts.forEach((a, i) => {
        const tip = attemptTip(k, nTasks, i, m);
        const ctrl = [P[0] + 0.9, (P[1] + tip[1]) / 2 + P[1] * 0.12, (P[2] + tip[2]) / 2 + P[2] * 0.12];
        let upto = grow(runT - a.started);
        let col = C.blue;
        let alpha = 0.85;
        if (a.verdict === "ok") {
          upto = 1;
          col = C.green;
          alpha = 0.95;
        } else if (a.verdict === "cancelled") {
          upto = grow(a.ended - a.started);
          col = C.bone;
          alpha = 0.16;
        } else if (a.verdict) {
          upto = Math.max(0.12, grow(a.ended - a.started) * 0.55);
          col = C.red;
          alpha = 0.7;
        }
        const end = curve(P, ctrl, tip, upto, col, alpha, a.verdict !== "cancelled");
        if (a.verdict === "ok") {
          dotAt(end, C.green, 1, 6);
          tips.push(end);
        } else if (a.verdict && a.verdict !== "cancelled") {
          dotAt(end, C.red, 0.9, 4);
        } else if (!a.verdict) {
          // Work in flight: beads travel the branch toward its growing tip.
          for (let j = 0; j < 3; j++) {
            dotAt(bezier(P, ctrl, tip, ((wall * 0.45 + j / 3) % 1) * upto), C.blue, 0.9, 2.4);
          }
        }
      });
    }

    if (st.integration !== null) {
      const g = Math.min(1, (runT - integrationAt) / 14 + 0.05);
      for (const tip of tips) {
        curve(tip, [MERGE[0] - 0.6, tip[1] * 0.4, tip[2] * 0.4], MERGE, g, C.green, 0.26, false);
      }
      if (g >= 1) {
        const pulse = st.done ? 0.75 + 0.25 * Math.sin(wall * 1.6) : 0.6;
        dotAt(MERGE, C.green, 1, 11);
        dotAt(MERGE, C.green, 0.22 * pulse, 40);
        if (st.done) dotAt(MERGE, C.green, 0.08, 90);
      }
    }

    const w = canvas.width;
    const h = canvas.height;
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(prog);
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);

    // A slow sway rather than a full turn: seen end-on, the tree would lose the
    // left-to-right reading of baseline, tasks, merge that it exists to show.
    const yaw = 0.62 + 0.3 * Math.sin(wall * 0.07) + pointer[0] * 0.28;
    const pitch = 0.3 + pointer[1] * 0.12;
    const target = [0.45, 0, 0];
    const eye = [
      target[0] + dist * Math.sin(yaw) * Math.cos(pitch),
      target[1] + dist * Math.sin(pitch),
      target[2] + dist * Math.cos(yaw) * Math.cos(pitch),
    ];
    gl.uniformMatrix4fv(uMVP, false, mul4(perspective(FOVY, w / Math.max(1, h), 0.1, 80), lookAt(eye, target)));
    // Pulling the camera back to fit a smaller box would shrink every node with
    // it; scaling by distance keeps them the same size on screen.
    gl.uniform1f(uDpr, dpr * (dist / 8.2));
    gl.uniform2f(uShift, shift[0], shift[1]);
    gl.uniform1f(uFade, fade);

    gl.bufferSubData(gl.ARRAY_BUFFER, 0, lineData, 0, nl * STRIDE);
    gl.uniform1f(uPoints, 0);
    gl.drawArrays(gl.LINES, 0, nl);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pointData, 0, np * STRIDE);
    gl.uniform1f(uPoints, 1);
    gl.drawArrays(gl.POINTS, 0, np);
    return st;
  }

  const cycle = run.duration / SPEED + HOLD;
  return {
    // Maps wall-clock seconds since the replay began to the run's own clock.
    runTimeAt: (elapsed) => Math.min(run.duration, (elapsed % cycle) * SPEED),
    fadeAt: (elapsed) => {
      const local = elapsed % cycle;
      return Math.max(0, Math.min(1, local / 0.8, (cycle - local) / 0.9));
    },
    // Sizes the canvas to the stage and frames the tree inside `box`, a rectangle
    // in the stage's own CSS pixels: centred on it, and pulled back until it fits.
    resize(width, height, ratio, box) {
      dpr = Math.min(ratio, 1.5);
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      const W = Math.max(1, width);
      const H = Math.max(1, height);
      shift = [((box.x + box.width / 2) / W) * 2 - 1, 1 - ((box.y + box.height / 2) / H) * 2];
      const aspect = W / H;
      const halfH = Math.max(HALF_W / (aspect * Math.max(0.05, box.width / W)), HALF_H / Math.max(0.05, box.height / H));
      dist = Math.min(24, Math.max(7, halfH / Math.tan(FOVY / 2)));
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
