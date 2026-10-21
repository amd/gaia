// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Source-level contract for main.cjs (it cannot be required without
 * Electron): the install-time "Lemonade Server found" question is modal to
 * the progress window and brought to the front, so it can't open hidden
 * behind other windows while the install waits on it.
 */

const fs = require("fs");
const path = require("path");

const main = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
  "utf8"
);

function body(name) {
  const start = main.indexOf(`async function ${name}(`);
  expect(start).toBeGreaterThan(-1);
  return main.slice(start, main.indexOf("\n}\n", start));
}

test("a download's Save dialog is titled, not named after its blob: URL", () => {
  expect(main).toMatch(/session\.on\("will-download"[\s\S]{0,200}setSaveDialogOptions\(\{\s*title: "Save from GAIA"/);
});

test("the Lemonade question is shown with the progress window as its owner", () => {
  expect(main).toMatch(/chooseLemonade: \(found\) => chooseLemonadeInDialog\(found, progress\.window\)/);
  const fn = body("chooseLemonadeInDialog");
  expect(fn).toMatch(/dialog\.showMessageBox\(owner, box\)/);
  expect(fn).toMatch(/owner\.focus\(\)/);
});
