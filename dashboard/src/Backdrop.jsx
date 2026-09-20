import { useEffect, useRef } from "react";

/**
 * The 3D backdrop: a procedural lattice of nodes and edges receding into
 * depth, drifting toward the viewer. It is not decoration picked at random —
 * it is the shape this product is about. A code graph with a blast radius,
 * forks branching off a checkpoint, candidates racing in parallel. Flying
 * slowly through that lattice is the one ambient image that means something
 * here.
 *
 * Engineering constraints it is built around, because this sits behind a
 * dense real-time console and must never compete with it:
 *
 *   - One draw call. A single fullscreen triangle; every node, edge and
 *     depth layer is computed procedurally in the fragment shader, so there
 *     is no geometry, no buffers to stream and no per-frame CPU work beyond
 *     setting three uniforms.
 *   - `powerPreference: "low-power"`. A background has no business waking a
 *     discrete GPU on a laptop that is about to stream SSE for 20 minutes.
 *   - Device pixel ratio capped at 1.5. A backdrop this diffuse gains
 *     nothing visible from 3x, and costs ~4x the fragments.
 *   - Paused entirely while the tab is hidden, and frozen (rendered once,
 *     then left alone) under prefers-reduced-motion.
 *   - Fails silently. If WebGL2 is unavailable the canvas renders nothing
 *     and the page's own CSS background is what shows — no error, no
 *     fallback library, no layout difference.
 *
 * Colour is driven from outside via `from`/`to`, so the job's own verdict can
 * tint the room it happens in: violet→green while work is being verified,
 * amber when the answer is NoPR, red when a run was aborted.
 */

const VERT = `#version 300 es
void main() {
  // Fullscreen triangle from gl_VertexID — no attribute buffers at all.
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
out vec4 fragColor;

uniform vec2  uRes;
uniform float uTime;
uniform vec3  uFrom;
uniform vec3  uTo;
uniform float uAlpha;

const int LAYERS = 14;

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  vec3 col = vec3(0.0);
  float drift = uTime * 0.045;

  for (int i = 0; i < LAYERS; i++) {
    float fi = float(i);

    // Each layer cycles 0..1 in depth; 0 is far away, 1 is at the camera.
    float d = fract(fi / float(LAYERS) + drift);

    // Perspective: distant layers are tiled small, near layers are huge.
    float persp = mix(7.0, 0.4, d);

    // Fade in from the far plane and back out as it passes the camera, so
    // layers are never seen popping into or out of existence.
    float fade = smoothstep(0.0, 0.14, d) * smoothstep(1.0, 0.72, d);

    vec2 p = uv * persp;
    vec2 cell = floor(p);
    vec2 f = fract(p) - 0.5;

    float h = hash21(cell + fi * 7.31);
    vec2 jitter = (vec2(h, fract(h * 57.0)) - 0.5) * 0.55;

    // The node itself.
    float node = smoothstep(0.17, 0.0, length(f - jitter));

    // The lattice edges between nodes, much fainter than the nodes.
    float grid = min(abs(f.x), abs(f.y));
    float edge = smoothstep(0.03, 0.0, grid) * 0.16;

    // Nodes breathe out of phase with one another.
    float pulse = 0.66 + 0.34 * sin(uTime * 1.2 + h * 24.0);

    vec3 tint = mix(uFrom, uTo, clamp(d * 1.15 + h * 0.3, 0.0, 1.0));
    col += tint * (node * pulse + edge) * fade;
  }

  // Vignette: fall off towards the edges of the frame, so the lattice reads as
  // depth rather than as wallpaper. The panels sit on their own opaque
  // surfaces, so what stays legible under them is a question of panel
  // background, not of how bright the layer behind them is.
  col *= smoothstep(1.3, 0.12, length(uv));

  // Alpha tracks luminance so dark regions stay genuinely transparent and
  // the page's own background shows through, rather than being painted over.
  float luma = max(col.r, max(col.g, col.b));
  fragColor = vec4(col, luma) * uAlpha;
}`;

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

function hexToRgb(hex) {
  const v = parseInt(hex.replace("#", ""), 16);
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}

export function Backdrop({ from = "#a371f7", to = "#3fb950", alpha = 0.5 }) {
  const canvasRef = useRef(null);
  // Colours are read through a ref so a verdict change re-tints the running
  // animation without tearing down and rebuilding the GL context.
  const colorRef = useRef({ from, to, alpha });
  colorRef.current = { from, to, alpha };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
      preserveDrawingBuffer: false,
    });
    if (!gl) return undefined;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let stopped = false;
    let u = null; // uniform locations, re-resolved whenever the program is built
    const start = performance.now();

    // Compiling and linking lives in here rather than inline so that a context
    // that comes back after being lost can be rebuilt from scratch — shaders,
    // program and uniform locations are all invalidated by a context loss and
    // none of them survive to be reused.
    const build = () => {
      const vs = compile(gl, gl.VERTEX_SHADER, VERT);
      const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
      if (!vs || !fs) return false;

      const prog = gl.createProgram();
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
      gl.useProgram(prog);

      u = {
        res: gl.getUniformLocation(prog, "uRes"),
        time: gl.getUniformLocation(prog, "uTime"),
        from: gl.getUniformLocation(prog, "uFrom"),
        to: gl.getUniformLocation(prog, "uTo"),
        alpha: gl.getUniformLocation(prog, "uAlpha"),
      };
      return true;
    };

    if (!build()) return undefined;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const w = Math.floor(canvas.clientWidth * dpr);
      const h = Math.floor(canvas.clientHeight * dpr);
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
      }
    };

    const draw = (t) => {
      if (!u || gl.isContextLost()) return;
      resize();
      const { from: f, to: o, alpha: a } = colorRef.current;
      gl.uniform2f(u.res, canvas.width, canvas.height);
      gl.uniform1f(u.time, t);
      gl.uniform3fv(u.from, hexToRgb(f));
      gl.uniform3fv(u.to, hexToRgb(o));
      gl.uniform1f(u.alpha, a);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const loop = () => {
      if (stopped) return;
      draw((performance.now() - start) / 1000);
      raf = requestAnimationFrame(loop);
    };

    const stop = () => {
      stopped = true;
      cancelAnimationFrame(raf);
    };

    const go = () => {
      // Paint one frame unconditionally before checking anything else. A page
      // can report document.hidden on its very first mount (prerendering, a
      // tab restored in the background, some headless/automation contexts) —
      // gating the *first* paint on visibility left the canvas permanently
      // transparent whenever that happened, since nothing would ever trigger
      // a second attempt. Visibility should only ever pause the loop after
      // there is already something on screen, never prevent the first frame.
      draw((performance.now() - start) / 1000);

      if (reduced.matches) {
        // Static from here — one real frame, then nothing moves.
        return;
      }
      if (document.hidden) return;
      stopped = false;
      raf = requestAnimationFrame(loop);
    };

    const onVisibility = () => (document.hidden ? stop() : go());

    // A WebGL context can be taken away at any time — a GPU driver reset, too
    // many WebGL surfaces alive at once, a laptop switching GPUs. Without
    // this the render loop would keep issuing draw calls at 60fps against a
    // dead context forever (each one a silent no-op) and the canvas would
    // stay blank for the rest of the session. Stop on loss; rebuild on
    // restore, which is the only way the backdrop ever comes back.
    const onLost = (e) => {
      e.preventDefault(); // required, or "restored" is never fired
      stop();
      u = null;
    };
    const onRestored = () => {
      if (build()) go();
    };

    canvas.addEventListener("webglcontextlost", onLost);
    canvas.addEventListener("webglcontextrestored", onRestored);

    go();
    document.addEventListener("visibilitychange", onVisibility);
    reduced.addEventListener("change", go);
    window.addEventListener("resize", resize);

    return () => {
      stop();
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      document.removeEventListener("visibilitychange", onVisibility);
      reduced.removeEventListener("change", go);
      window.removeEventListener("resize", resize);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, []);

  return <canvas ref={canvasRef} className="backdrop" aria-hidden="true" />;
}
