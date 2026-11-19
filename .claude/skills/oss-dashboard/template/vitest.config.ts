import { defineConfig } from "vitest/config";

// Handlers use only Web-standard globals and D1 is real SQLite (test/d1.ts), so plain
// Node runs the tests. The threads pool avoids a tinypool IPC crash on some Node builds.
// The two suites are defined in vitest.workspace.ts.
export default defineConfig({
  test: { environment: "node", pool: "threads" },
});
