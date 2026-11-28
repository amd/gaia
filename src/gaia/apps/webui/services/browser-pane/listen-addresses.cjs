// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The local addresses TCP listeners on one port are bound to, read from the
 * OS: `lsof` on macOS and other Unixes, /proc/net/tcp{,6} on Linux, and
 * `netstat -ano` on Windows. A dev server GAIA starts must listen only on
 * loopback, so nothing else on the network can reach the folder it serves.
 */

"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const LIST_TIMEOUT_MS = 10000;
const PROC_LISTEN_STATE = "0A";

/** True for 127.0.0.0/8 and ::1 (also as an IPv4-mapped address). "*", 0.0.0.0 and :: are every interface. */
function isLoopback(address) {
  const a = String(address).replace(/^\[|\]$/g, "").replace(/%.*$/, "").toLowerCase();
  if (/^127\.\d+\.\d+\.\d+$/.test(a)) return true;
  if (/^::ffff:127\.\d+\.\d+\.\d+$/.test(a)) return true;
  return a === "::1" || a === "0:0:0:0:0:0:0:1";
}

/** "1.2.3.4:80" or "[::1]:80" -> { address, port }; null when there is no port. */
function splitHostPort(text) {
  const cut = text.lastIndexOf(":");
  if (cut <= 0) return null;
  const port = Number(text.slice(cut + 1));
  if (!Number.isInteger(port)) return null;
  return { address: text.slice(0, cut).replace(/^\[|\]$/g, ""), port };
}

/** `lsof -nP -iTCP:<port> -sTCP:LISTEN -Fn` output: one "n<addr>:<port>" line per socket. */
function parseLsof(stdout, port) {
  const found = [];
  for (const line of stdout.split(/\r?\n/)) {
    if (!line.startsWith("n")) continue;
    const hp = splitHostPort(line.slice(1));
    if (hp && hp.port === port) found.push(hp.address);
  }
  return found;
}

/** A /proc/net/tcp address: 32-bit words in host byte order, hex. */
function decodeProcAddress(hex, endianness = os.endianness()) {
  const bytes = [];
  for (let w = 0; w < hex.length; w += 8) {
    const word = hex.slice(w, w + 8).match(/../g).map((b) => parseInt(b, 16));
    bytes.push(...(endianness === "LE" ? word.reverse() : word));
  }
  if (bytes.length === 4) return bytes.join(".");
  if (bytes.every((b) => b === 0)) return "::";
  if (bytes.slice(0, 15).every((b) => b === 0) && bytes[15] === 1) return "::1";
  if (bytes.slice(0, 10).every((b) => b === 0) && bytes[10] === 0xff && bytes[11] === 0xff) {
    return `::ffff:${bytes.slice(12).join(".")}`;
  }
  const groups = [];
  for (let i = 0; i < 16; i += 2) groups.push(((bytes[i] << 8) | bytes[i + 1]).toString(16));
  return groups.join(":");
}

/** The listening sockets on `port` in one /proc/net/tcp or tcp6 table. */
function parseProcNetTcp(text, port, endianness = os.endianness()) {
  const found = [];
  for (const line of text.split(/\r?\n/).slice(1)) {
    const cols = line.trim().split(/\s+/);
    if (cols.length < 4 || cols[3] !== PROC_LISTEN_STATE) continue;
    const [addrHex, portHex] = cols[1].split(":");
    if (parseInt(portHex, 16) !== port) continue;
    found.push(decodeProcAddress(addrHex, endianness));
  }
  return found;
}

/**
 * `netstat -ano` output. The state column is translated on non-English
 * Windows, so a listener is told by its foreign address, which is always
 * 0.0.0.0:0 or [::]:0 for one and never for a connection.
 */
function parseNetstat(stdout, port) {
  const found = [];
  for (const line of stdout.split(/\r?\n/)) {
    const cols = line.trim().split(/\s+/);
    if (cols.length < 4 || cols[0].toUpperCase() !== "TCP") continue;
    if (cols[2] !== "0.0.0.0:0" && cols[2] !== "[::]:0") continue;
    const hp = splitHostPort(cols[1]);
    if (hp && hp.port === port) found.push(hp.address);
  }
  return found;
}

function run(execFile, file, args) {
  return new Promise((resolve, reject) => {
    execFile(file, args, { timeout: LIST_TIMEOUT_MS, windowsHide: true, maxBuffer: 16 * 1024 * 1024 }, (err, stdout) => {
      if (err) reject(Object.assign(err, { stdout: String(stdout || "") }));
      else resolve(String(stdout));
    });
  });
}

/**
 * Every local address something listens on at `port`. Rejects when the OS
 * can't be asked, never answers "none" for a list it could not read.
 * @param {number} port
 * @returns {Promise<string[]>}
 */
async function listeningAddresses(
  port,
  { platform = process.platform, execFile = childProcess.execFile, readFile = fs.promises.readFile, env = process.env } = {}
) {
  if (platform === "linux") {
    const v4 = await readFile("/proc/net/tcp", "utf8");
    let v6 = "";
    try {
      v6 = await readFile("/proc/net/tcp6", "utf8");
    } catch (err) {
      if (err.code !== "ENOENT") throw err;
    }
    return [...parseProcNetTcp(v4, port), ...parseProcNetTcp(v6, port)];
  }
  if (platform === "win32") {
    const root = env.SystemRoot || env.SYSTEMROOT || "C:\\Windows";
    return parseNetstat(await run(execFile, path.win32.join(root, "System32", "netstat.exe"), ["-ano"]), port);
  }
  const lsof = platform === "darwin" ? "/usr/sbin/lsof" : "lsof";
  try {
    return parseLsof(await run(execFile, lsof, ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-Fn"]), port);
  } catch (err) {
    // lsof exits 1 with no output when nothing matches.
    if (err.code === 1 && !err.stdout.trim()) return [];
    throw err;
  }
}

module.exports = { decodeProcAddress, isLoopback, listeningAddresses, parseLsof, parseNetstat, parseProcNetTcp };
