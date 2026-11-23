// Identify orphaned GAIA backend processes left over from a previous launch.
//
// On Windows an orphaned `gaia chat --ui` process keeps an open handle on
// `gaia.exe`, which makes the next `uv pip install --refresh` upgrade fail with
// `os error 32` (file in use) — issue #1388. We must positively identify and
// kill that process BEFORE the installer runs, so the executable can be
// replaced.
//
// The check is deliberately conservative: it returns false whenever it cannot
// positively confirm the process is a GAIA backend, so we never signal an
// unrelated user process (issue #782 TOCTOU mitigation).

const realFs = require("fs");
const path = require("path");
const { spawnSync: realSpawnSync } = require("child_process");

// One record per running app, named for the app's pid: two copies of the app
// sharing ~/.gaia (an installed app and a dev build) must not see each other's
// live backend as an orphan (issue #7062).
const PIDFILE_DIR = "backend-pids";
// Written by versions before per-app records; read only to clean up after them.
const LEGACY_PIDFILE = "backend.pid";

// Legitimate backend invocation is `gaia chat --ui --ui-port <port>`. Match on
// "gaia" plus one of the backend-specific flags so unrelated Python/Node
// processes (Jupyter, LSP, etc.) are never matched.
function commandLooksLikeBackend(cmd) {
  if (!cmd) return false;
  return cmd.includes("gaia") && (cmd.includes("chat") || cmd.includes("--ui-port"));
}

/**
 * Best-effort check that `pid` belongs to a GAIA backend process.
 *
 * @param {number} pid
 * @param {object} [deps] injectable dependencies for testing
 * @param {string} [deps.platform] process.platform override
 * @param {object} [deps.fs] fs module override
 * @param {Function} [deps.spawnSync] child_process.spawnSync override
 * @returns {boolean}
 */
function isGaiaBackendProcess(pid, deps = {}) {
  const fs = deps.fs || realFs;
  const spawnSync = deps.spawnSync || realSpawnSync;
  const platform = deps.platform || process.platform;

  // Defense-in-depth: pid is interpolated into shell/CIM-filter args below.
  // Production callers parseInt it, but reject anything non-integer here so a
  // future caller can't inject through the PowerShell -Filter / ps -p args.
  if (!Number.isInteger(pid)) return false;

  try {
    if (platform === "linux") {
      const procCmd = `/proc/${pid}/cmdline`;
      if (!fs.existsSync(procCmd)) return false;
      // /proc/<pid>/cmdline is NUL-separated.
      const raw = fs.readFileSync(procCmd, "utf8");
      return commandLooksLikeBackend(raw);
    }

    if (platform === "darwin") {
      const out = spawnSync("ps", ["-p", String(pid), "-o", "command="], {
        encoding: "utf8",
        timeout: 5000,
      });
      return commandLooksLikeBackend(out && out.stdout ? out.stdout : "");
    }

    if (platform === "win32") {
      const out = spawnSync(
        "powershell.exe",
        [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`,
        ],
        // timeout: a hung PowerShell on the recovery path must not block app startup.
        { encoding: "utf8", timeout: 5000 }
      );
      return commandLooksLikeBackend(out && out.stdout ? out.stdout : "");
    }
  } catch {
    // Any probe failure → treat as "not a backend" so we don't kill blindly.
    return false;
  }

  return false;
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // EPERM: it exists but belongs to someone else.
    return err.code === "EPERM";
  }
}

function backendRecordPath(gaiaDir, ownerPid) {
  return path.join(gaiaDir, PIDFILE_DIR, `${ownerPid}.pid`);
}

/** Record that app `ownerPid` started backend `backendPid`. Returns the file written. */
function recordBackend({ gaiaDir, ownerPid, backendPid, fs = realFs }) {
  const file = backendRecordPath(gaiaDir, ownerPid);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, String(backendPid), { mode: 0o600 });
  return file;
}

/** Remove app `ownerPid`'s record, but only while it still names `backendPid`. */
function forgetBackend({ gaiaDir, ownerPid, backendPid, fs = realFs }) {
  const file = backendRecordPath(gaiaDir, ownerPid);
  let recorded;
  try {
    recorded = parseInt(fs.readFileSync(file, "utf8").trim(), 10);
  } catch (err) {
    if (err.code === "ENOENT") return false;
    throw err;
  }
  if (recorded !== backendPid) return false;
  fs.unlinkSync(file);
  return true;
}

/**
 * Stop every recorded backend whose app is gone, and delete its record.
 * A backend whose app is still running is left alone, record and all: it
 * belongs to another copy of the app sharing this ~/.gaia.
 *
 * @returns {Promise<{ killed: number[], kept: number[] }>} backend pids
 */
async function cleanupOrphanedBackends({
  gaiaDir,
  selfPid = process.pid,
  killBackend,
  isBackend = isGaiaBackendProcess,
  isAlive = pidAlive,
  fs = realFs,
  logger = console,
}) {
  const killed = [];
  const kept = [];

  async function stopIfBackend(backendPid, source) {
    let confirmed = false;
    try {
      confirmed = isBackend(backendPid);
    } catch (err) {
      logger.warn(`[main] Could not verify pid ${backendPid} from ${source}: ${err.message}`);
    }
    if (!confirmed) {
      logger.log(`[main] PID ${backendPid} from ${source} is not a GAIA backend; skipping kill`);
      return;
    }
    try {
      await killBackend(backendPid);
      killed.push(backendPid);
      logger.log(`[main] Stopped orphaned backend pid ${backendPid} (from ${source})`);
    } catch (err) {
      logger.warn(`[main] Could not stop orphaned backend pid ${backendPid}: ${err.message}`);
    }
  }

  function readPid(file) {
    return parseInt(fs.readFileSync(file, "utf8").trim(), 10);
  }

  function remove(file) {
    try {
      fs.unlinkSync(file);
    } catch (err) {
      if (err.code !== "ENOENT") logger.warn(`[main] Could not remove ${file}: ${err.message}`);
    }
  }

  const legacy = path.join(gaiaDir, LEGACY_PIDFILE);
  if (fs.existsSync(legacy)) {
    try {
      const backendPid = readPid(legacy);
      if (!Number.isNaN(backendPid)) await stopIfBackend(backendPid, LEGACY_PIDFILE);
    } catch (err) {
      logger.warn(`[main] Failed reading ${legacy}: ${err.message}`);
    }
    remove(legacy);
  }

  const dir = path.join(gaiaDir, PIDFILE_DIR);
  let names = [];
  try {
    names = fs.readdirSync(dir);
  } catch (err) {
    if (err.code !== "ENOENT") logger.warn(`[main] Could not list ${dir}: ${err.message}`);
  }
  for (const name of names) {
    const m = /^(\d+)\.pid$/.exec(name);
    if (!m) continue;
    const ownerPid = Number(m[1]);
    const file = path.join(dir, name);
    let backendPid;
    try {
      backendPid = readPid(file);
    } catch (err) {
      logger.warn(`[main] Failed reading ${file}: ${err.message}`);
      continue;
    }
    if (ownerPid !== selfPid && isAlive(ownerPid)) {
      // A record whose backend is gone is stale, even if its app's pid is now
      // someone else's; it can never protect a live backend, so drop it.
      let stale = false;
      if (!Number.isNaN(backendPid)) {
        try {
          stale = !isBackend(backendPid);
        } catch (err) {
          logger.warn(`[main] Could not verify pid ${backendPid} from ${PIDFILE_DIR}/${name}: ${err.message}`);
        }
      }
      if (stale) {
        logger.log(`[main] Backend pid ${backendPid} of app pid ${ownerPid} is gone; removing its record`);
        remove(file);
        continue;
      }
      if (!Number.isNaN(backendPid)) kept.push(backendPid);
      logger.log(`[main] Backend pid ${backendPid} belongs to running GAIA app pid ${ownerPid}; leaving it`);
      continue;
    }
    if (!Number.isNaN(backendPid)) await stopIfBackend(backendPid, `${PIDFILE_DIR}/${name}`);
    remove(file);
  }
  return { killed, kept };
}

module.exports = {
  isGaiaBackendProcess,
  commandLooksLikeBackend,
  cleanupOrphanedBackends,
  recordBackend,
  forgetBackend,
  backendRecordPath,
  PIDFILE_DIR,
  LEGACY_PIDFILE,
};
