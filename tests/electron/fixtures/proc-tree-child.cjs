// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A stand-in child process for the process-tree and agent-lifecycle tests.
 *
 *   --ignore-term     keep running on SIGTERM (only SIGKILL stops it)
 *   --children=N      start N children of this same script (each a grandchild
 *                     of the test), passing --ignore-term down when given
 *   --detach-children  start them detached, so they outlive this process even
 *                     on Windows (where Node otherwise ties children to a job
 *                     that dies with their parent)
 *   --close-stdin     close stdin straight away and keep running, so the
 *                     parent's next write fails with EPIPE (Linux and macOS;
 *                     on Windows the parent's pipe stays writable)
 *   --stdio           answer newline-delimited JSON-RPC on stdin: "ping" gets
 *                     a result, "shutdown" is ignored when --ignore-shutdown
 *                     is given and exits otherwise
 *
 * Prints "PID <pid>" for itself and every descendant on stdout (or stderr
 * in --stdio mode, where stdout carries JSON-RPC), so the test can see the
 * whole tree.
 */

"use strict";

const { spawn } = require("child_process");

const args = process.argv.slice(2);
const has = (flag) => args.includes(flag);
const childCount = Number((args.find((a) => a.startsWith("--children=")) || "--children=0").split("=")[1]);
const stdioMode = has("--stdio");
const report = (line) => (stdioMode ? process.stderr : process.stdout).write(line + "\n");

if (has("--ignore-term")) {
  process.on("SIGTERM", () => report(`IGNORED SIGTERM ${process.pid}`));
}

report(`PID ${process.pid}`);

for (let i = 0; i < childCount; i++) {
  const childArgs = [__filename];
  if (has("--ignore-term")) childArgs.push("--ignore-term");
  const child = spawn(process.execPath, childArgs, {
    stdio: ["ignore", "pipe", "ignore"],
    detached: has("--detach-children"),
    windowsHide: true,
  });
  child.stdout.on("data", (d) => {
    for (const line of String(d).split("\n")) if (line.trim()) report(line.trim());
  });
}

if (has("--close-stdin")) {
  // destroy() alone leaves fd 0 open: Node never closes its stdio fds.
  require("fs").closeSync(0);
} else if (stdioMode) {
  let buf = "";
  process.stdin.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      const msg = JSON.parse(line);
      if (msg.method === "shutdown" && !has("--ignore-shutdown")) process.exit(0);
      if (msg.method === "ping" && msg.id) {
        process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { memoryMB: 1 } }) + "\n");
      }
    }
  });
}

setInterval(() => {}, 1000);
