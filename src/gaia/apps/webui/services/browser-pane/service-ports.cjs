// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Loopback ports GAIA's other services listen on, which no pane page may
 * reach: the daemon (its port is in host/instance.json), the local model
 * server, and the MCP bridge and adapters. The app's own backend and the pane
 * bridge are added by main, which knows those ports first-hand.
 */

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

/** Lemonade's default port; LEMONADE_BASE_URL can name another. Python: lemonade_client.DEFAULT_PORT. */
const LEMONADE_DEFAULT_PORT = 13305;
/** MCP bridge, Agent UI MCP, TUI MCP, Telegram and Slack health. Python: gaia/mcp/ports.py. */
const MCP_PORTS = [8765, 8766, 8767, 8768, 8769];
const CACHE_MS = 5000;

/** Where the daemon records its port. Python: gaia/daemon/paths.py host_dir(). */
function daemonInstanceFile(env = process.env, home = os.homedir()) {
  if (env.GAIA_DAEMON_HOME) return path.join(env.GAIA_DAEMON_HOME, "instance.json");
  const gaiaHome = (env.GAIA_HOME || "").trim();
  if (gaiaHome) return path.join(gaiaHome.replace(/^~(?=$|[\\/])/, home), "host", "instance.json");
  return path.join(home, ".gaia", "host", "instance.json");
}

function portOfUrl(raw) {
  try {
    const url = new URL(raw);
    if (url.port) return Number(url.port);
    return url.protocol === "https:" ? 443 : 80;
  } catch {
    return null;
  }
}

/** The ports, read now: a missing or unreadable daemon record just leaves its port out. */
function readServicePorts({ env = process.env, home = os.homedir(), readFile = fs.readFileSync, logger = console } = {}) {
  const ports = new Set([LEMONADE_DEFAULT_PORT, ...MCP_PORTS]);
  const lemonade = env.LEMONADE_BASE_URL ? portOfUrl(env.LEMONADE_BASE_URL) : null;
  if (lemonade) ports.add(lemonade);
  const file = daemonInstanceFile(env, home);
  try {
    const port = Number(JSON.parse(readFile(file, "utf8")).port);
    if (Number.isInteger(port) && port > 0 && port < 65536) ports.add(port);
  } catch (err) {
    if (err.code !== "ENOENT") logger.error(`[browser-pane] Could not read the daemon's port from ${file}: ${err.message}`);
  }
  return [...ports];
}

/** readServicePorts, cached for a few seconds: the request filter asks on every request. */
function servicePorts(opts = {}) {
  const now = Date.now();
  const cache = servicePorts.cache;
  if (cache && now - cache.at < CACHE_MS) return cache.ports;
  const ports = readServicePorts(opts);
  servicePorts.cache = { at: now, ports };
  return ports;
}

module.exports = { LEMONADE_DEFAULT_PORT, MCP_PORTS, daemonInstanceFile, readServicePorts, servicePorts };
