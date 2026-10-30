// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import type { AgentManifest, CatalogIndex } from "../src/types";
import {
  makeEnv,
  publishByRefRequest,
  publishRequest,
  sampleManifest,
  sha256Of,
} from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

async function publish(
  env: Env,
  opts: {
    id?: string;
    version: string;
    channel?: string;
    filename?: string;
    description?: string;
    author?: string;
    token?: string;
    readme?: string;
  }
) {
  const id = opts.id ?? "gaia";
  return worker.fetch(
    publishRequest({
      token: opts.token ?? "tok_amd",
      manifestYaml: sampleManifest({
        id,
        version: opts.version,
        author: opts.author,
        description: opts.description,
      }),
      artifact: `${id}-${opts.version}-bytes`,
      filename: opts.filename ?? `${id}-${opts.version}-linux-x64`,
      channel: opts.channel,
      readme: opts.readme,
    }),
    env as never
  );
}

async function get(env: Env, path: string): Promise<Response> {
  return worker.fetch(new Request(`https://hub.amd-gaia.ai${path}`), env as never);
}

async function manifestOf(env: Env, id: string): Promise<AgentManifest> {
  return (await (await get(env, `/agents/${id}/manifest.json`)).json()) as AgentManifest;
}

async function indexOf(env: Env): Promise<CatalogIndex> {
  return (await (await get(env, "/index.json")).json()) as CatalogIndex;
}

describe("channels — an RC never becomes latest_version", () => {
  it("publishes an RC to next and leaves latest_version on stable", async () => {
    const env = makeEnv();
    expect((await publish(env, { version: "0.25.2" })).status).toBe(201);
    const res = await publish(env, {
      version: "0.26.0-rc.1",
      channel: "next",
      description: '"RC-only description"',
    });
    expect(res.status).toBe(201);
    const body = (await res.json()) as any;
    expect(body.published.channel).toBe("next");
    expect(body.published.latest_version).toBe("0.25.2");
    expect(body.published.channels).toEqual({ stable: "0.25.2", next: "0.26.0-rc.1" });

    const m = await manifestOf(env, "gaia");
    expect(m.latest_version).toBe("0.25.2");
    // A client that installs the newest entry of `versions` must not see the RC.
    expect(Object.keys(m.versions)).toEqual(["0.25.2"]);
    expect(Object.keys(m.next_versions!)).toEqual(["0.26.0-rc.1"]);
    // Display metadata stays the stable release's.
    expect(m.description).toBe("General conversation agent");

    const entry = (await indexOf(env)).agents.find((a) => a.id === "gaia")!;
    expect(entry.latest_version).toBe("0.25.2");
    expect(entry.channels).toEqual({ stable: "0.25.2", next: "0.26.0-rc.1" });
    expect(Object.keys(entry.min_gaia_versions!)).toEqual(["0.25.2"]);
    expect(entry.description).toBe("General conversation agent");
  });

  it("refuses a pre-release with no channel (the old default is stable)", async () => {
    const env = makeEnv();
    const res = await publish(env, { version: "0.26.0-rc.1" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("prerelease_on_stable");
    expect(env.bucket.keys()).toEqual([]);
  });

  it("refuses a pre-release sent to stable explicitly", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.25.2" });
    const res = await publish(env, { version: "0.26.0-rc.1", channel: "stable" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("prerelease_on_stable");
    expect((await manifestOf(env, "gaia")).latest_version).toBe("0.25.2");
  });

  it("refuses a pre-release by reference on stable too", async () => {
    const env = makeEnv();
    const bytes = new TextEncoder().encode("big-rc-installer");
    const sha256 = await sha256Of(bytes);
    await env.bucket.put("agents/gaia/0.26.0-rc.1/gaia-setup.exe", bytes, { sha256 });
    const res = await worker.fetch(
      publishByRefRequest({
        manifestYaml: sampleManifest({ id: "gaia", version: "0.26.0-rc.1" }),
        filename: "gaia-setup.exe",
        sha256,
        size: bytes.byteLength,
      }),
      env as never
    );
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("prerelease_on_stable");
    expect(await env.bucket.get("agents/gaia/manifest.json")).toBeNull();
  });

  it("keeps a next-only package out of index.json until it has a stable version", async () => {
    const env = makeEnv();
    await publish(env, { id: "fresh", version: "1.0.0-rc.1", channel: "next" });
    const m = await manifestOf(env, "fresh");
    expect(m.latest_version).toBe("");
    expect(m.channels).toEqual({ stable: "", next: "1.0.0-rc.1" });
    expect(m.versions).toEqual({});
    expect((await indexOf(env)).agents.map((a) => a.id)).not.toContain("fresh");

    await publish(env, { id: "fresh", version: "1.0.0" });
    const entry = (await indexOf(env)).agents.find((a) => a.id === "fresh")!;
    expect(entry.latest_version).toBe("1.0.0");
    expect(entry.channels).toEqual({ stable: "1.0.0", next: "1.0.0" });
  });
});

describe("channels — a stable publish updates both", () => {
  it("moves stable and next to a final newer than the RC", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.25.2" });
    await publish(env, { version: "0.26.0-rc.2", channel: "next" });
    const res = await publish(env, { version: "0.26.0", description: '"Final"' });
    expect(res.status).toBe(201);
    expect(((await res.json()) as any).published.channels).toEqual({
      stable: "0.26.0",
      next: "0.26.0",
    });

    const m = await manifestOf(env, "gaia");
    expect(m.latest_version).toBe("0.26.0");
    expect(m.channels).toEqual({ stable: "0.26.0", next: "0.26.0" });
    expect(m.description).toBe("Final");
    // The superseded RC stays downloadable on next.
    expect(Object.keys(m.next_versions!)).toEqual(["0.26.0-rc.2"]);
    const entry = (await indexOf(env)).agents.find((a) => a.id === "gaia")!;
    expect(entry.channels).toEqual({ stable: "0.26.0", next: "0.26.0" });
  });

  it("leaves next on a newer RC when stable gets an older patch", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.25.2" });
    await publish(env, { version: "0.26.0-rc.1", channel: "next" });
    await publish(env, { version: "0.25.3" });
    expect((await manifestOf(env, "gaia")).channels).toEqual({
      stable: "0.25.3",
      next: "0.26.0-rc.1",
    });
  });
});

describe("channels — round-trip", () => {
  it("reports the same channels in the publish response, manifest, and index", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.25.2", channel: "stable" });
    const res = await publish(env, { version: "0.26.0-rc.1", channel: "next" });
    const fromPublish = ((await res.json()) as any).published.channels;
    const fromManifest = (await manifestOf(env, "gaia")).channels;
    const fromIndex = (await indexOf(env)).agents.find((a) => a.id === "gaia")!.channels;
    expect(fromPublish).toEqual({ stable: "0.25.2", next: "0.26.0-rc.1" });
    expect(fromManifest).toEqual(fromPublish);
    expect(fromIndex).toEqual(fromPublish);
  });

  it("serves an RC's artifact from its version path", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.26.0-rc.1", channel: "next" });
    const res = await get(env, "/agents/gaia/0.26.0-rc.1/gaia-0.26.0-rc.1-linux-x64");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("gaia-0.26.0-rc.1-bytes");
  });

  it("appends a second platform to an RC on next", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.26.0-rc.1", channel: "next" });
    const res = await publish(env, {
      version: "0.26.0-rc.1",
      channel: "next",
      filename: "gaia-0.26.0-rc.1-darwin-arm64",
    });
    expect(res.status).toBe(201);
    expect(((await res.json()) as any).published.version_artifacts).toBe(2);
    expect((await manifestOf(env, "gaia")).next_versions!["0.26.0-rc.1"].artifacts).toHaveLength(2);
  });

  it("derives channels for a manifest stored before channels existed", async () => {
    const env = makeEnv();
    await publish(env, { version: "0.25.2" });
    const legacy = (await env.bucket.get("agents/gaia/manifest.json").then((o: any) => o.json())) as AgentManifest;
    delete legacy.channels;
    await env.bucket.put("agents/gaia/manifest.json", JSON.stringify(legacy));

    await publish(env, { version: "0.26.0-rc.1", channel: "next" });
    const m = await manifestOf(env, "gaia");
    expect(m.latest_version).toBe("0.25.2");
    expect(m.channels).toEqual({ stable: "0.25.2", next: "0.26.0-rc.1" });
  });

  it("fixes a version's channel at its first publish", async () => {
    const env = makeEnv();
    // A final may be staged on next; it cannot then join stable under the same version.
    await publish(env, { version: "0.27.0", channel: "next" });
    const res = await publish(env, { version: "0.27.0", filename: "gaia-0.27.0-darwin-arm64" });
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error.code).toBe("channel_mismatch");
    const m = await manifestOf(env, "gaia");
    expect(m.versions).toEqual({});
    expect(m.next_versions!["0.27.0"].artifacts).toHaveLength(1);
  });

  it("answers a re-posted by-reference RC artifact with version_exists", async () => {
    const env = makeEnv();
    const bytes = new TextEncoder().encode("rc-installer");
    const sha256 = await sha256Of(bytes);
    await env.bucket.put("agents/gaia/0.26.0-rc.1/gaia-setup.exe", bytes, { sha256 });
    const post = () =>
      worker.fetch(
        publishByRefRequest({
          manifestYaml: sampleManifest({ id: "gaia", version: "0.26.0-rc.1" }),
          filename: "gaia-setup.exe",
          sha256,
          size: bytes.byteLength,
          channel: "next",
        }),
        env as never
      );
    expect((await post()).status).toBe(201);
    const again = await post();
    expect(again.status).toBe(409);
    expect(((await again.json()) as any).error.code).toBe("version_exists");
  });

  it("rejects an unknown channel", async () => {
    const env = makeEnv();
    const res = await publish(env, { version: "0.25.2", channel: "beta" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_channel");
    expect(env.bucket.keys()).toEqual([]);
  });
});

describe("tui alias — agents/terminal-hub/ and agents/tui/ resolve each other", () => {
  it("serves today's terminal-hub publish under both paths, with the index unchanged", async () => {
    const env = makeEnv();
    expect((await publish(env, { id: "terminal-hub", version: "0.25.2" })).status).toBe(201);

    for (const id of ["terminal-hub", "tui"]) {
      const art = await get(env, `/agents/${id}/0.25.2/terminal-hub-0.25.2-linux-x64`);
      expect(art.status).toBe(200);
      expect(await art.text()).toBe("terminal-hub-0.25.2-bytes");
      const m = await manifestOf(env, id);
      expect(m.id).toBe("terminal-hub");
      expect(m.latest_version).toBe("0.25.2");
    }
    expect((await get(env, "/agents/tui/0.25.2/gaia-agent.yaml")).status).toBe(200);
    expect((await indexOf(env)).agents.map((a) => a.id)).toEqual(["terminal-hub"]);
  });

  it("reads tui RCs and finals through the old path, and merges the two manifests", async () => {
    const env = makeEnv();
    await publish(env, { id: "terminal-hub", version: "0.25.2", readme: "# TUI 0.25.2" });
    await publish(env, { id: "tui", version: "0.26.0-rc.1", channel: "next" });

    // RC only under tui: the index keeps the stable package's id and version.
    let entries = (await indexOf(env)).agents;
    expect(entries.map((a) => a.id)).toEqual(["terminal-hub"]);
    expect(entries[0].latest_version).toBe("0.25.2");
    expect(entries[0].channels).toEqual({ stable: "0.25.2", next: "0.26.0-rc.1" });
    // Docs come from the id that published the stable latest.
    expect(entries[0].readme).toBe("# TUI 0.25.2");
    const rc = await get(env, "/agents/terminal-hub/0.26.0-rc.1/tui-0.26.0-rc.1-linux-x64");
    expect(rc.status).toBe(200);

    await publish(env, { id: "tui", version: "0.26.0" });
    entries = (await indexOf(env)).agents;
    expect(entries.map((a) => a.id)).toEqual(["tui"]);
    expect(entries[0].latest_version).toBe("0.26.0");
    expect(Object.keys(entries[0].min_gaia_versions!).sort()).toEqual(["0.25.2", "0.26.0"]);

    for (const id of ["terminal-hub", "tui"]) {
      const m = await manifestOf(env, id);
      expect(m.id).toBe("tui");
      expect(m.latest_version).toBe("0.26.0");
      expect(Object.keys(m.versions).sort()).toEqual(["0.25.2", "0.26.0"]);
      expect(m.versions["0.25.2"].artifact.path).toBe(
        "agents/terminal-hub/0.25.2/terminal-hub-0.25.2-linux-x64"
      );
    }
    const old = await get(env, "/agents/terminal-hub/0.26.0/tui-0.26.0-linux-x64");
    expect(await old.text()).toBe("tui-0.26.0-bytes");
    const neu = await get(env, "/agents/tui/0.25.2/terminal-hub-0.25.2-linux-x64");
    expect(await neu.text()).toBe("terminal-hub-0.25.2-bytes");
  });

  it("refuses a version the other id already holds, on either channel", async () => {
    const env = makeEnv();
    await publish(env, { id: "terminal-hub", version: "0.25.2" });
    const res = await publish(env, { id: "tui", version: "0.25.2" });
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error.code).toBe("version_exists");
    expect(await env.bucket.get("agents/tui/manifest.json")).toBeNull();

    await publish(env, { id: "tui", version: "0.26.0-rc.1", channel: "next" });
    const rc = await publish(env, { id: "terminal-hub", version: "0.26.0-rc.1", channel: "next" });
    expect(rc.status).toBe(409);
    expect(((await rc.json()) as any).error.code).toBe("version_exists");
  });

  it("reports the merged channels when publishing under the new id", async () => {
    const env = makeEnv();
    await publish(env, { id: "terminal-hub", version: "0.25.2" });
    const res = await publish(env, { id: "tui", version: "0.26.0-rc.1", channel: "next" });
    const published = ((await res.json()) as any).published;
    expect(published.latest_version).toBe("0.25.2");
    expect(published.channels).toEqual({ stable: "0.25.2", next: "0.26.0-rc.1" });
  });

  it("extends the owner of one id to the other", async () => {
    const env = makeEnv();
    await publish(env, { id: "terminal-hub", version: "0.25.2" });
    const res = await publish(env, {
      id: "tui",
      version: "0.26.0",
      author: "Someone Else",
      token: "tok_admin",
    });
    expect(res.status).toBe(403);
    expect(((await res.json()) as any).error.code).toBe("forbidden_scope");
  });

  it("still 404s an object neither id holds, naming the requested path", async () => {
    const env = makeEnv();
    await publish(env, { id: "terminal-hub", version: "0.25.2" });
    const res = await get(env, "/agents/tui/9.9.9/nothing");
    expect(res.status).toBe(404);
    expect(((await res.json()) as any).error.message).toContain("agents/tui/9.9.9/nothing");
    expect((await get(env, "/agents/gaia/manifest.json")).status).toBe(404);
  });
});
