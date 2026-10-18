// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tray and taskbar-overlay icons for each tray state, drawn onto raw bitmaps.
 *
 * Each state adds its own badge shape to the bottom-right of the logo, so the
 * state reads without colour (macOS template images are one colour, and some
 * users can't tell the colours apart):
 *
 *   idle       no badge
 *   working    an open ring, like a spinner
 *   waiting    an hourglass: waiting for the model to load or answer
 *   needs-you  a solid disc (with "!" where there is room)
 *   error      a solid triangle
 *
 * Bitmaps are Electron's native layout: premultiplied BGRA, row-major.
 */

const STATES = Object.freeze(["idle", "working", "waiting", "needs-you", "error"]);

// Mid-tones that hold up on both light and dark taskbars.
const BADGE_COLORS = Object.freeze({
  working: [0xb0, 0x8d, 0x57], // brass
  waiting: [0x94, 0x8a, 0x7c], // warm gray
  "needs-you": [0xe5, 0xa1, 0x3a], // amber
  error: [0xd9, 0x49, 0x3f], // red
  unread: [0xb0, 0x8d, 0x57],
});

// The badge sits in a disc of this radius (fraction of the icon) ...
const BADGE_RADIUS = 0.27;
// ... cut out of the logo with this much clear space around it.
const BADGE_GAP = 0.07;
const SUPERSAMPLE = 4;

/** Point-in-shape tests in badge space: u, v in [-1, 1], v grows downward. */
const SHAPES = {
  working(u, v) {
    const r = Math.hypot(u, v);
    if (r > 1 || r < 0.5) return false;
    // A gap in the top-right quarter makes it a spinner, not a plain ring.
    return !(u > 0 && v < 0);
  },
  waiting(u, v) {
    if (Math.abs(v) > 0.95) return false;
    if (Math.abs(v) >= 0.75) return Math.abs(u) <= 0.8;
    return Math.abs(u) <= 0.75 * Math.abs(v) + 0.14;
  },
  "needs-you"(u, v, size) {
    if (Math.hypot(u, v) > 1) return false;
    // "!" only where it is at least a pixel wide.
    if (size >= 32 && Math.abs(u) <= 0.16) {
      if (v >= -0.62 && v <= 0.2) return false;
      if (v >= 0.38 && v <= 0.64) return false;
    }
    return true;
  },
  error(u, v) {
    if (v < -0.95 || v > 0.85) return false;
    return Math.abs(u) <= ((v + 0.95) / 1.8) * 1.05;
  },
  unread(u, v) {
    return Math.hypot(u, v) <= 1;
  },
};

/** Fraction of the pixel at (px, py) where `inside(x, y)` holds. */
function coverage(px, py, inside) {
  let hits = 0;
  for (let sy = 0; sy < SUPERSAMPLE; sy++) {
    for (let sx = 0; sx < SUPERSAMPLE; sx++) {
      if (inside(px + (sx + 0.5) / SUPERSAMPLE, py + (sy + 0.5) / SUPERSAMPLE)) hits++;
    }
  }
  return hits / (SUPERSAMPLE * SUPERSAMPLE);
}

function checkBitmap(bitmap) {
  const { width, height, buffer } = bitmap || {};
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || width !== height) {
    throw new TypeError(`tray icon bitmap must be square, got ${width}x${height}`);
  }
  if (!Buffer.isBuffer(buffer) || buffer.length !== width * height * 4) {
    throw new TypeError(
      `tray icon bitmap buffer must hold ${width * height * 4} bytes, got ${buffer && buffer.length}`
    );
  }
}

/**
 * Draw a badge into `out` (premultiplied BGRA). With `knockout`, first clear
 * the logo under and around the badge so it stands apart from it.
 */
function paintBadge(out, size, kind, { template, knockout, center, radius }) {
  const shape = SHAPES[kind];
  if (!shape) throw new Error(`no tray badge shape for "${kind}"`);
  const [r, g, b] = template ? [0, 0, 0] : BADGE_COLORS[kind];
  const cx = center * size;
  const cy = center * size;
  const rad = radius * size;
  const clear = (BADGE_RADIUS + BADGE_GAP) * size;

  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const i = (py * size + px) * 4;
      if (knockout) {
        const cut = coverage(px, py, (x, y) => Math.hypot(x - cx, y - cy) <= clear);
        if (cut > 0) {
          for (let c = 0; c < 4; c++) out[i + c] = Math.round(out[i + c] * (1 - cut));
        }
      }
      const cov = coverage(px, py, (x, y) => shape((x - cx) / rad, (y - cy) / rad, size));
      if (cov === 0) continue;
      const keep = 1 - cov;
      const a = 255 * cov;
      out[i] = Math.round(b * cov + out[i] * keep);
      out[i + 1] = Math.round(g * cov + out[i + 1] * keep);
      out[i + 2] = Math.round(r * cov + out[i + 2] * keep);
      out[i + 3] = Math.round(a + out[i + 3] * keep);
    }
  }
}

/**
 * The logo bitmap with `state`'s badge drawn on. Idle returns an unchanged copy.
 *
 * @param {{width:number, height:number, buffer:Buffer}} bitmap Premultiplied BGRA.
 * @param {string} state One of STATES.
 * @param {{template?: boolean}} [opts] Draw in black for a macOS template image.
 * @returns {Buffer}
 */
function drawStateIcon(bitmap, state, opts = {}) {
  checkBitmap(bitmap);
  if (!STATES.includes(state)) {
    throw new Error(`unknown tray state "${state}"; expected one of ${STATES.join(", ")}`);
  }
  const out = Buffer.from(bitmap.buffer);
  if (state === "idle") return out;
  const center = 1 - BADGE_RADIUS - 0.01;
  paintBadge(out, bitmap.width, state, {
    template: Boolean(opts.template),
    knockout: true,
    center,
    radius: BADGE_RADIUS,
  });
  return out;
}

/**
 * A Windows taskbar overlay: the badge alone, filling a transparent square.
 *
 * @param {"needs-you"|"unread"} kind
 * @param {number} size Pixel size.
 * @returns {Buffer} Premultiplied BGRA.
 */
function drawOverlayIcon(kind, size) {
  if (kind !== "needs-you" && kind !== "unread") {
    throw new Error(`no taskbar overlay for "${kind}"; expected needs-you or unread`);
  }
  if (!Number.isInteger(size) || size <= 0) {
    throw new TypeError(`overlay size must be a positive integer, got ${size}`);
  }
  const out = Buffer.alloc(size * size * 4);
  paintBadge(out, size, kind, { template: false, knockout: false, center: 0.5, radius: 0.47 });
  return out;
}

module.exports = { STATES, BADGE_COLORS, drawStateIcon, drawOverlayIcon };
