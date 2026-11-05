// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A real Electron main process that quits through the app's quit guard, with
// work running and a question nobody answers, as under a logout or `kill`.
// Driven by tests/electron/sigterm-electron.test.mjs.

const path = require("path");
const { EventEmitter } = require("events");
const { app, BrowserWindow } = require("electron");

const guardPath = process.env.GAIA_QUIT_GUARD || path.join(__dirname, "..", "..", "..", "src", "gaia", "apps", "webui", "services", "quit-guard.cjs");
const { installQuitGuard, quitOnTermSignal } = require(guardPath);

const guard = installQuitGuard({
  app,
  updater: new EventEmitter(),
  shouldAsk: () => true,
  confirm: () => {
    console.log("FIXTURE asked whether to quit");
    return new Promise(() => {});
  },
  onQuitting: () => console.log("FIXTURE quitting"),
  onError: (err) => console.log(`FIXTURE quit check failed: ${err.message}`),
});

const armed = quitOnTermSignal({ proc: process, app, guard });

app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  new BrowserWindow({ show: false });
  await armed;
  console.log("FIXTURE ready");
});
