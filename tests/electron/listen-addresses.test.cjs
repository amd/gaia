// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Reading which addresses a dev server's port is bound to, from each OS's own
 * listing. The listings are canned, so every platform's parser runs on any OS.
 */

const path = require("path");

const PANE = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "services", "browser-pane");
const { decodeProcAddress, isLoopback, listeningAddresses, parseLsof, parseNetstat, parseProcNetTcp } = require(path.join(PANE, "listen-addresses.cjs"));

const execFileReturning = (stdout, err = null) =>
  jest.fn((file, args, opts, cb) => {
    cb(err, stdout);
  });

describe("isLoopback", () => {
  test.each(["127.0.0.1", "127.1.2.3", "::1", "[::1]", "::1%lo0", "::ffff:127.0.0.1", "0:0:0:0:0:0:0:1"])("%s is loopback", (a) => {
    expect(isLoopback(a)).toBe(true);
  });

  test.each(["*", "0.0.0.0", "::", "[::]", "192.168.1.5", "10.0.0.2", "fe80::1", "::ffff:192.168.1.5", ""])("%s is not", (a) => {
    expect(isLoopback(a)).toBe(false);
  });
});

describe("macOS and other Unixes: lsof", () => {
  const LSOF = ["p4242", "f5", "n*:9876", "f6", "n127.0.0.1:9876", "f7", "n[::1]:9876", "f8", "n192.168.1.5:19876"].join("\n");

  test("reads the address of every listener on the port, and only that port", () => {
    expect(parseLsof(LSOF, 9876)).toEqual(["*", "127.0.0.1", "::1"]);
  });

  test("asks /usr/sbin/lsof on macOS for listening TCP sockets on the port", async () => {
    const execFile = execFileReturning("p1\nf3\nn127.0.0.1:5173\n");
    await expect(listeningAddresses(5173, { platform: "darwin", execFile })).resolves.toEqual(["127.0.0.1"]);
    expect(execFile.mock.calls[0][0]).toBe("/usr/sbin/lsof");
    expect(execFile.mock.calls[0][1]).toEqual(["-nP", "-iTCP:5173", "-sTCP:LISTEN", "-Fn"]);
  });

  test("lsof's exit 1 with no output means nothing listens", async () => {
    const execFile = execFileReturning("", Object.assign(new Error("Command failed"), { code: 1 }));
    await expect(listeningAddresses(5173, { platform: "darwin", execFile })).resolves.toEqual([]);
  });

  test("any other lsof failure is an error, never an empty list", async () => {
    const execFile = execFileReturning("", Object.assign(new Error("spawn /usr/sbin/lsof ENOENT"), { code: "ENOENT" }));
    await expect(listeningAddresses(5173, { platform: "darwin", execFile })).rejects.toThrow(/ENOENT/);
  });
});

describe("Linux: /proc/net/tcp", () => {
  const HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n";
  // 9876 is 0x2694. Listening (0A) on 0.0.0.0 and 127.0.0.1; an established (01) socket on the port is no listener.
  const TCP4 =
    HEAD +
    "   0: 00000000:2694 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1 1 0 100 0 0 10 0\n" +
    "   1: 0100007F:2694 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 2 1 0 100 0 0 10 0\n" +
    "   2: 0101A8C0:2694 0201A8C0:C350 01 00000000:00000000 00:00000000 00000000  1000        0 3 1 0 100 0 0 10 0\n" +
    "   3: 0101A8C0:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 4 1 0 100 0 0 10 0\n";
  const TCP6 =
    HEAD +
    "   0: 00000000000000000000000001000000:2694 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000 0 5 1 0 100 0 0 10 0\n" +
    "   1: 00000000000000000000000000000000:2694 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000 0 6 1 0 100 0 0 10 0\n";

  test("decodes little-endian words, IPv4 and IPv6", () => {
    expect(decodeProcAddress("0100007F", "LE")).toBe("127.0.0.1");
    expect(decodeProcAddress("7F000001", "BE")).toBe("127.0.0.1");
    expect(decodeProcAddress("0101A8C0", "LE")).toBe("192.168.1.1");
    expect(decodeProcAddress("00000000000000000000000001000000", "LE")).toBe("::1");
    expect(decodeProcAddress("00000000000000000000000000000000", "LE")).toBe("::");
    expect(decodeProcAddress("0000000000000000FFFF00000100007F", "LE")).toBe("::ffff:127.0.0.1");
  });

  test("lists only sockets in LISTEN on the port", () => {
    expect(parseProcNetTcp(TCP4, 9876, "LE")).toEqual(["0.0.0.0", "127.0.0.1"]);
    expect(parseProcNetTcp(TCP6, 9876, "LE")).toEqual(["::1", "::"]);
  });

  test("reads both tables, and a machine with no IPv6 table is fine", async () => {
    const files = { "/proc/net/tcp": TCP4, "/proc/net/tcp6": TCP6 };
    const readFile = jest.fn(async (f) => {
      if (f in files) return files[f];
      throw Object.assign(new Error(`ENOENT: ${f}`), { code: "ENOENT" });
    });
    const addrs = await listeningAddresses(9876, { platform: "linux", readFile });
    expect(addrs).toHaveLength(4);
    delete files["/proc/net/tcp6"];
    expect(await listeningAddresses(9876, { platform: "linux", readFile })).toHaveLength(2);
  });

  test("an unreadable IPv4 table is an error", async () => {
    const readFile = jest.fn(async () => {
      throw Object.assign(new Error("EACCES"), { code: "EACCES" });
    });
    await expect(listeningAddresses(9876, { platform: "linux", readFile })).rejects.toThrow(/EACCES/);
  });
});

describe("Windows: netstat -ano", () => {
  // The state column is translated on a German Windows; listeners are told by their foreign address.
  const NETSTAT = [
    "",
    "Aktive Verbindungen",
    "",
    "  Proto  Lokale Adresse         Remoteadresse          Status           PID",
    "  TCP    0.0.0.0:9876           0.0.0.0:0              ABHÖREN          4242",
    "  TCP    127.0.0.1:9876         127.0.0.1:50000        HERGESTELLT      4242",
    "  TCP    [::1]:9876             [::]:0                 ABHÖREN          4242",
    "  TCP    192.168.1.5:19876      0.0.0.0:0              ABHÖREN          99",
    "  UDP    0.0.0.0:9876           *:*                                     7",
  ].join("\r\n");

  test("lists TCP listeners on the port, whatever the language", () => {
    expect(parseNetstat(NETSTAT, 9876)).toEqual(["0.0.0.0", "::1"]);
  });

  test("runs netstat from System32", async () => {
    const execFile = execFileReturning(NETSTAT);
    await expect(listeningAddresses(9876, { platform: "win32", execFile, env: { SystemRoot: "C:\\Windows" } })).resolves.toEqual(["0.0.0.0", "::1"]);
    expect(execFile.mock.calls[0][0]).toBe("C:\\Windows\\System32\\netstat.exe");
    expect(execFile.mock.calls[0][1]).toEqual(["-ano"]);
  });
});
