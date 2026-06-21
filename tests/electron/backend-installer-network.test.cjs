// Tests for the installer network pre-check hardening (issue #1572):
//   - trust the system / pinned CA store so a corporate TLS-inspection proxy
//     doesn't make an online machine look "offline"
//   - honor HTTPS_PROXY/HTTP_PROXY in the probe
//   - classify TLS/cert failures distinctly from real connectivity loss
//
// The loopback TLS/proxy suites need a throwaway server certificate. We do NOT
// commit a private key (GitHub push protection rejects it, rightly), so we
// mint one at runtime with `openssl`. If openssl is unavailable those suites
// skip loudly; the pure-logic suites always run.

const fs = require("fs");
const os = require("os");
const path = require("path");
const net = require("net");
const http = require("http");
const https = require("https");
const { execFileSync } = require("child_process");

const {
  buildCaBundle,
  proxyForHttps,
  classifyNetworkError,
  _checkOneHost,
} = require("../../src/gaia/apps/webui/services/backend-installer.cjs");

function opensslAvailable() {
  try {
    execFileSync("openssl", ["version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}
const HAS_OPENSSL = opensslAvailable();
const describeTls = HAS_OPENSSL ? describe : describe.skip;
if (!HAS_OPENSSL) {
  // eslint-disable-next-line no-console
  console.warn(
    "[backend-installer-network] openssl not found — skipping loopback TLS/proxy suites"
  );
}

let TMP;
let CERT;
let CERT_PATH;
let KEY;
beforeAll(() => {
  TMP = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-tls-"));
  if (HAS_OPENSSL) {
    CERT_PATH = path.join(TMP, "cert.pem");
    const keyPath = path.join(TMP, "key.pem");
    // execFileSync (no shell) passes the /CN=... subject literally on all OSes.
    execFileSync("openssl", [
      "req", "-x509", "-newkey", "rsa:2048", "-nodes",
      "-keyout", keyPath, "-out", CERT_PATH,
      "-days", "1", "-subj", "/CN=localhost",
      "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], { stdio: "ignore" });
    CERT = fs.readFileSync(CERT_PATH, "utf8");
    KEY = fs.readFileSync(keyPath, "utf8");
  }
});
afterAll(() => {
  try { fs.rmSync(TMP, { recursive: true, force: true }); } catch { /* ignore */ }
});

// ── env isolation ──────────────────────────────────────────────────────────
const PROXY_VARS = ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"];
const SAVED = {};
beforeEach(() => {
  for (const v of [...PROXY_VARS, "NODE_EXTRA_CA_CERTS"]) SAVED[v] = process.env[v];
  for (const v of PROXY_VARS) delete process.env[v];
  delete process.env.NODE_EXTRA_CA_CERTS;
});
afterEach(() => {
  for (const [k, val] of Object.entries(SAVED)) {
    if (val === undefined) delete process.env[k];
    else process.env[k] = val;
  }
});

// ── classifyNetworkError ─────────────────────────────────────────────────────
describe("classifyNetworkError", () => {
  test("treats cert/issuer errors as 'tls' (NOT offline) — the #1572 signature", () => {
    expect(classifyNetworkError({ code: "UNABLE_TO_GET_ISSUER_CERT_LOCALLY" })).toBe("tls");
    expect(classifyNetworkError({ code: "SELF_SIGNED_CERT_IN_CHAIN" })).toBe("tls");
    expect(classifyNetworkError({ code: "DEPTH_ZERO_SELF_SIGNED_CERT" })).toBe("tls");
    expect(classifyNetworkError({ code: "ERR_TLS_CERT_ALTNAME_INVALID" })).toBe("tls");
    expect(classifyNetworkError({ code: "CERT_HAS_EXPIRED" })).toBe("tls");
  });

  test("treats DNS/connect failures as 'connectivity'", () => {
    expect(classifyNetworkError({ code: "ENOTFOUND" })).toBe("connectivity");
    expect(classifyNetworkError({ code: "ECONNREFUSED" })).toBe("connectivity");
    expect(classifyNetworkError({ code: "ENETUNREACH" })).toBe("connectivity");
  });

  test("treats timeouts as 'timeout'", () => {
    expect(classifyNetworkError({ code: "ETIMEDOUT" })).toBe("timeout");
    expect(classifyNetworkError({ message: "socket timed out" })).toBe("timeout");
  });
});

// ── proxyForHttps ────────────────────────────────────────────────────────────
describe("proxyForHttps", () => {
  test("returns null when no proxy env var is set", () => {
    expect(proxyForHttps()).toBeNull();
  });

  test("prefers HTTPS_PROXY over HTTP_PROXY", () => {
    process.env.HTTP_PROXY = "http://http-proxy:8080";
    process.env.HTTPS_PROXY = "http://https-proxy:8080";
    expect(proxyForHttps()).toBe("http://https-proxy:8080");
  });

  test("falls back to HTTP_PROXY when HTTPS_PROXY is absent", () => {
    process.env.HTTP_PROXY = "http://http-proxy:8080";
    expect(proxyForHttps()).toBe("http://http-proxy:8080");
  });
});

// ── buildCaBundle ────────────────────────────────────────────────────────────
describe("buildCaBundle", () => {
  test("includes the NODE_EXTRA_CA_CERTS contents alongside the bundled roots", () => {
    // buildCaBundle just reads the file; any PEM-shaped text proves inclusion.
    const fakePem = "-----BEGIN CERTIFICATE-----\nDEADBEEF\n-----END CERTIFICATE-----\n";
    const pemPath = path.join(TMP, "extra.pem");
    fs.writeFileSync(pemPath, fakePem);
    process.env.NODE_EXTRA_CA_CERTS = pemPath;
    const bundle = buildCaBundle();
    expect(Array.isArray(bundle)).toBe(true);
    expect(bundle).toContain(fakePem);
    // The bundled Mozilla roots must still be present (passing `ca` replaces them).
    expect(bundle.length).toBeGreaterThan(1);
  });

  test("does not throw when NODE_EXTRA_CA_CERTS points at a missing file", () => {
    process.env.NODE_EXTRA_CA_CERTS = path.join(TMP, "does-not-exist.pem");
    expect(() => buildCaBundle()).not.toThrow();
  });
});

// ── live loopback probes ─────────────────────────────────────────────────────
describeTls("_checkOneHost over loopback TLS", () => {
  let server;
  let url;

  beforeEach((done) => {
    server = https.createServer({ cert: CERT, key: KEY }, (req, res) => {
      res.writeHead(200);
      res.end("ok");
    });
    server.listen(0, "127.0.0.1", () => {
      url = `https://localhost:${server.address().port}/`;
      done();
    });
  });
  afterEach((done) => {
    server.closeAllConnections?.();
    server.close(() => done());
  });

  test("self-signed cert without trust → classified 'tls' (not offline)", async () => {
    const result = await _checkOneHost(url);
    expect(result.ok).toBe(false);
    expect(result.kind).toBe("tls");
  });

  test("self-signed cert WITH NODE_EXTRA_CA_CERTS → ok (the fix)", async () => {
    process.env.NODE_EXTRA_CA_CERTS = CERT_PATH;
    const result = await _checkOneHost(url);
    expect(result.ok).toBe(true);
    expect(result.status).toBe(200);
  });
});

describeTls("_checkOneHost through an HTTP CONNECT proxy", () => {
  let httpsServer;
  let proxy;
  let targetUrl;
  let tunnelSockets;

  beforeEach((done) => {
    tunnelSockets = [];
    httpsServer = https.createServer({ cert: CERT, key: KEY }, (req, res) => {
      res.writeHead(200);
      res.end("ok");
    });
    httpsServer.listen(0, "127.0.0.1", () => {
      targetUrl = `https://localhost:${httpsServer.address().port}/`;
      // Minimal CONNECT-tunnel proxy.
      proxy = http.createServer();
      proxy.on("connect", (req, clientSocket, head) => {
        const [host, port] = req.url.split(":");
        const upstream = net.connect(Number(port), host, () => {
          clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
          upstream.write(head);
          upstream.pipe(clientSocket);
          clientSocket.pipe(upstream);
        });
        tunnelSockets.push(upstream, clientSocket);
        upstream.on("error", () => clientSocket.destroy());
        clientSocket.on("error", () => upstream.destroy());
      });
      proxy.listen(0, "127.0.0.1", done);
    });
  });
  afterEach((done) => {
    for (const s of tunnelSockets) {
      try { s.destroy(); } catch { /* ignore */ }
    }
    httpsServer.closeAllConnections?.();
    proxy.closeAllConnections?.();
    httpsServer.close(() => proxy.close(() => done()));
  });

  test("HTTPS_PROXY is honored and the tunneled TLS probe succeeds", async () => {
    process.env.HTTPS_PROXY = `http://127.0.0.1:${proxy.address().port}`;
    process.env.NODE_EXTRA_CA_CERTS = CERT_PATH;
    const result = await _checkOneHost(targetUrl);
    expect(result.ok).toBe(true);
    expect(result.status).toBe(200);
  });
});
