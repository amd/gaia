// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the per-state tray badges
 * (src/gaia/apps/webui/services/tray-icons.cjs), drawn on the real logo assets.
 */

const fs = require("fs");
const path = require("path");
const { decodeRgbaPng } = require("./_helpers/tray-icon-png.cjs");
const {
  STATES,
  BADGE_COLORS,
  drawStateIcon,
  drawOverlayIcon,
} = require("../../src/gaia/apps/webui/services/tray-icons.cjs");

const ASSETS = path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "assets");

/** A real asset as Electron hands it over: premultiplied BGRA. */
function asset(file) {
  const { width, height, pixels } = decodeRgbaPng(fs.readFileSync(path.join(ASSETS, file)), file);
  const buffer = Buffer.alloc(pixels.length);
  for (let i = 0; i < pixels.length; i += 4) {
    const a = pixels[i + 3] / 255;
    buffer[i] = Math.round(pixels[i + 2] * a);
    buffer[i + 1] = Math.round(pixels[i + 1] * a);
    buffer[i + 2] = Math.round(pixels[i] * a);
    buffer[i + 3] = pixels[i + 3];
  }
  return { width, height, buffer };
}

/** Which pixels of the badge corner are mostly badge colour: the badge's silhouette. */
function badgeMask(buffer, size, [r, g, b]) {
  const bits = [];
  const from = Math.floor(size / 2);
  for (let y = from; y < size; y++) {
    for (let x = from; x < size; x++) {
      const i = (y * size + x) * 4;
      const a = buffer[i + 3];
      const near = a > 200 && Math.abs(buffer[i + 2] - r) < 24 && Math.abs(buffer[i + 1] - g) < 24 && Math.abs(buffer[i] - b) < 24;
      bits.push(near ? 1 : 0);
    }
  }
  return bits.join("");
}

const BADGED = STATES.filter((s) => s !== "idle");

describe.each([
  ["tray-icon.png", 32],
  ["tray-icon@2x.png", 64],
])("%s", (file, size) => {
  const base = asset(file);

  test("idle leaves the logo untouched", () => {
    expect(drawStateIcon(base, "idle").equals(base.buffer)).toBe(true);
  });

  test("never changes the input bitmap", () => {
    const before = Buffer.from(base.buffer);
    drawStateIcon(base, "error");
    expect(base.buffer.equals(before)).toBe(true);
  });

  test.each(BADGED)("%s draws a badge in its colour in the bottom-right", (state) => {
    const mask = badgeMask(drawStateIcon(base, state), size, BADGE_COLORS[state]);
    expect(mask).toMatch(/1/);
    // The logo's top-left stays untouched.
    const out = drawStateIcon(base, state);
    for (let y = 0; y < size * 0.4; y++) {
      for (let x = 0; x < size * 0.4; x++) {
        const i = (y * size + x) * 4;
        expect(out.subarray(i, i + 4).equals(base.buffer.subarray(i, i + 4))).toBe(true);
      }
    }
  });

  test("the four badges are different shapes, not just colours", () => {
    const masks = BADGED.map((state) => badgeMask(drawStateIcon(base, state), size, BADGE_COLORS[state]));
    expect(new Set(masks).size).toBe(BADGED.length);
  });

  test("a clear gap separates the badge from the logo", () => {
    const out = drawStateIcon(base, "needs-you");
    // On the diagonal, between the badge's edge (0.27) and the cut's (0.34).
    const at = Math.floor(size * (0.72 - 0.305 / Math.SQRT2));
    const i = (at * size + at) * 4;
    expect(base.buffer[i + 3]).toBeGreaterThan(200);
    expect(out[i + 3]).toBeLessThan(40);
  });
});

describe("macOS template images", () => {
  test.each([["tray-iconTemplate.png"], ["tray-iconTemplate@2x.png"]])(
    "%s badges are black, so the OS can tint them",
    (file) => {
      const base = asset(file);
      for (const state of BADGED) {
        const out = drawStateIcon(base, state, { template: true });
        const mask = badgeMask(out, base.width, [0, 0, 0]);
        expect(mask).toMatch(/1/);
      }
    }
  );

  test("template badges keep distinct shapes", () => {
    const base = asset("tray-iconTemplate@2x.png");
    const masks = BADGED.map((s) => badgeMask(drawStateIcon(base, s, { template: true }), base.width, [0, 0, 0]));
    expect(new Set(masks).size).toBe(BADGED.length);
  });
});

describe("taskbar overlay", () => {
  test.each([
    ["needs-you", 16],
    ["needs-you", 32],
    ["unread", 16],
  ])("%s at %ipx fills a transparent square", (kind, size) => {
    const out = drawOverlayIcon(kind, size);
    expect(out.length).toBe(size * size * 4);
    expect(out[3]).toBe(0);
    // Off the "!" that a large needs-you badge knocks out of its centre.
    const inside = ((size / 2) * size + Math.round(size * 0.72)) * 4;
    expect(out[inside + 3]).toBe(255);
  });

  // Colour alone isn't enough (#5350): compare shapes, i.e. the alpha channel.
  const alpha = (buf) => Buffer.from(buf.filter((_, i) => i % 4 === 3));
  test.each([[16], [20], [24], [32]])("at %ipx needs-you has a mark the unread dot doesn't", (size) => {
    expect(alpha(drawOverlayIcon("needs-you", size)).equals(alpha(drawOverlayIcon("unread", size)))).toBe(false);
  });
});

describe("input checks", () => {
  test("an unknown state fails loudly", () => {
    expect(() => drawStateIcon(asset("tray-icon.png"), "busy")).toThrow(/unknown tray state "busy"/);
  });

  test("a bitmap of the wrong length fails loudly", () => {
    expect(() => drawStateIcon({ width: 16, height: 16, buffer: Buffer.alloc(10) }, "idle")).toThrow(
      /must hold 1024 bytes, got 10/
    );
  });

  test("a non-square bitmap fails loudly", () => {
    expect(() => drawStateIcon({ width: 16, height: 8, buffer: Buffer.alloc(512) }, "idle")).toThrow(/square/);
  });

  test("an unknown overlay fails loudly", () => {
    expect(() => drawOverlayIcon("error", 16)).toThrow(/no taskbar overlay for "error"/);
  });
});
