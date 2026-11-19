import { fileURLToPath } from "node:url";
import { defineWorkspace } from "vitest/config";

const PROJECT_TESTS = ["test/assets.test.ts", "test/tags.test.ts", "test/config.test.ts"];

// "template" tests the dashboard's logic against test/example-config.ts, so it passes
// whatever a project puts in src/config.ts. "project" tests this project's own settings
// and data (its release files and tags) against the real src/config.ts.
export default defineWorkspace([
  {
    extends: "./vitest.config.ts",
    test: { name: "template", include: ["test/**/*.test.ts"], exclude: PROJECT_TESTS },
    resolve: {
      alias: [{ find: /^\.\.?\/(src\/)?config$/, replacement: fileURLToPath(new URL("./test/example-config.ts", import.meta.url)) }],
    },
  },
  { extends: "./vitest.config.ts", test: { name: "project", include: PROJECT_TESTS } },
]);
