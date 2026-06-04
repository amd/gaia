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
const { spawnSync: realSpawnSync } = require("child_process");

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
        { encoding: "utf8" }
      );
      return commandLooksLikeBackend(out && out.stdout ? out.stdout : "");
    }
  } catch {
    // Any probe failure → treat as "not a backend" so we don't kill blindly.
    return false;
  }

  return false;
}

module.exports = { isGaiaBackendProcess, commandLooksLikeBackend };
