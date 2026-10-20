// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Published-binary lookup for the home page's download links.
//
// The download buttons are built from what hub.amd-gaia.ai (the download
// server, workers/agent-hub) actually published, fetched at build time from
// `${HUB_CATALOG_URL}/agents/<id>/manifest.json`. HUB_CATALOG_URL is REQUIRED:
// if it is unset, or the fetch fails, or the shape is wrong, the build FAILS
// LOUDLY -- there is no silent fallback to stale filenames.
//
//   Production (Railway): set HUB_CATALOG_URL=https://hub.amd-gaia.ai
//   Local dev:            HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run dev

/** One published per-platform binary, with the URL it downloads from. */
export interface PlatformBinary {
  filename: string;
  sha256: string;
  size_bytes: number;
  url: string;
}

/**
 * One artifact exactly as the manifest publishes it. `path` is the hub's own
 * object key (`agents/<id>/<version>/<filename>`) and
 * workers/agent-hub/schemas/manifest.schema.json requires it on every
 * artifact — so it is the thing a download URL is built from, never a shape
 * this file re-derives. There is no `url` field to use instead.
 */
interface HubArtifact {
  filename: string;
  sha256: string;
  size_bytes: number;
  path?: string;
}

export interface ComponentRelease {
  version: string;
  binaries: PlatformBinary[];
}

const componentReleases = new Map<string, Promise<ComponentRelease>>();

/**
 * The published per-platform binaries of one hub entry, from its
 * `agents/<id>/manifest.json`. index.json carries a single representative
 * download size, not the artifact list, so a per-platform download link has to
 * come from here.
 *
 * Each URL is the hub origin joined to the artifact's own published `path`.
 *
 * Fails loudly for the same reason the catalog does: a download button built
 * from stale or guessed filenames 404s on the visitor, and the filenames are
 * only knowable from what the hub actually published.
 */
export async function getComponentRelease(
  id: string,
): Promise<ComponentRelease> {
  const hubUrl = process.env.HUB_CATALOG_URL;
  if (!hubUrl) {
    throw new Error(
      `[catalog] HUB_CATALOG_URL is not set, so the published binaries for '${id}' ` +
        `cannot be resolved. Set HUB_CATALOG_URL=https://hub.amd-gaia.ai.`,
    );
  }
  const base = hubUrl.replace(/\/+$/, "");
  const url = `${base}/agents/${id}/manifest.json`;

  const load = async (): Promise<ComponentRelease> => {
    let res: Response;
    try {
      res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
    } catch (e) {
      throw new Error(
        `[catalog] Failed to fetch the '${id}' manifest from ${url}: ${(e as Error).message}. ` +
          `The download links are built from it and there is no bundled fallback.`,
      );
    }
    if (!res.ok) {
      throw new Error(
        `[catalog] Manifest request for '${id}' at ${url} returned HTTP ${res.status}. ` +
          `Check that '${id}' is published (GET ${base}/index.json lists what the hub serves).`,
      );
    }
    const manifest = (await res.json()) as {
      latest_version?: string;
      versions?: Record<string, { artifacts?: HubArtifact[] }>;
    };
    const version = manifest.latest_version;
    if (!version) {
      throw new Error(
        `[catalog] The '${id}' manifest at ${url} declares no latest_version.`,
      );
    }
    const artifacts = manifest.versions?.[version]?.artifacts ?? [];
    if (!artifacts.length) {
      throw new Error(
        `[catalog] The '${id}' manifest names latest_version ${version} but publishes ` +
          `no artifacts for it. See ${url}.`,
      );
    }
    console.log(
      `[catalog] Loaded ${artifacts.length} '${id}' binaries at ${version}`,
    );
    return {
      version,
      binaries: artifacts.map((a) => {
        // The hub's own object key, never a re-derived one: a CDN move or a
        // per-OS subdirectory would 404 every link on the site silently.
        if (!a.path) {
          throw new Error(
            `[catalog] The '${id}' artifact '${a.filename}' at ${version} publishes no ` +
              `'path'. manifest.schema.json requires it on every artifact and the download ` +
              `URL is built from it — the site will not guess an object key. Republish ` +
              `'${id}' from a hub Worker that emits complete artifacts (see ${url} and ` +
              `workers/agent-hub/schemas/manifest.schema.json).`,
          );
        }
        return {
          filename: a.filename,
          sha256: a.sha256,
          size_bytes: a.size_bytes,
          url: `${base}/${a.path.replace(/^\/+/, "")}`,
        };
      }),
    };
  };

  const cached = componentReleases.get(id) ?? load();
  componentReleases.set(id, cached);
  return cached;
}

// ---- Display helpers ----

/** Human-readable download size, e.g. "2.3 MB". */
export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
