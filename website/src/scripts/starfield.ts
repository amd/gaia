// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The site background: stars streaking past as if the page were a ship under
// way. A visit drops out of warp — long streaks that visibly brake — and settles
// to a cruise; scrolling is a burst of
// thrust; the pointer nudges the vanishing point like a slow course correction.
// A faint red haze sits behind the hero, a dark veil keeps the middle of the
// screen readable, and the whole field dims once the reader scrolls past the hero.
//
// Dependency-free and framework-agnostic. Colours come from tokens.css
// (--g-sky-star, --g-sky-hot, --g-bg), never from literals here. Holds one still
// frame under prefers-reduced-motion, and while the visitor has paused it from
// the footer (html[data-motion="paused"], toggled by a `gaia-motion` event).

export interface StarFieldOptions {
  /** Streak count at a 1440x900 viewport; scaled by area, capped for big screens. */
  density?: number;
}

/** Detail of the `gaia-motion` event Footer.astro dispatches on `document`. */
export interface MotionEventDetail {
  paused: boolean;
}

const CRUISE = 0.2; // depth units per second at rest
const WARP = 8; // the speed a visit starts at
const ARRIVE_S = 2.6; // seconds to brake from WARP to CRUISE
const TRAIL = 0.1; // streak length, as depth travelled
const HOT_SHARE = 0.08; // share of streaks drawn in the brand red
const STEER = 0.1; // how far the pointer moves the vanishing point
const DIMMED = 0.35; // field strength once the hero has scrolled away
const FADE_TAIL_FROM = 0.45; // nearness past which a streak gets a fading tail

type RGB = [number, number, number];
interface Star {
  x: number;
  y: number;
  z: number;
  hot: boolean;
}

const readRGB = (name: string): RGB => {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const parts = raw.split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) {
    throw new Error(`[starfield] ${name} is "${raw}", expected an "R G B" triplet from tokens.css.`);
  }
  return parts as RGB;
};

export function mountStarField(canvas: HTMLCanvasElement, opts: StarFieldOptions = {}): () => void {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('[starfield] 2D canvas context is unavailable.');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const star = readRGB('--g-sky-star').join(',');
  const hot = readRGB('--g-sky-hot').join(',');
  const ground = readRGB('--g-bg').join(',');

  let paused = document.documentElement.dataset.motion === 'paused';
  const moving = () => !reduce && !paused;

  let W = 0;
  let H = 0;
  let vx = 0;
  let vy = 0;
  let tx = 0;
  let ty = 0;
  // Seconds into the warp exit; starts finished when the field is still.
  let arrival = moving() ? 0 : ARRIVE_S;
  let boost = 0;
  let lastScroll = scrollY;
  let last = performance.now();
  let raf = 0;
  const stars: Star[] = [];

  const spawn = (s: Star, z = 1): Star => {
    const a = Math.random() * Math.PI * 2;
    const r = 0.03 + Math.random() ** 0.7;
    s.x = Math.cos(a) * r;
    s.y = Math.sin(a) * r;
    s.z = z;
    s.hot = Math.random() < HOT_SHARE;
    return s;
  };

  const build = () => {
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    tx = W / 2;
    ty = H * 0.44;
    if (!vx) {
      vx = tx;
      vy = ty;
    }
    const n = Math.min(1100, Math.round(((opts.density ?? 700) * W * H) / (1440 * 900)));
    while (stars.length < n) stars.push(spawn({ x: 0, y: 0, z: 0, hot: false }, Math.random()));
    stars.length = n;
  };

  const draw = (dt: number) => {
    if (moving()) {
      arrival = Math.min(ARRIVE_S, arrival + dt);
      boost *= Math.exp(-dt * 2.2);
      vx += (tx - vx) * Math.min(1, dt * 0.7);
      vy += (ty - vy) * Math.min(1, dt * 0.7);
    }
    // Cubic ease-out: most of the braking happens early, then a long glide in.
    const v = CRUISE + (WARP - CRUISE) * (1 - arrival / ARRIVE_S) ** 3 + boost;
    const f = Math.max(W, H) * 0.55;
    // 1 at the top of the page, easing to DIMMED one viewport down.
    const strength = 1 - (1 - DIMMED) * Math.min(1, scrollY / Math.max(1, H));
    ctx.clearRect(0, 0, W, H);

    // The haze: red behind the headline, gone by the edges.
    const r = Math.max(W, H) * 0.42;
    const haze = ctx.createRadialGradient(vx, vy, 0, vx, vy, r);
    haze.addColorStop(0, `rgba(${hot},${0.11 * strength})`);
    haze.addColorStop(1, `rgba(${hot},0)`);
    ctx.fillStyle = haze;
    ctx.fillRect(0, 0, W, H);

    ctx.lineCap = 'round';
    for (const s of stars) {
      if (moving()) s.z -= v * dt;
      if (s.z <= 0.015) {
        spawn(s);
        continue;
      }
      const X = vx + (s.x / s.z) * f;
      const Y = vy + (s.y / s.z) * f;
      if (X < -40 || X > W + 40 || Y < -40 || Y > H + 40) {
        spawn(s);
        continue;
      }
      const tz = Math.min(1, s.z + v * TRAIL + 0.004);
      const TX = vx + (s.x / tz) * f;
      const TY = vy + (s.y / tz) * f;
      const near = 1 - s.z;
      const col = s.hot ? hot : star;
      const alpha = Math.min(1, near * 1.4 + 0.1) * strength;
      // Only the near, long streaks show their tail; a far one is a few pixels,
      // and a gradient per star every frame is most of the cost.
      if (near > FADE_TAIL_FROM) {
        const g = ctx.createLinearGradient(TX, TY, X, Y);
        g.addColorStop(0, `rgba(${col},0)`);
        g.addColorStop(1, `rgba(${col},${alpha})`);
        ctx.strokeStyle = g;
      } else {
        ctx.strokeStyle = `rgba(${col},${alpha * 0.7})`;
      }
      ctx.lineWidth = 0.6 + near * near * 2.2;
      ctx.beginPath();
      ctx.moveTo(TX, TY);
      ctx.lineTo(X, Y);
      ctx.stroke();
    }

    // The veil: darkens the middle of the screen, where the text is, so no
    // streak runs through a sentence at full strength.
    const veil = ctx.createRadialGradient(W / 2, H * 0.48, 0, W / 2, H * 0.48, Math.max(W * 0.42, H * 0.5));
    veil.addColorStop(0, `rgba(${ground},0.72)`);
    veil.addColorStop(0.6, `rgba(${ground},0.45)`);
    veil.addColorStop(1, `rgba(${ground},0)`);
    ctx.fillStyle = veil;
    ctx.fillRect(0, 0, W, H);
  };

  const frame = (now: number) => {
    const dt = Math.min(now - last, 60) / 1000;
    last = now;
    draw(dt);
    if (moving()) raf = requestAnimationFrame(frame);
  };

  const start = () => {
    cancelAnimationFrame(raf);
    last = performance.now();
    raf = requestAnimationFrame(frame);
  };

  const onScroll = () => {
    if (moving()) {
      boost = Math.min(1.8, boost + Math.abs(scrollY - lastScroll) * 0.004);
    } else {
      // A still field has no loop to pick up the dimming, so redraw it once.
      draw(0);
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

  addEventListener('scroll', onScroll, { passive: true });
  document.addEventListener('gaia-motion', onMotion);
  if (!reduce) {
    addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('visibilitychange', onVisible);
  }
  const ro = new ResizeObserver(() => {
    build();
    if (!moving()) draw(0);
  });
  ro.observe(canvas);

  build();
  if (moving()) start();
  else draw(0);

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    removeEventListener('scroll', onScroll);
    removeEventListener('pointermove', onMove);
    document.removeEventListener('visibilitychange', onVisible);
    document.removeEventListener('gaia-motion', onMotion);
  };
}
