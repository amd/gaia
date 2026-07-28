/*
 * GAIA constellation starfield — pointer-reactive canvas background.
 *
 * Framework-agnostic, dependency-free, no build step. Mount it on a fixed,
 * pointer-events:none <canvas> behind the page content. See StarField.astro.
 *
 * Honors prefers-reduced-motion: renders one static frame, no rAF loop.
 *
 * Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
 * SPDX-License-Identifier: MIT
 */

const STAR_CELL = 108;        // grid cell px — one candidate star each
const STAR_SKIP = 0.82;       // > this random value → skip the cell (open sky)
const LINK_MAX_DIST = 150;    // px — constellation line reach
const POINTER_RADIUS = 280;   // px — pointer influence radius
const PACKET_INTERVAL = 340;  // ms between signal packets
const PACKET_MAX = 46;

// Stellar temperature classes, weighted to the real sky: mostly blue-white and
// white, few warm. This is what keeps the field from reading as a gold wash.
// Weights are fixed; only the four colors are themeable.
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
  let pointerX = null, pointerY = null;
  let raf = 0, last = performance.now(), spawnAcc = 0;

  function build() {
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    if (!W || !H) return;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    nodes = []; edges = []; packets = [];
    const cols = Math.ceil(W / STAR_CELL), rows = Math.ceil(H / STAR_CELL);
    let id = 0;

    for (let gx = 0; gx < cols; gx++) {
      for (let gy = 0; gy < rows; gy++) {
        const s = gx * 73.3 + gy * 191.7;
        if (rnd(s + 9) > STAR_SKIP) continue;
        const mag = rnd(s + 5);              // magnitude: most tiny, few bright
        const t = rnd(s + 13);
        const col = pal.stars[CLASS_WEIGHTS.findIndex((w) => t < w)] || pal.stars[0];
        nodes.push({
          id: id++,
          x: (gx + 0.15 + rnd(s) * 0.7) * STAR_CELL,
          y: (gy + 0.15 + rnd(s + 3) * 0.7) * STAR_CELL,
          col,
          base: 0.5 + mag * mag * 1.8,       // radius
          bright: 0.28 + mag * 0.5,
          tw: rnd(s + 7) * 6.28,             // twinkle phase
          tws: 0.4 + rnd(s + 11) * 0.9,      // twinkle speed
          pulse: 0,                          // 0..1, set when a packet arrives
        });
      }
    }

    // Constellation lines: each star joins its 1–2 nearest neighbours.
    const maxD2 = LINK_MAX_DIST * LINK_MAX_DIST;
    const seen = new Set();
    nodes.forEach((a) => {
      const near = [];
      nodes.forEach((b) => {
        if (b === a) return;
        const d2 = (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
        if (d2 < maxD2) near.push({ b, d2 });
      });
      near.sort((p, q) => p.d2 - q.d2);
      const links = a.base > 1.4 ? 2 : 1;    // brighter stars anchor more lines
      for (let k = 0; k < Math.min(links, near.length); k++) {
        const b = near[k].b;
        const key = a.id < b.id ? `${a.id}:${b.id}` : `${b.id}:${a.id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        edges.push({ a, b });
      }
    });
  }

  function draw(now) {
    const dt = Math.min(now - last, 60);
    last = now;
    ctx.clearRect(0, 0, W, H);

    // Faint cool zodiacal glow — depth without a color cast.
    const sx = W * 0.8, sy = H * 0.14, sr = Math.max(W, H) * 0.45;
    const glow = ctx.createRadialGradient(sx, sy, 0, sx, sy, sr);
    glow.addColorStop(0, `rgba(${gr},${gg},${gb},${pal.glowAlpha})`);
    glow.addColorStop(0.5, `rgba(${gr},${gg},${gb},${pal.glowAlpha / 3})`);
    glow.addColorStop(1, `rgba(${gr},${gg},${gb},0)`);
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, W, H);

    // Pointer proximity, squared falloff.
    const near = (x, y) => {
      if (pointerX == null) return 0;
      const d = Math.hypot(x - pointerX, y - pointerY);
      if (d > POINTER_RADIUS) return 0;
      const k = 1 - d / POINTER_RADIUS;
      return k * k;
    };

    // Constellation lines.
    ctx.lineWidth = 1;
    edges.forEach((e) => {
      const k = Math.max(
        near(e.a.x, e.a.y),
        near(e.b.x, e.b.y),
        near((e.a.x + e.b.x) / 2, (e.a.y + e.b.y) / 2),
      );
      ctx.strokeStyle = `rgba(${lr},${lg},${lb},${pal.lineAlpha + k * 0.22})`;
      ctx.beginPath();
      ctx.moveTo(e.a.x, e.a.y);
      ctx.lineTo(e.b.x, e.b.y);
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
        const x = p.e.a.x + (p.e.b.x - p.e.a.x) * p.t;
        const y = p.e.a.y + (p.e.b.y - p.e.a.y) * p.t;
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
        const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, halo);
        g.addColorStop(0, `rgba(${cr},${cg},${cb},${a * 0.32})`);
        g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(n.x, n.y, halo, 0, 7); ctx.fill();
      }
      ctx.fillStyle = `rgba(${cr},${cg},${cb},${Math.min(1, a + 0.15)})`;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 7); ctx.fill();
    });

    raf = requestAnimationFrame(draw);
  }

  const onMove = (e) => { pointerX = e.clientX; pointerY = e.clientY; };
  const onLeave = () => { pointerX = pointerY = null; };
  addEventListener('pointermove', onMove, { passive: true });
  addEventListener('pointerleave', onLeave);

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
