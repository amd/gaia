// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A tiny dev server for the browser pane's dev-server tests. Node only, no
// packages. `--port N` sets the port, `--crash` fails on boot, and a syntax
// error saved into src/app.js is logged as a build error, like a bundler would.

"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const vm = require("vm");

const args = process.argv.slice(2);
const port = Number(args[args.indexOf("--port") + 1] || process.env.PORT || 41730);
const source = path.join(__dirname, "src", "app.js");

console.log(`> fixture-dev-server starting (node ${process.version})`);

if (args.includes("--crash")) {
  console.log("> loading config");
  console.error("Error: Cannot find module './missing-config'");
  console.error("    at Module._resolveFilename (node:internal/modules/cjs/loader:1145:15)");
  console.error("    at Object.<anonymous> (server.js:1:1)");
  process.exit(1);
}

let page = "<h1>Not built yet</h1>";

function build() {
  const code = fs.readFileSync(source, "utf8");
  try {
    const mod = { exports: "" };
    vm.runInNewContext(code, { module: mod }, { filename: "src/app.js" });
    page = String(mod.exports);
    console.log("[build] compiled src/app.js");
  } catch (err) {
    console.error(`[build] ERROR in src/app.js: ${err.name}: ${err.message}`);
  }
}

build();
fs.watchFile(source, { interval: 200 }, build);

http
  .createServer((req, res) => {
    console.log(`${req.method} ${req.url}`);
    res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
    res.end(`<!doctype html><title>Fixture dev server</title>${page}`);
  })
  .listen(port, "localhost", () => console.log(`> ready on http://localhost:${port}/`));

process.on("SIGTERM", () => process.exit(0));
