/**
 * The gate cascade behind every page, drawn with raw WebGL2.
 *
 * At the top the planner throws out a fountain of candidates. They spiral down
 * a narrowing vortex through seven sieve planes, one per gate. A candidate a
 * gate rejects strikes the sieve, skates to its rim and falls away as an ember;
 * most die at the first gates, where rejection is free. Each gate passed turns
 * a survivor greener, and below the last one the survivors converge into a
 * single beam that lands in the merge: the verified change. A star marks the
 * planner at the top and another the merge at the bottom. Progress moves the
 * camera down the cascade: on the landing page the scroll (the merge arrives
 * at the closing call to action), in the console the live job's stage, and the
 * console also tones it from the job's verdict.
 *
 * Every particle's path is a function of its seeds and the clock, computed in
 * the vertex shader, so a frame costs no CPU work beyond a few uniforms.
 *
 * Budget, the same one the hero's replay keeps:
 *   - static geometry, uploaded once; all motion is in the shaders;
 *   - one program, two draw calls (points, then the gate rims as lines);
 *   - `low-power` GPU preference, device pixel ratio capped at 1.25;
 *   - 30 fps and paused in a hidden tab (Field.jsx);
 *   - a single still frame under prefers-reduced-motion;
 *   - silent failure without WebGL2: the page's CSS background shows instead.
 */

import { lookAt, mul4, perspective, rng } from "./gl";

const YTOP = 20;
const YBOT = -44;
const YEND = -64;
const MERGE_Y = YEND + 1;
const RTOP = 15;
const RBOT = 1.1;
// One plane per gate, from scope at the top to the strength audit at the bottom.
const GATES = [10, 1, -8, -17, -25, -32, -38];
// Where candidates die: mostly at the cheap gates first, as the product intends.
// What is left (14%) survives every gate and joins the beam.
const FATE = [0.3, 0.22, 0.12, 0.08, 0.06, 0.05, 0.03];

const VERT = `#version 300 es
layout(location = 0) in vec4 aSeed;
// x: 0 candidate, 1 sieve mesh dot, 2 gate rim, 3 star, 4 beam
// y: the gate a candidate dies at (7 = survives), or the gate a mesh dot or rim belongs to
layout(location = 1) in vec2 aKind;
uniform mat4 uMVP;
uniform float uDpr;
uniform float uPhase;
uniform float uTime;
uniform float uDensity;
uniform float uGain;
uniform float uFogFrom;
uniform float uGateY[7];
uniform vec3 uBone;
uniform vec3 uAccent;
uniform vec3 uEmber;
uniform vec3 uAsh;
out vec4 vCol;
const float YTOP = ${YTOP.toFixed(1)};
const float YBOT = ${YBOT.toFixed(1)};
const float YEND = ${YEND.toFixed(1)};
const float RTOP = ${RTOP.toFixed(1)};
const float RBOT = ${RBOT.toFixed(1)};
const float TAU = 6.2831853;

float funnel(float y) {
  float u = clamp((y - YBOT) / (YTOP - YBOT), 0.0, 1.0);
  return mix(RBOT, RTOP, pow(u, 1.35));
}
vec3 polar(float r, float a, float y) { return vec3(r * cos(a), y, r * sin(a)); }

void main() {
  vec3 pos;
  vec3 col;
  float a;
  float size;
  float spin = uTime * 0.05;

  if (aKind.x < 0.5) {
    if (aSeed.w > uDensity) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; vCol = vec4(0.0); return; }
    float tau = fract(uPhase * (0.34 + 0.12 * aSeed.y) + aSeed.x);
    float y = YTOP + tau * (YEND - YTOP);
    float share = 0.22 + 0.78 * sqrt(aSeed.y);
    float swirl = 5.5 * (0.6 + 0.8 * aSeed.w);
    float ang = aSeed.z * TAU + tau * swirl + spin;
    int fate = int(aKind.y + 0.5);
    float gy = fate < 7 ? uGateY[fate] : -1e9;
    if (y < gy) {
      // Rejected: struck the sieve, skates to the rim, falls off as an ember.
      float tg = (YTOP - gy) / (YTOP - YEND);
      float age = tau - tg;
      float rg = funnel(gy);
      float at = aSeed.z * TAU + tg * swirl + spin;
      float slide = smoothstep(0.0, 0.045, age);
      float drop = max(0.0, age - 0.04);
      pos = polar(mix(share * rg, rg * 1.07, slide) + drop * 9.0, at + age * 1.5, gy - drop * drop * 420.0);
      col = mix(uEmber, uAsh, smoothstep(0.02, 0.14, age));
      a = 0.95 * exp(-age * 15.0);
      size = 2.4 + 2.2 * exp(-age * 30.0);
    } else {
      float passed = 0.0;
      for (int k = 0; k < 7; k++) passed += step(y, uGateY[k]);
      float g = passed / 7.0;
      if (y < YBOT) {
        // The beam: every survivor, converged.
        float b = clamp((YBOT - y) / (YBOT - YEND), 0.0, 1.0);
        pos = polar(0.18 + 0.35 * share * (1.0 - b), ang * 1.6, y);
        col = mix(uAccent, vec3(1.0), 0.18);
        a = 0.9 * (1.0 - b * 0.6);
        size = 3.2;
      } else {
        // The fountain opens from the planner's point, then follows the funnel.
        float open = smoothstep(0.0, 0.07, tau);
        pos = polar(funnel(y) * share * open, ang, y + (1.0 - open) * 2.5);
        col = mix(uBone, uAccent, g * g * 0.9 + 0.1 * g);
        a = (0.5 + 0.4 * g) * smoothstep(0.0, 0.025, tau);
        size = 1.7 + 1.5 * g;
      }
    }
  } else if (aKind.x > 3.5) {
    // The beam's own stream, so the payoff is dense whatever the survivors do.
    float tb = fract(uPhase * 0.55 + aSeed.x);
    float y = YBOT + 2.0 - tb * (YBOT + 2.0 - YEND);
    pos = polar(0.15 + 0.55 * aSeed.y * (1.0 - tb * 0.6), aSeed.z * TAU + uTime * 0.8 + tb * 8.0, y);
    col = mix(uAccent, vec3(1.0), 0.25 * aSeed.w);
    a = 0.75 * smoothstep(0.0, 0.08, tb) * (1.0 - smoothstep(0.85, 1.0, tb));
    size = 2.2;
  } else if (aKind.x > 2.5) {
    // Stars: the planner (w 0, halo 2) and the merge (w 1, halo 3), breathing.
    pos = aSeed.xyz;
    float merge = mod(aSeed.w, 2.0);
    float halo = step(1.5, aSeed.w);
    float breathe = 0.5 + 0.5 * sin(uTime * 1.6 + merge * 2.0);
    col = merge > 0.5 ? mix(uAccent, vec3(1.0), 0.3) : mix(uBone, uAccent, 0.3);
    a = halo > 0.5 ? (0.12 + 0.1 * merge) * (0.8 + 0.4 * breathe) : 0.7 + 0.3 * breathe;
    size = (halo > 0.5 ? 40.0 + 30.0 * merge : 11.0 + 9.0 * merge) * (0.9 + 0.2 * breathe);
  } else {
    // Sieve planes: a rim with a light sweeping round it, and a faint polar mesh.
    pos = aSeed.xyz;
    float k = aKind.y;
    float ang = atan(pos.z, pos.x);
    float sweep = pow(0.5 + 0.5 * cos(ang - uTime * 0.55 - k * 0.9), 14.0);
    col = mix(uBone, uAccent, 0.15 + 0.6 * k / 6.0);
    if (aKind.x < 1.5) {
      a = 0.09 + 0.35 * sweep;
      size = 1.1;
    } else {
      a = 0.2 + 0.75 * sweep;
      size = 1.0;
    }
  }

  vec4 p = uMVP * vec4(pos, 1.0);
  gl_Position = p;
  float fog = clamp(1.0 - (p.w - uFogFrom) / 70.0, 0.25, 1.0);
  gl_PointSize = size * uDpr * (62.0 / max(p.w, 1.0));
  vCol = vec4(col, a * fog * uGain);
}`;

const FRAG = `#version 300 es
precision mediump float;
in vec4 vCol;
uniform float uPoints;
out vec4 outColor;
void main() {
  float a = vCol.a;
  if (uPoints > 0.5) {
    float r = length(gl_PointCoord - 0.5);
    float disc = smoothstep(0.5, 0.0, r);
    a *= disc * disc;
  }
  outColor = vec4(vCol.rgb * a, a);
}`;

const C = {
  bone: [0.93, 0.91, 0.86],
  green: [0.36, 0.88, 0.56],
  blue: [0.49, 0.72, 1.0],
  amber: [0.94, 0.71, 0.3],
  red: [1.0, 0.42, 0.37],
  violet: [0.72, 0.64, 1.0],
  ember: [1.0, 0.56, 0.32],
  ash: [0.62, 0.22, 0.2],
};

// What each tone does to the cascade: the colour survivors turn and the beam
// burns, how fast candidates fall, and what share of them are drawn. `run` is
// the busiest: candidates are racing.
export const TONES = {
  idle: { accent: C.green, flow: 0.22, density: 0.78 },
  run: { accent: C.blue, flow: 0.48, density: 1 },
  verified: { accent: C.green, flow: 0.22, density: 0.85 },
  unverified: { accent: C.amber, flow: 0.18, density: 0.7 },
  failed: { accent: C.red, flow: 0.15, density: 0.6 },
  error: { accent: C.violet, flow: 0.15, density: 0.55 },
};

const FOVY = 0.9;
// The camera aims 2.5 below camY, so at the end of its travel the merge star
// sits at the centre of the screen.
const CAM_TOP = YTOP - 5;
const CAM_END = MERGE_Y + 2.5;

const funnel = (y) => {
  const u = Math.min(1, Math.max(0, (y - YBOT) / (YTOP - YBOT)));
  return RBOT + (RTOP - RBOT) * u ** 1.35;
};

function pickFate(x) {
  let acc = 0;
  for (let k = 0; k < FATE.length; k++) {
    acc += FATE[k];
    if (x < acc) return k;
  }
  return 7;
}

// Candidates, sieve mesh dots, the beam and the two stars (all points), then the
// gate rims (line segments), six floats a vertex.
function buildCascade(count) {
  const r = rng(20261022);
  const pts = [];
  for (let i = 0; i < count; i++) pts.push(r(), r(), r(), r(), 0, pickFate(r()));
  GATES.forEach((gy, k) => {
    const rad = funnel(gy);
    for (const share of [0.22, 0.42, 0.62, 0.82]) {
      const n = Math.round(110 * share);
      for (let j = 0; j < n; j++) {
        const a = (j / n) * Math.PI * 2 + k * 0.3;
        pts.push(rad * share * Math.cos(a), gy, rad * share * Math.sin(a), 0, 1, k);
      }
    }
  });
  for (let i = 0; i < Math.round(count / 10); i++) pts.push(r(), r(), r(), r(), 4, 0);
  for (const w of [0, 2]) pts.push(0, YTOP + 2.5, 0, w, 3, 0);
  for (const w of [1, 3]) pts.push(0, MERGE_Y, 0, w, 3, 0);
  const nPoints = pts.length / 6;
  const SEG = 240;
  GATES.forEach((gy, k) => {
    const rad = funnel(gy);
    for (let j = 0; j < SEG; j++) {
      for (const jj of [j, j + 1]) {
        const a = (jj / SEG) * Math.PI * 2;
        pts.push(rad * Math.cos(a), gy, rad * Math.sin(a), 0, 2, k);
      }
    }
  });
  return { data: new Float32Array(pts), nPoints, nLines: pts.length / 6 - nPoints };
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
    phase: u("uPhase"),
    time: u("uTime"),
    density: u("uDensity"),
    gain: u("uGain"),
    fogFrom: u("uFogFrom"),
    gateY: u("uGateY"),
    bone: u("uBone"),
    accent: u("uAccent"),
    ember: u("uEmber"),
    ash: u("uAsh"),
    points: u("uPoints"),
  };

  // Fewer candidates on a small screen: the cascade is narrower there anyway.
  const { data, nPoints, nLines } = buildCascade(window.innerWidth < 700 ? 7000 : 15000);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 24, 0);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 24, 16);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  gl.disable(gl.DEPTH_TEST);
  gl.uniform1fv(U.gateY, GATES);
  gl.uniform3fv(U.bone, C.bone);
  gl.uniform3fv(U.ember, C.ember);
  gl.uniform3fv(U.ash, C.ash);

  let w = 1;
  let h = 1;
  let dpr = 1;
  let gain = 1;
  let progress = 0;
  let camY = CAM_TOP;
  let pointer = [0, 0];
  const lean = [0, 0];
  const tone = { ...TONES.idle, accent: [...TONES.idle.accent] };
  let target = TONES.idle;
  let phase = 0.37;
  let last = null;

  function draw(seconds, still = false) {
    const dt = last === null ? 0 : Math.min(0.1, seconds - last);
    last = seconds;
    // Ease toward the tone, the progress and the pointer rather than
    // jump: a verdict arriving recolours the cascade over about a second.
    const ease = (rate) => (still ? 1 : 1 - Math.exp(-dt * rate));
    const k = ease(2.5);
    for (let i = 0; i < 3; i++) tone.accent[i] += (target.accent[i] - tone.accent[i]) * k;
    tone.flow += (target.flow - tone.flow) * k;
    tone.density += (target.density - tone.density) * k;
    phase += dt * tone.flow * 0.42;
    camY += (CAM_TOP + (CAM_END - CAM_TOP) * progress - camY) * ease(4);
    lean[0] += (pointer[0] - lean[0]) * ease(3);
    lean[1] += (pointer[1] - lean[1]) * ease(3);

    const t = still ? 0 : seconds;
    const aspect = w / h;
    // A narrow screen stands a little further back but lets the rims run off
    // its edges rather than shrinking the cascade to fit. A wide one sets its
    // axis right of centre, so the copy column on the left
    // sits over the quiet outer edge and the fountain rises beside the replay.
    const back = 30 * Math.max(1, 0.72 / Math.max(0.4, aspect));
    // Over the last quarter of the travel the axis drifts back to centre, so
    // the beam lands on the closing call to action.
    const s = Math.min(1, Math.max(0, (camY - CAM_TOP) / (CAM_END - CAM_TOP)));
    const centre = Math.min(1, Math.max(0, (s - 0.72) / 0.25));
    const off = aspect > 1.2 ? -7.5 * (1 - centre * centre * (3 - 2 * centre)) : 0;
    const eye = [off + Math.sin(t * 0.04) * 3 + lean[0] * 7, camY + 9 - lean[1] * 4, back];
    const at = [off + lean[0] * 2, camY - 2.5, 0];

    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniformMatrix4fv(U.mvp, false, mul4(perspective(FOVY, aspect, 0.5, 160), lookAt(eye, at)));
    gl.uniform1f(U.dpr, dpr);
    gl.uniform1f(U.phase, phase);
    gl.uniform1f(U.time, t);
    gl.uniform1f(U.density, tone.density);
    gl.uniform1f(U.gain, gain);
    gl.uniform1f(U.fogFrom, back - 16);
    gl.uniform3fv(U.accent, tone.accent);

    gl.uniform1f(U.points, 1);
    gl.drawArrays(gl.POINTS, 0, nPoints);
    gl.uniform1f(U.points, 0);
    gl.drawArrays(gl.LINES, nPoints, nLines);
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
    setProgress(p) {
      progress = Math.max(0, Math.min(1, p));
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
