/*
 * GAIA star chart — pointer-reactive canvas background.
 *
 * The figures are REAL: eleven classical constellations at their J2000
 * positions, gnomonically projected per figure and scattered across the
 * viewport (see constellations.js). Star size and brightness come from visual
 * magnitude, so Betelgeuse outshines Meissa here exactly as it does overhead.
 * Faint field stars fill the gaps; only the real asterisms carry lines.
 *
 * Framework-agnostic, dependency-free, no build step. Mount it on a fixed,
 * pointer-events:none <canvas> behind the page content. See StarField.astro.
 *
 * Pointer-reactive: a smoothed cursor trails the real one, brightening nearby
 * stars and lines and drifting nearby stars toward it. No ambient glow — the
 * reaction lives entirely in the field itself. Honors prefers-reduced-motion:
 * one static frame, no rAF loop, no pointer reaction at all.
 *
 * Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
 * SPDX-License-Identifier: MIT
 */

import { CONSTELLATIONS, projectFigure } from './constellations.js';

const FIELD_CELL = 128;       // px — one candidate field star per cell
const FIELD_SKIP = 0.55;      // > this random value → empty cell (open sky)
const FIG_CELL_W = 470;       // px — one constellation per cell, at most
const FIG_CELL_H = 430;
const FIG_SKIP = 0.72;        // > this → leave the cell empty
const POINTER_RADIUS = 300;   // px — pointer influence radius
const POINTER_ATTRACT = 12;   // px — max drift of a star toward the pointer
const POINTER_SMOOTH = 160;   // ms — time constant of the trailing pointer
const PACKET_INTERVAL = 420;  // ms between signal packets
const PACKET_MAX = 30;

// Stellar temperature classes, weighted to the real sky: mostly blue-white and
// white, few warm. This is what keeps the field from reading as a gold wash.
const CLASS_WEIGHTS = [0.42, 0.78, 0.93, 1.0];

// Dark ground (the designed default).
const DARK_PALETTE = {
  stars: [[201, 215, 255], [248, 247, 255], [255, 244, 234], [255, 210, 161]],
  line: [190, 200, 225],
  lineAlpha: 0.045,
  glow: [190, 200, 230],
  glowAlpha: 0.045,
  packetCore: [255, 240, 210],
};

// Deterministic hash-noise so the sky is stable across reloads and resizes.
const rnd = (s) => {
  const x = Math.sin(s * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

// Projected once at module load — the geometry never changes, only its placement.
const FIGURES = CONSTELLATIONS.map(projectFigure);

/**
 * @param {HTMLCanvasElement} canvas
 * @param {Partial<typeof DARK_PALETTE>} [palette] Theme colors; defaults to the
 *   dark ground. Only the palette is themeable — geometry, density, twinkle and
 *   packet behavior are identical in both themes.
 * @returns {() => void} destroy
 */
export function mountStarField(canvas, palette) {
  if (!canvas) return () => {};
  const ctx = canvas.getContext('2d');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const pal = { ...DARK_PALETTE, ...(palette || {}) };
  const [lr, lg, lb] = pal.line;
  const [gr, gg, gb] = pal.glow;
  const [pr, pg, pb] = pal.packetCore;

  let W = 0, H = 0;
  let nodes = [], edges = [], packets = [];
  let pointerX = null, pointerY = null;   // raw pointer (event target)
  let spx = null, spy = null, pk = 0;     // smoothed pointer + influence 0..1
  let raf = 0, last = performance.now(), spawnAcc = 0;

  const starColor = (seed) => {
    const t = rnd(seed);
    return pal.stars[CLASS_WEIGHTS.findIndex((w) => t < w)] || pal.stars[0];
  };

  const addStar = (x, y, radius, bright, seed) => {
    const n = {
      x, y,
      dx: x, dy: y,                        // drawn position (pointer drift)
      col: starColor(seed),
      base: radius,
      bright,
      tw: rnd(seed + 7) * 6.28,            // twinkle phase
      tws: 0.4 + rnd(seed + 11) * 0.9,     // twinkle speed
      pulse: 0,                            // 0..1, set when a packet arrives
    };
    nodes.push(n);
    return n;
  };

  // One constellation, placed in a cell: projected coordinates scaled to the
  // cell, rotated a few degrees so the sky does not read as a grid of decals.
  function placeFigure(fig, cx, cy, size, rot, seed) {
    const cos = Math.cos(rot), sin = Math.sin(rot);
    const placed = fig.pts.map((p, i) => {
      const x = cx + (p.x * cos - p.y * sin) * size;
      const y = cy + (p.x * sin + p.y * cos) * size;
      // Visual magnitude → radius and brightness. Real magnitudes run roughly
      // 0–5 here, and the scale is inverted: smaller number, brighter star.
      const r = Math.max(0.65, 2.35 - p.mag * 0.33);
      const b = Math.max(0.3, 0.92 - p.mag * 0.1);
      return addStar(x, y, r, b, seed + i * 17.3);
    });

    fig.lines.forEach(([a, b]) => edges.push({ a: placed[a], b: placed[b] }));
  }

  function build() {
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    if (!W || !H) return;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    nodes = []; edges = []; packets = [];

    // 1. Field stars — texture between the figures, never joined by lines.
    const cols = Math.ceil(W / FIELD_CELL), rows = Math.ceil(H / FIELD_CELL);
    for (let gx = 0; gx < cols; gx++) {
      for (let gy = 0; gy < rows; gy++) {
        const s = gx * 73.3 + gy * 191.7;
        if (rnd(s + 9) > FIELD_SKIP) continue;
        const mag = rnd(s + 5);
        addStar(
          (gx + 0.15 + rnd(s) * 0.7) * FIELD_CELL,
          (gy + 0.15 + rnd(s + 3) * 0.7) * FIELD_CELL,
          0.45 + mag * mag * 1.1,
          0.2 + mag * 0.34,
          s + 13,
        );
      }
    }

    // 2. Constellations — one per cell at most, cycling the catalogue so the
    //    same figure never lands twice side by side.
    const fcols = Math.max(1, Math.round(W / FIG_CELL_W));
    const frows = Math.max(1, Math.round(H / FIG_CELL_H));
    const cw = W / fcols, ch = H / frows;
    let pick = 0;
    for (let gx = 0; gx < fcols; gx++) {
      for (let gy = 0; gy < frows; gy++) {
        const s = gx * 37.1 + gy * 53.9 + 4.2;
        if (fcols * frows > 2 && rnd(s + 21) > FIG_SKIP) continue;
        const fig = FIGURES[pick++ % FIGURES.length];
        placeFigure(
          fig,
          (gx + 0.22 + rnd(s + 1) * 0.56) * cw,
          (gy + 0.22 + rnd(s + 2) * 0.56) * ch,
          Math.min(cw, ch) * (0.44 + rnd(s + 3) * 0.2),
          (rnd(s + 4) - 0.5) * 0.34,          // ±~10°
          s * 3.7,
        );
      }
    }
  }

  function draw(now) {
    const dt = Math.min(now - last, 60);
    last = now;
    ctx.clearRect(0, 0, W, H);

    // The pointer the field reacts to trails the real one (exponential
    // smoothing), and its influence fades in/out instead of popping. In a
    // reduced-motion static frame pk stays 0, so the field never reacts.
    if (!reduce) {
      const t = 1 - Math.exp(-dt / POINTER_SMOOTH);
      if (pointerX != null) {
        if (spx == null) { spx = pointerX; spy = pointerY; }
        spx += (pointerX - spx) * t;
        spy += (pointerY - spy) * t;
        pk += (1 - pk) * t;
      } else {
        pk += (0 - pk) * t;
      }
    }

    // Faint cool zodiacal glow — depth without a color cast.
    const sx = W * 0.8, sy = H * 0.14, sr = Math.max(W, H) * 0.45;
    const glow = ctx.createRadialGradient(sx, sy, 0, sx, sy, sr);
    glow.addColorStop(0, `rgba(${gr},${gg},${gb},${pal.glowAlpha})`);
    glow.addColorStop(0.5, `rgba(${gr},${gg},${gb},${pal.glowAlpha / 3})`);
    glow.addColorStop(1, `rgba(${gr},${gg},${gb},0)`);
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, W, H);

    // Pointer proximity against the SMOOTHED pointer, squared falloff, scaled
    // by the fade so leaving the window releases the field gradually.
    const near = (x, y) => {
      if (spx == null || pk < 0.01) return 0;
      const d = Math.hypot(x - spx, y - spy);
      if (d > POINTER_RADIUS) return 0;
      const k = 1 - d / POINTER_RADIUS;
      return k * k * pk;
    };

    // Each star drifts a touch toward the pointer. Computed once per frame and
    // reused by lines, packets and the star pass so the geometry stays coherent.
    nodes.forEach((n) => {
      const k = near(n.x, n.y);
      if (k > 0) {
        const d = Math.hypot(spx - n.x, spy - n.y) || 1;
        const m = k * POINTER_ATTRACT;
        n.dx = n.x + ((spx - n.x) / d) * m;
        n.dy = n.y + ((spy - n.y) / d) * m;
      } else {
        n.dx = n.x;
        n.dy = n.y;
      }
    });

    // Asterism lines.
    ctx.lineWidth = 1;
    edges.forEach((e) => {
      const k = Math.max(
        near(e.a.x, e.a.y),
        near(e.b.x, e.b.y),
        near((e.a.x + e.b.x) / 2, (e.a.y + e.b.y) / 2),
      );
      ctx.strokeStyle = `rgba(${lr},${lg},${lb},${pal.lineAlpha + k * 0.22})`;
      ctx.beginPath();
      ctx.moveTo(e.a.dx, e.a.dy);
      ctx.lineTo(e.b.dx, e.b.dy);
      ctx.stroke();
    });

    // Gold signal packets travelling the lines — the only gold in the field.
    if (!reduce) {
      spawnAcc += dt;
      while (spawnAcc > PACKET_INTERVAL) {
        spawnAcc -= PACKET_INTERVAL;
        if (packets.length < PACKET_MAX && edges.length) {
          packets.push({
            e: edges[(Math.random() * edges.length) | 0],
            t: 0,
            sp: 0.006 + Math.random() * 0.006,
          });
        }
      }
      for (let i = packets.length - 1; i >= 0; i--) {
        const p = packets[i];
        p.t += p.sp * (dt / 16.67);
        if (p.t >= 1) {
          p.e.b.pulse = 1;                   // arrival lights the target star
          packets.splice(i, 1);
          continue;
        }
        const x = p.e.a.dx + (p.e.b.dx - p.e.a.dx) * p.t;
        const y = p.e.a.dy + (p.e.b.dy - p.e.a.dy) * p.t;
        const g = ctx.createRadialGradient(x, y, 0, x, y, 7);
        g.addColorStop(0, 'rgba(244,196,107,0.65)');
        g.addColorStop(1, 'rgba(231,163,60,0)');
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.fill();
        ctx.fillStyle = `rgba(${pr},${pg},${pb},0.9)`;
        ctx.beginPath(); ctx.arc(x, y, 1.3, 0, 7); ctx.fill();
      }
    }

    // Stars: twinkle, pointer lift, halo only on the brighter ones.
    nodes.forEach((n) => {
      if (!reduce) n.tw += (n.tws * dt) / 1000;
      if (n.pulse > 0) n.pulse = Math.max(0, n.pulse - dt / 700);
      const tw = 0.62 + 0.38 * Math.sin(n.tw);
      const k = near(n.x, n.y);
      const r = n.base + n.pulse * 2.4 + k * 1.5;
      const a = Math.min(1, n.bright * tw + n.pulse * 0.6 + k * 0.5);
      const [cr, cg, cb] = n.col;

      if (n.base > 1.1 || n.pulse > 0.02 || k > 0.05) {
        const halo = n.base * 3 + 4 + n.pulse * 10 + k * 12;
        const g = ctx.createRadialGradient(n.dx, n.dy, 0, n.dx, n.dy, halo);
        g.addColorStop(0, `rgba(${cr},${cg},${cb},${a * 0.32})`);
        g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(n.dx, n.dy, halo, 0, 7); ctx.fill();
      }
      ctx.fillStyle = `rgba(${cr},${cg},${cb},${Math.min(1, a + 0.15)})`;
      ctx.beginPath(); ctx.arc(n.dx, n.dy, r, 0, 7); ctx.fill();
    });

    raf = requestAnimationFrame(draw);
  }

  const onMove = (e) => { pointerX = e.clientX; pointerY = e.clientY; };
  const onLeave = () => { pointerX = pointerY = null; };
  // Reduced motion renders one static frame — a pointer must not animate it.
  if (!reduce) {
    addEventListener('pointermove', onMove, { passive: true });
    addEventListener('pointerleave', onLeave);
  }

  const ro = new ResizeObserver(() => build());
  ro.observe(canvas);

  build();
  draw(performance.now());
  if (reduce) cancelAnimationFrame(raf);   // one static frame only

  return function destroy() {
    cancelAnimationFrame(raf);
    ro.disconnect();
    removeEventListener('pointermove', onMove);
    removeEventListener('pointerleave', onLeave);
  };
}
