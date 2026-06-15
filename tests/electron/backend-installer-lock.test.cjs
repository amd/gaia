// Tests for the Windows file-lock detection that produces an actionable
// upgrade-failure message (issue #1388).

const {
  isFileLockedError,
  isTransientNetworkError,
} = require("../../src/gaia/apps/webui/services/backend-installer.cjs");

describe("isFileLockedError", () => {
  test("detects the uv os-error-32 signature from issue #1388", () => {
    const out =
      "error: failed to remove file `C:\\Users\\x\\.gaia\\venv\\Lib\\site-packages\\../../Scripts/gaia.exe`: " +
      "The process cannot access the file because it is being used by another process. (os error 32)";
    expect(isFileLockedError(out)).toBe(true);
  });

  test("detects the plain 'being used by another process' phrasing", () => {
    expect(
      isFileLockedError("The file is being used by another process.")
    ).toBe(true);
  });

  test("ignores unrelated pip failures", () => {
    expect(
      isFileLockedError("error: No solution found when resolving dependencies")
    ).toBe(false);
    expect(isFileLockedError("")).toBe(false);
    expect(isFileLockedError(undefined)).toBe(false);
  });
});

describe("isTransientNetworkError", () => {
  test("detects the broken-pipe signature that failed release v0.20.1", () => {
    const out = [
      "error: Failed to fetch: `https://pypi.org/simple/scipy/`",
      "  Caused by: error sending request for url (https://pypi.org/simple/scipy/)",
      "  Caused by: client error (SendRequest)",
      "  Caused by: connection error",
      "  Caused by: stream closed because of a broken pipe",
    ].join("\n");
    expect(isTransientNetworkError(out)).toBe(true);
  });

  test("detects common transient network phrasings", () => {
    [
      "error sending request for url",
      "connection reset by peer",
      "connection refused",
      "Could not connect to server",
      "operation timed out",
      "request timeout",
      "Temporary failure in name resolution",
      "failed to lookup address information",
      "network is unreachable",
    ].forEach((phrase) => {
      expect(isTransientNetworkError(phrase)).toBe(true);
    });
  });

  test("does NOT retry dependency-resolution or file-lock failures", () => {
    // No solution found / version conflicts are deterministic — retrying is futile.
    expect(
      isTransientNetworkError(
        "error: No solution found when resolving dependencies"
      )
    ).toBe(false);
    // File-lock (os error 32) is a distinct, user-actionable failure.
    expect(
      isTransientNetworkError(
        "The process cannot access the file because it is being used by another process. (os error 32)"
      )
    ).toBe(false);
    expect(isTransientNetworkError("")).toBe(false);
    expect(isTransientNetworkError(undefined)).toBe(false);
  });
});
