// Tests for the Windows file-lock detection that produces an actionable
// upgrade-failure message (issue #1388).

const {
  isFileLockedError,
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
