// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Checks that a Linux tray-icon PNG is a real logo rather than a flat
 * placeholder (#5268). Shared by
 * tray-manager.test.cjs (source tree) and appimage-smoke.test.mjs (packaged).
 */

const zlib = require("zlib");

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** Decode an 8-bit RGBA, non-interlaced PNG into { width, height, pixels }. */
function decodeRgbaPng(buf, label) {
  if (!buf.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error(`${label}: not a PNG file`);
  }
  let offset = 8;
  let header = null;
  const idat = [];
  while (offset < buf.length) {
    const length = buf.readUInt32BE(offset);
    const type = buf.toString("ascii", offset + 4, offset + 8);
    const data = buf.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        interlace: data[12],
      };
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
    offset += 12 + length;
  }
  if (!header) throw new Error(`${label}: PNG has no IHDR chunk`);
  if (header.bitDepth !== 8 || header.colorType !== 6 || header.interlace !== 0) {
    throw new Error(
      `${label}: expected 8-bit RGBA non-interlaced PNG, got bitDepth=` +
        `${header.bitDepth} colorType=${header.colorType} interlace=${header.interlace}`
    );
  }

  const { width, height } = header;
  const bpp = 4;
  const stride = width * bpp;
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const pixels = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? pixels[y * stride + x - bpp] : 0;
      const b = y > 0 ? pixels[(y - 1) * stride + x] : 0;
      const c = x >= bpp && y > 0 ? pixels[(y - 1) * stride + x - bpp] : 0;
      let pred;
      if (filter === 0) pred = 0;
      else if (filter === 1) pred = a;
      else if (filter === 2) pred = b;
      else if (filter === 3) pred = (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else throw new Error(`${label}: unknown PNG filter type ${filter}`);
      pixels[y * stride + x] = (line[x] + pred) & 0xff;
    }
  }
  return { width, height, pixels };
}

/**
 * Throw unless `buf` is a `size`×`size` RGBA PNG of the round GAIA logo:
 * transparent corners and many distinct colours. A flat square fails both.
 */
function assertTrayLogoPng(buf, size, label) {
  const { width, height, pixels } = decodeRgbaPng(buf, label);
  if (width !== size || height !== size) {
    throw new Error(`${label}: expected ${size}x${size}, got ${width}x${height}`);
  }
  const alphaAt = (x, y) => pixels[(y * width + x) * 4 + 3];
  const corners = [
    [0, 0],
    [width - 1, 0],
    [0, height - 1],
    [width - 1, height - 1],
  ];
  for (const [x, y] of corners) {
    if (alphaAt(x, y) !== 0) {
      throw new Error(
        `${label}: corner (${x},${y}) is opaque — expected the round logo on a ` +
          "transparent background, not a filled square"
      );
    }
  }
  const colours = new Set();
  for (let i = 0; i < pixels.length; i += 4) {
    if (pixels[i + 3] > 0) colours.add(pixels.readUInt32BE(i));
  }
  const minColours = 64;
  if (colours.size < minColours) {
    throw new Error(
      `${label}: only ${colours.size} distinct visible colours (need >= ` +
        `${minColours}) — looks like a placeholder, not the GAIA logo`
    );
  }
}

module.exports = { decodeRgbaPng, assertTrayLogoPng };
