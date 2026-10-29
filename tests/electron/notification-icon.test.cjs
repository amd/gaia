// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Desktop notifications must carry a small icon. KDE Plasma's panel crashed on
// every banner while GAIA passed its 4096 px, 10 MB app icon (#5888).

const fs = require("fs");
const path = require("path");

const WEBUI = path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const { notificationIconPath, appIconPath } = require(path.join(WEBUI, "services", "app-icon.cjs"));

const MAX_SIDE = 256;
const MAX_BYTES = 256 * 1024;

function pngSize(file) {
  const buf = fs.readFileSync(file);
  expect(buf.subarray(1, 4).toString("ascii")).toBe("PNG");
  expect(buf.toString("ascii", 12, 16)).toBe("IHDR");
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20), bytes: buf.length };
}

describe("notification icon", () => {
  test("is a small square PNG that exists in the app's assets", () => {
    const file = notificationIconPath();
    expect(fs.existsSync(file)).toBe(true);
    const { width, height, bytes } = pngSize(file);
    expect(width).toBe(height);
    expect(width).toBeGreaterThanOrEqual(64);
    expect(width).toBeLessThanOrEqual(MAX_SIDE);
    expect(bytes).toBeLessThanOrEqual(MAX_BYTES);
  });

  test("is not the full-size app icon", () => {
    expect(path.resolve(notificationIconPath())).not.toBe(path.resolve(appIconPath("linux")));
  });

  test("ships inside the packaged app", () => {
    const builder = fs.readFileSync(path.join(WEBUI, "electron-builder.yml"), "utf-8");
    expect(builder).toMatch(/assets\/\*\*\/\*/);
  });

  test("every Notification in the app is built with notificationIconPath()", () => {
    const sources = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === "node_modules" || entry.name === "dist" || entry.name === "src") continue;
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.(c|m)?js$/.test(entry.name)) sources.push(full);
      }
    };
    walk(WEBUI);
    const offenders = [];
    let constructions = 0;
    for (const file of sources) {
      const text = fs.readFileSync(file, "utf-8");
      const re = /new Notification\(\{([\s\S]*?)\}\)/g;
      let m;
      while ((m = re.exec(text))) {
        constructions += 1;
        if (!/icon:\s*notificationIconPath\(\)/.test(m[1])) {
          offenders.push(`${path.relative(WEBUI, file)}: ${m[1].trim().split("\n")[0]}`);
        }
      }
    }
    expect(constructions).toBeGreaterThanOrEqual(2);
    expect(offenders).toEqual([]);
  });
});
