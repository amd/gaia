// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** GAIA's other local services are out of the browser pane's reach, wherever they listen. */

const path = require("path");

const PANE = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "services", "browser-pane");
const { LEMONADE_DEFAULT_PORT, MCP_PORTS, daemonInstanceFile, readServicePorts } = require(path.join(PANE, "service-ports.cjs"));
const { classifyNavigation } = require(path.join(PANE, "navigation-policy.cjs"));

const quiet = { error: jest.fn() };
const noDaemon = () => {
  const err = new Error("missing");
  err.code = "ENOENT";
  throw err;
};

describe("GAIA's other local services", () => {
  test("Lemonade, the MCP ports and the daemon's recorded port are all out of reach", () => {
    const ports = readServicePorts({ env: {}, home: "/home/ada", readFile: () => JSON.stringify({ port: 51234 }), logger: quiet });
    expect(ports).toEqual(expect.arrayContaining([LEMONADE_DEFAULT_PORT, ...MCP_PORTS, 51234]));
    for (const port of ports) {
      for (const host of ["127.0.0.1", "localhost", "[::1]", "0.0.0.0", "127.1", "[::ffff:127.0.0.1]"]) {
        expect(classifyNavigation(`http://${host}:${port}/`, { blockedPorts: ports }).allowed).toBe(false);
      }
    }
  });

  test("a Lemonade on another port, named by LEMONADE_BASE_URL, is out of reach too", () => {
    const ports = readServicePorts({ env: { LEMONADE_BASE_URL: "http://127.0.0.1:9100/api/v1" }, readFile: noDaemon, logger: quiet });
    expect(ports).toContain(9100);
  });

  test("no daemon record means no daemon port, and nothing is logged", () => {
    const logger = { error: jest.fn() };
    const ports = readServicePorts({ env: {}, readFile: noDaemon, logger });
    expect(ports.sort()).toEqual([LEMONADE_DEFAULT_PORT, ...MCP_PORTS].sort());
    expect(logger.error).not.toHaveBeenCalled();
  });

  test.each([["not json"], [JSON.stringify({ port: "abc" })], [JSON.stringify({ port: 70000 })]])("a damaged daemon record %s adds no port", (text) => {
    const ports = readServicePorts({ env: {}, readFile: () => text, logger: quiet });
    expect(ports.sort()).toEqual([LEMONADE_DEFAULT_PORT, ...MCP_PORTS].sort());
  });

  test("the daemon record is found where Python's daemon writes it", () => {
    expect(daemonInstanceFile({}, "/home/ada")).toBe(path.join("/home/ada", ".gaia", "host", "instance.json"));
    expect(daemonInstanceFile({ GAIA_HOME: "/srv/gaia" }, "/home/ada")).toBe(path.join("/srv/gaia", "host", "instance.json"));
    expect(daemonInstanceFile({ GAIA_DAEMON_HOME: "/tmp/d" }, "/home/ada")).toBe(path.join("/tmp/d", "instance.json"));
  });

  test("a site elsewhere on those port numbers still opens", () => {
    expect(classifyNavigation(`https://example.com:${LEMONADE_DEFAULT_PORT}/`, { blockedPorts: [LEMONADE_DEFAULT_PORT] }).allowed).toBe(true);
  });
});

describe("a name that resolves to this computer", () => {
  const { isLoopbackAddress, reachesAppService } = require(path.join(PANE, "navigation-policy.cjs"));
  const resolvesTo = (...addresses) => jest.fn(async () => addresses.map((address) => ({ address })));

  test.each([
    ["127.0.0.1", true],
    ["127.8.0.1", true],
    ["0.0.0.0", true],
    ["::1", true],
    ["::", true],
    ["::ffff:127.0.0.1", true],
    ["::ffff:7f00:2", true],
    ["10.0.0.1", false],
    ["::ffff:8.8.8.8", false],
    ["2001:db8::1", false],
  ])("%s is on this computer: %s", (address, expected) => {
    expect(isLoopbackAddress(address)).toBe(expected);
  });

  test("a DNS name pointing at loopback can't reach GAIA's ports", async () => {
    const lookup = resolvesTo("93.184.216.34", "127.0.0.1");
    await expect(reachesAppService(new URL("http://rebind.example:13305/"), [13305], lookup)).resolves.toBe(true);
    expect(lookup).toHaveBeenCalledWith("rebind.example");
  });

  test("a name on the internet, or one that doesn't resolve, is not GAIA's", async () => {
    await expect(reachesAppService(new URL("http://site.example:13305/"), [13305], resolvesTo("93.184.216.34"))).resolves.toBe(false);
    await expect(reachesAppService(new URL("http://gone.example:13305/"), [13305], jest.fn(async () => { throw new Error("ENOTFOUND"); }))).resolves.toBe(false);
  });

  test("only requests to GAIA's own ports are looked up", async () => {
    const lookup = resolvesTo("127.0.0.1");
    await expect(reachesAppService(new URL("http://rebind.example:5173/"), [13305], lookup)).resolves.toBe(false);
    expect(lookup).not.toHaveBeenCalled();
  });

  test("the pane's request filter cancels a request to such a name", async () => {
    const { installSessionPolicy } = require(path.join(PANE, "navigation-policy.cjs"));
    const { EventEmitter } = require("events");
    const dns = require("dns");
    const lookup = jest.spyOn(dns.promises, "lookup").mockResolvedValue([{ address: "127.0.0.1", family: 4 }]);
    const ses = new EventEmitter();
    ses.setPermissionRequestHandler = jest.fn();
    ses.setPermissionCheckHandler = jest.fn();
    ses.setDevicePermissionHandler = jest.fn();
    ses.webRequest = { onBeforeRequest: jest.fn((h) => (ses.beforeRequest = h)) };
    installSessionPolicy(ses, { blockedPorts: () => [13305], onPermissionDenied: jest.fn(), downloadsDir: () => "/dl" });
    const answer = await new Promise((resolve) => ses.beforeRequest({ url: "http://rebind.example:13305/api" }, resolve));
    expect(answer).toEqual({ cancel: true });
    lookup.mockRestore();
  });
});
