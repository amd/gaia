import { defineConfig } from "vitest/config";

// The Worker handlers depend only on Web-standard globals (Request, Response,
// FormData, crypto.subtle) that Node 18+ and Vitest's default environment
// already provide, so tests run in plain Node against an in-memory R2 fake
// (see test/fake-r2.ts). No Miniflare/wrangler runtime is required for the
// unit suite — `wrangler dev` is documented separately for end-to-end checks.
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
    // The default forks pool crashes on some Node 20 builds (tinypool IPC
    // ERR_INVALID_ARG_TYPE deserializing worker messages). The suite needs no
    // process-level isolation, so run it in worker threads instead.
    pool: "threads",
  },
});
