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
