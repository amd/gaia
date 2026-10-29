// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The site background: a ship's-eye view of a deep Hubble field. A visit drops
// out of warp — long streaks that visibly brake — and settles to a cruise;
// scrolling is a burst of thrust; the pointer nudges the vanishing point like a
// slow course correction. Stars fill a volume, so near ones rush past while far
// ones crawl; bright ones carry Hubble's four-point diffraction spikes. Real
// Hubble galaxies and globular clusters (public/space/) sit far beyond the
// stars, turning slowly. A faint red haze sits behind the hero, a dark veil
// keeps the middle of the screen readable, and the whole field dims once the
// reader scrolls past the hero.
//
// Dependency-free and framework-agnostic. Colours come from tokens.css
// (--g-sky-*, --g-bg), never from literals here. Holds one still frame under
// prefers-reduced-motion, and while the visitor has paused it from the footer
// (html[data-motion="paused"], toggled by a `gaia-motion` event).
//
// The warp exit is exported (warpSpeed, warpTravelled, warpElapsed) so the
// headline scramble runs on the same clock and settles as the stars do.

export interface StarFieldOptions {
  /** Star count at a 1440x900 viewport; scaled by area, capped for big screens. */
  density?: number;
}

/** Detail of the `gaia-motion` event Footer.astro dispatches on `document`. */
export interface MotionEventDetail {
  paused: boolean;
}

export const CRUISE = 0.2; // speed at rest
export const WARP = 8; // the speed a visit starts at
export const ARRIVE_S = 3.3; // seconds to brake from WARP to CRUISE

/** Speed `t` seconds into the warp exit. Cubic ease-out: most of the braking happens early. */
export const warpSpeed = (t: number): number => CRUISE + (WARP - CRUISE) * (1 - Math.min(t, ARRIVE_S) / ARRIVE_S) ** 3;

/** Distance travelled by `t`: the integral of warpSpeed. */
export const warpTravelled = (t: number): number => {
  const u = Math.min(t, ARRIVE_S) / ARRIVE_S;
  return CRUISE * t + ((WARP - CRUISE) * ARRIVE_S * (1 - (1 - u) ** 4)) / 4;
};

let elapsed = ARRIVE_S;
let mounted = false;
/** Seconds into the warp exit; ARRIVE_S once it is over, or when the field never moved. */
export const warpElapsed = (): number => elapsed;
/** Whether mountStarField has run, so the warp clock is live. */
export const starFieldMounted = (): boolean => mounted;
/** Dispatched on `document` once the field has mounted and its warp clock is running. */
export const STARFIELD_READY = 'gaia-starfield-ready';

const DEPTH = 6; // how deep the star volume runs
const NEAR = 0.04; // a star this close has passed the viewer
const FOV = 0.55; // focal length, as a share of the shorter side
const TRAVEL = 0.55; // depth units covered per unit of speed per second
const STREAK = 0.05; // streak length per unit of speed above cruise
const SPIKE_MAG = 0.9; // magnitude above which a star shows diffraction spikes
const STEER = 0.1; // how far the pointer moves the vanishing point
const DIMMED = 0.35; // field strength once the hero has scrolled away
const TWINKLERS = 150; // backdrop points redrawn each frame; the rest are baked
const FADE_IN_S = 0.5; // a reborn star fades in rather than popping
const STREAK_MAX = 2.2; // px: streaks stay long but thin, however near the star
const GLOW_MAX = 28; // px
const CRUISE_FRAME_MS = 1000 / 30; // once settled, the slow drift needs no more than this

// Star colour by temperature, weighted roughly like a deep Hubble field.
const TEMPS: Array<[string, number]> = [
  ['--g-sky-o', 0.08],
  ['--g-sky-b', 0.18],
  ['--g-sky-star', 0.34],
  ['--g-sky-f', 0.2],
  ['--g-sky-g', 0.11],
  ['--g-sky-k', 0.07],
  ['--g-sky-m', 0.02],
];

// public/space/hubble-atlas.webp: 192 px cells, 5 across; see CREDITS.md there.
const ATLAS = '/space/hubble-atlas.webp';
const CELL = 192;
const COLS = 5;
const GALAXY_CELLS = 16;
const CLUSTER_CELLS = [16, 17, 18];
const EDGE_ON = new Set([2, 7, 8, 13, 15]); // seen edge-on: they rock instead of spin
const FACE_ON = [...Array(GALAXY_CELLS).keys()].filter((c) => !EDGE_ON.has(c));
const EDGE_ON_SHARE = 0.15; // small edge-on dust lanes read as scratches, so they are rare

interface Kind {
  cells: number[];
  count: number;
  z: [number, number];
  size: [number, number];
  alpha: [number, number];
  /** Share of the ship's speed this layer approaches at: far layers parallax less. */
  drift: number;
  /** Rotation in the object's own plane, radians per second. */
  spin: [number, number];
}
const KINDS: Record<'galaxy' | 'cluster', Kind> = {
  galaxy: { cells: [...Array(GALAXY_CELLS).keys()], count: 8, z: [7, 20], size: [0.5, 1.7], alpha: [0.18, 0.38], drift: 0.35, spin: [0.02, 0.07] },
  cluster: { cells: CLUSTER_CELLS, count: 2, z: [6, 14], size: [0.15, 0.35], alpha: [0.22, 0.36], drift: 1, spin: [0.004, 0.012] },
};

type RGB = [number, number, number];
interface Star {
  x: number;
  y: number;
  z: number;
  rgb: string;
  mag: number;
  /** Seconds since it was (re)born, for the fade-in. */
  age: number;
}
interface SpaceObject {
  kind: Kind;
  cell: number;
  x: number;
  y: number;
  z: number;
  size: number;
  alpha: number;
  flip: number;
  rot: number;
  rotV: number;
  spin: number;
  spinV: number;
  tilt: number;
  vx: number;
  vy: number;
}
interface Speck {
  x: number;
  y: number;
  size: number;
  rot: number;
  alpha: number;
  cell: number;
}
interface Mote {
  x: number;
  y: number;
  r: number;
  alpha: number;
  phase: number;
  rgb: string;
}

const readRGB = (name: string): RGB => {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const parts = raw.split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) {
    throw new Error(`[starfield] ${name} is "${raw}", expected an "R G B" triplet from tokens.css.`);
  }
  return parts as RGB;
};

const rnd = (a: number, b: number) => a + Math.random() * (b - a);

function glowSprite(rgb: string, spikes: boolean): HTMLCanvasElement {
  const S = 128;
  const h = S / 2;
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d');
  if (!g) throw new Error('[starfield] 2D canvas context is unavailable.');
  const core = g.createRadialGradient(h, h, 0, h, h, spikes ? 18 : h);
  core.addColorStop(0, `rgba(${rgb},1)`);
  core.addColorStop(0.1, `rgba(${rgb},1)`);
  core.addColorStop(0.3, `rgba(${rgb},0.3)`);
  core.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = core;
  g.fillRect(0, 0, S, S);
  if (spikes) {
    // Hubble's secondary-mirror struts: a thin + that fades outward.
    for (const horizontal of [true, false]) {
      const lg = horizontal ? g.createLinearGradient(0, h, S, h) : g.createLinearGradient(h, 0, h, S);
      lg.addColorStop(0, `rgba(${rgb},0)`);
      lg.addColorStop(0.42, `rgba(${rgb},0.35)`);
      lg.addColorStop(0.5, `rgba(${rgb},0.95)`);
      lg.addColorStop(0.58, `rgba(${rgb},0.35)`);
      lg.addColorStop(1, `rgba(${rgb},0)`);
      g.fillStyle = lg;
      if (horizontal) g.fillRect(0, h - 0.8, S, 1.6);
      else g.fillRect(h - 0.8, 0, 1.6, S);
    }
  }
  return c;
}

export function mountStarField(canvas: HTMLCanvasElement, opts: StarFieldOptions = {}): () => void {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('[starfield] 2D canvas context is unavailable.');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const hot = readRGB('--g-sky-hot').join(',');
  const ground = readRGB('--g-bg').join(',');
  const temps = TEMPS.map(([token, w]) => ({ rgb: readRGB(token).join(','), w }));
  const pickTemp = () => {
    let r = Math.random();
    for (const t of temps) if ((r -= t.w) <= 0) return t.rgb;
    return temps[2].rgb;
  };
  const glows = new Map<string, HTMLCanvasElement>();
  const sprite = (rgb: string, spikes: boolean) => {
    const key = rgb + (spikes ? '+' : '');
    let s = glows.get(key);
    if (!s) glows.set(key, (s = glowSprite(rgb, spikes)));
    return s;
  };

  const atlas = new Image();
  atlas.decoding = 'async';
  atlas.src = ATLAS;

  let paused = document.documentElement.dataset.motion === 'paused';
  const moving = () => !reduce && !paused;

  let W = 0;
  let H = 0;
  let F = 0;
  let spreadX = 1;
  let spreadY = 1;
  let vx = 0;
  let vy = 0;
  let tx = 0;
  let ty = 0;
  // Seconds into the warp exit; starts finished when the field is still.
  elapsed = moving() ? 0 : ARRIVE_S;
  mounted = true;
  let boost = 0;
  let lastScroll = scrollY;
  let last = performance.now();
  let raf = 0;
  const stars: Star[] = [];
  const objects: SpaceObject[] = [];
  let specks: Speck[] = [];
  let motes: Mote[] = [];
  let baked: Mote[] = [];
  let backdrop: HTMLCanvasElement | null = null;

  const spawnStar = (s: Star, z: number, age = 0): Star => {
    s.z = z;
    s.age = age;
    s.x = rnd(-1, 1) * z * spreadX;
    s.y = rnd(-1, 1) * z * spreadY;
    s.rgb = pickTemp();
    s.mag = Math.random() ** 2.4;
    return s;
  };

  const spawnObject = (o: SpaceObject, kind: Kind, z: number): SpaceObject => {
    const pool = kind === KINDS.galaxy && Math.random() >= EDGE_ON_SHARE ? FACE_ON : kind.cells;
    const cell = pool[Math.floor(Math.random() * pool.length)];
    const edgeOn = kind === KINDS.galaxy && EDGE_ON.has(cell);
    o.kind = kind;
    o.cell = cell;
    o.z = z;
    o.x = rnd(-1, 1) * z * spreadX * 0.85;
    o.y = rnd(-1, 1) * z * spreadY * 0.85;
    o.size = kind.size[0] + Math.random() ** 1.6 * (kind.size[1] - kind.size[0]);
    o.alpha = rnd(...kind.alpha);
    o.flip = Math.random() < 0.5 ? -1 : 1;
    o.rot = Math.random() * Math.PI * 2; // position angle, slowly turning
    o.rotV = rnd(-0.012, 0.012);
    o.spin = Math.random() * Math.PI * 2; // rotation in the object's own plane
    o.spinV = edgeOn ? 0 : (Math.random() < 0.5 ? -1 : 1) * rnd(...kind.spin);
    o.tilt = kind === KINDS.galaxy && !edgeOn ? rnd(0.35, 1) : 1; // a disk seen at an angle
    o.vx = rnd(-1, 1) * 0.025; // its own sideways motion, world units per second
    o.vy = rnd(-1, 1) * 0.018;
    return o;
  };

  const build = () => {
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    F = Math.min(W, H) * FOV;
    spreadX = (W / 2 / F) * 1.2;
    spreadY = (H / 2 / F) * 1.2;
    tx = W / 2;
    ty = H * 0.44;
    if (!vx) {
      vx = tx;
      vy = ty;
    }
    const n = Math.min(1500, Math.round(((opts.density ?? 1000) * W * H) / (1440 * 900)));
    while (stars.length < n) stars.push(spawnStar({ x: 0, y: 0, z: 0, rgb: '', mag: 0, age: 0 }, rnd(NEAR, DEPTH), FADE_IN_S));
    stars.length = n;
    if (!objects.length) {
      for (const kind of Object.values(KINDS)) {
        for (let i = 0; i < kind.count; i++) objects.push(spawnObject({} as SpaceObject, kind, rnd(...kind.z)));
      }
    }
    // Placed once, in 0..1 of the screen, so a resize redraws them where they were.
    if (!specks.length) {
      // A Hubble deep field behind everything: tiny, still galaxies.
      specks = Array.from({ length: 16 }, () => ({
        x: Math.random(),
        y: Math.random(),
        size: rnd(4, 11),
        rot: Math.random() * Math.PI * 2,
        alpha: rnd(0.15, 0.35),
        cell: Math.floor(Math.random() * GALAXY_CELLS),
      }));
      // The backdrop never moves, so it is baked; only its brightest points twinkle.
      const all = Array.from({ length: Math.round(n * 0.75) }, () => ({
        x: Math.random(),
        y: Math.random(),
        r: rnd(0.3, 0.85),
        alpha: 0.05 + Math.random() ** 3 * 0.3,
        phase: Math.random() * Math.PI * 2,
        rgb: pickTemp(),
      })).sort((a, b) => b.alpha - a.alpha);
      motes = all.slice(0, TWINKLERS);
      baked = all.slice(TWINKLERS);
    }
    backdrop = document.createElement('canvas');
    backdrop.width = W * dpr;
    backdrop.height = H * dpr;
    const bg = backdrop.getContext('2d');
    if (!bg) throw new Error('[starfield] 2D canvas context is unavailable.');
    bg.scale(dpr, dpr);
    for (const m of baked) {
      bg.fillStyle = `rgba(${m.rgb},${m.alpha.toFixed(3)})`;
      bg.beginPath();
      bg.arc(m.x * W, m.y * H, m.r, 0, Math.PI * 2);
      bg.fill();
    }
  };

  const drawCell = (cell: number, size: number) => {
    const sx = (cell % COLS) * CELL;
    const sy = Math.floor(cell / COLS) * CELL;
    ctx.drawImage(atlas, sx, sy, CELL, CELL, -size / 2, -size / 2, size, size);
  };

  const draw = (dt: number, now: number) => {
    if (moving()) {
      elapsed = Math.min(ARRIVE_S, elapsed + dt);
      boost *= Math.exp(-dt * 2.2);
      vx += (tx - vx) * Math.min(1, dt * 0.7);
      vy += (ty - vy) * Math.min(1, dt * 0.7);
    }
    const v = warpSpeed(elapsed) + boost;
    const warp = Math.min(1, (v - CRUISE) / (WARP - CRUISE));
    const step = moving() ? v * dt * TRAVEL : 0;
    const M = Math.min(W, H);
    const atlasReady = atlas.complete && atlas.naturalWidth > 0;
    // 1 at the top of the page, easing to DIMMED one viewport down.
    const strength = 1 - (1 - DIMMED) * Math.min(1, scrollY / Math.max(1, H));
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, W, H);

    // The haze: red behind the headline, gone by the edges.
    const r = Math.max(W, H) * 0.42;
    const haze = ctx.createRadialGradient(vx, vy, 0, vx, vy, r);
    haze.addColorStop(0, `rgba(${hot},${0.11 * strength})`);
    haze.addColorStop(1, `rgba(${hot},0)`);
    ctx.fillStyle = haze;
    ctx.fillRect(0, 0, W, H);

    ctx.globalCompositeOperation = 'lighter';
    if (backdrop) {
      ctx.globalAlpha = (1 - warp * 0.6) * strength;
      ctx.drawImage(backdrop, 0, 0, W, H);
    }
    for (const m of motes) {
      ctx.globalAlpha = m.alpha * (0.7 + 0.3 * Math.sin(now / 900 + m.phase)) * (1 - warp * 0.6) * strength;
      ctx.fillStyle = `rgb(${m.rgb})`;
      ctx.beginPath();
      ctx.arc(m.x * W, m.y * H, m.r, 0, Math.PI * 2);
      ctx.fill();
    }

    if (atlasReady) {
      for (const s of specks) {
        ctx.save();
        ctx.globalAlpha = s.alpha * (1 - warp * 0.5) * strength;
        ctx.translate(s.x * W, s.y * H);
        ctx.rotate(s.rot);
        drawCell(s.cell, s.size);
        ctx.restore();
      }
      // Galaxies and clusters, far to near; near ones smear along the line of flight at warp.
      ctx.globalCompositeOperation = 'screen';
      for (const o of objects) {
        const k = o.kind;
        if (moving()) {
          o.z -= step * k.drift;
          o.x += o.vx * dt * o.z * 0.2;
          o.y += o.vy * dt * o.z * 0.2;
          o.rot += o.rotV * dt;
          o.spin += o.spinV * dt;
        }
        const px = vx + (o.x / o.z) * F;
        const py = vy + (o.y / o.z) * F;
        const sz = (o.size * F) / o.z;
        if (o.z < 0.35 || px < -sz || px > W + sz || py < -sz || py > H + sz) {
          spawnObject(o, k, k.z[1]);
          continue;
        }
        const appear = Math.min(1, (k.z[1] - o.z) * 0.8);
        const tooClose = Math.max(0, Math.min(1, (1.6 * M - sz) / (0.8 * M)));
        const a = o.alpha * appear * tooClose * strength;
        if (a < 0.01) continue;
        const zt = o.z + (v - CRUISE) * STREAK * k.drift;
        const gx0 = vx + (o.x / zt) * F;
        const gy0 = vy + (o.y / zt) * F;
        const gsz = (o.size * F) / zt;
        const ghosts = sz < M * 0.6 && Math.hypot(px - gx0, py - gy0) > 3 ? 3 : 0;
        for (let g = ghosts; g >= 0; g--) {
          const f = ghosts ? g / ghosts : 0;
          ctx.save();
          ctx.globalAlpha = a * (g === 0 ? 1 : 0.22 * (1 - f));
          ctx.translate(px + (gx0 - px) * f, py + (gy0 - py) * f);
          ctx.rotate(o.rot);
          ctx.scale(o.flip, o.tilt);
          ctx.rotate(o.spin);
          drawCell(o.cell, sz + (gsz - sz) * f);
          ctx.restore();
        }
      }
      ctx.globalCompositeOperation = 'lighter';
    }

    // Stars. Additive blending, so draw order does not matter and nothing is sorted.
    ctx.lineCap = 'round';
    for (const s of stars) {
      s.z -= step;
      if (moving()) s.age += dt;
      if (s.z <= NEAR) {
        spawnStar(s, DEPTH);
        continue;
      }
      const X = vx + (s.x / s.z) * F;
      const Y = vy + (s.y / s.z) * F;
      // Reborn at the same depth, so the near field does not empty out after the warp.
      if (X < -80 || X > W + 80 || Y < -80 || Y > H + 80) {
        spawnStar(s, s.z);
        continue;
      }
      const fadeIn = Math.min(1, (DEPTH - s.z) * 1.5, s.age / FADE_IN_S);
      const bright = Math.min(1, ((0.35 + s.mag) * 1.5) / (s.z * 0.55 + 0.2)) * fadeIn * strength;
      if (bright < 0.015) continue;
      const size = (0.45 + s.mag * 1.6) * Math.min(5, 1 / s.z);
      // Streak: where the star was a moment ago; nothing at rest, long at warp.
      const tz = s.z + (v - CRUISE) * STREAK;
      const TX = vx + (s.x / tz) * F;
      const TY = vy + (s.y / tz) * F;
      const len = Math.hypot(X - TX, Y - TY);
      if (len > 1.5) {
        // A dim tail and a bright head read as a taper without a gradient per star.
        const MX = TX + (X - TX) * 0.5;
        const MY = TY + (Y - TY) * 0.5;
        ctx.strokeStyle = `rgb(${s.rgb})`;
        if (len > 20 && bright > 0.25) {
          ctx.globalAlpha = 0.2 * bright;
          ctx.lineWidth = Math.min(STREAK_MAX * 2.2, Math.max(2.5, size * 2.6));
          ctx.beginPath();
          ctx.moveTo(MX, MY);
          ctx.lineTo(X, Y);
          ctx.stroke();
        }
        ctx.lineWidth = Math.min(STREAK_MAX, Math.max(0.9, size * 0.85));
        ctx.globalAlpha = bright * 0.35;
        ctx.beginPath();
        ctx.moveTo(TX, TY);
        ctx.lineTo(MX, MY);
        ctx.stroke();
        ctx.globalAlpha = Math.min(1, bright * 1.3);
        ctx.beginPath();
        ctx.moveTo(MX, MY);
        ctx.lineTo(X, Y);
        ctx.stroke();
      }
      const glow = Math.min(GLOW_MAX, size * (2.6 + s.mag * 4) * (1 - warp * 0.35));
      ctx.globalAlpha = bright;
      ctx.drawImage(sprite(s.rgb, false), X - glow / 2, Y - glow / 2, glow, glow);
      // Spikes read at rest; at warp the streak carries the motion instead.
      if (s.mag > SPIKE_MAG && warp < 0.5) {
        const sp = Math.min(GLOW_MAX * 3, size * (8 + s.mag * 8));
        ctx.globalAlpha = bright * (1 - warp * 2);
        ctx.drawImage(sprite(s.rgb, true), X - sp / 2, Y - sp / 2, sp, sp);
      }
    }

    // The veil: darkens the middle of the screen, where the text is, so no
    // streak runs through a sentence at full strength.
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    const veil = ctx.createRadialGradient(W / 2, H * 0.48, 0, W / 2, H * 0.48, Math.max(W * 0.42, H * 0.5));
    veil.addColorStop(0, `rgba(${ground},0.72)`);
    veil.addColorStop(0.6, `rgba(${ground},0.45)`);
    veil.addColorStop(1, `rgba(${ground},0)`);
    ctx.fillStyle = veil;
    ctx.fillRect(0, 0, W, H);
  };

  const frame = (now: number) => {
    if (elapsed >= ARRIVE_S && boost < 0.02 && now - last < CRUISE_FRAME_MS) {
      raf = requestAnimationFrame(frame);
      return;
    }
    const dt = Math.min(now - last, 60) / 1000;
    last = now;
    draw(dt, now);
    if (moving()) raf = requestAnimationFrame(frame);
  };

  const start = () => {
    cancelAnimationFrame(raf);
    last = performance.now();
    raf = requestAnimationFrame(frame);
  };

  const still = () => draw(0, 0);

  // A still field has no loop to pick up the dimming, so scrolling redraws it —
  // at most once per frame, however many scroll events arrive.
  let stillRedraw = 0;
  const onScroll = () => {
    if (moving()) {
      boost = Math.min(1.8, boost + Math.abs(scrollY - lastScroll) * 0.004);
    } else if (!stillRedraw) {
      stillRedraw = requestAnimationFrame(() => {
        stillRedraw = 0;
        still();
      });
    }
    lastScroll = scrollY;
  };
  const onMove = (e: PointerEvent) => {
    tx = W / 2 + (e.clientX - W / 2) * STEER;
    ty = H * 0.44 + (e.clientY - H / 2) * STEER;
  };
  // Hidden tabs stop rAF anyway; resuming without this would replay the gap
  // as one long frame, which the dt clamp then turns into a visible stall.
  const onVisible = () => {
    last = performance.now();
  };
  const onMotion = (e: Event) => {
    paused = (e as CustomEvent<MotionEventDetail>).detail.paused;
    if (moving()) start();
    else cancelAnimationFrame(raf);
  };
  // A still field is drawn before the Hubble atlas arrives; draw it again once it has.
  const onAtlas = () => {
    if (!moving()) still();
  };
  const onAtlasError = () => {
    throw new Error(`[starfield] ${ATLAS} failed to load; the galaxies need it (public/space/).`);
  };

  addEventListener('scroll', onScroll, { passive: true });
  document.addEventListener('gaia-motion', onMotion);
  atlas.addEventListener('load', onAtlas);
  atlas.addEventListener('error', onAtlasError);
  if (!reduce) {
    addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('visibilitychange', onVisible);
  }
  const ro = new ResizeObserver(() => {
    build();
    if (!moving()) still();
  });
  ro.observe(canvas);

  build();
  if (moving()) start();
  else still();
  document.dispatchEvent(new Event(STARFIELD_READY));

  return () => {
    cancelAnimationFrame(raf);
    cancelAnimationFrame(stillRedraw);
    ro.disconnect();
    atlas.removeEventListener('load', onAtlas);
    atlas.removeEventListener('error', onAtlasError);
    removeEventListener('scroll', onScroll);
    removeEventListener('pointermove', onMove);
    document.removeEventListener('visibilitychange', onVisible);
    document.removeEventListener('gaia-motion', onMotion);
  };
}
