// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A stand-in for the GitHub URLs auto-updater.cjs reads, built from release()
 * fixtures: releases.atom (paged with ?after=<tag>), latest/download/<file>,
 * and download/<tag>/<file> for GET and HEAD. The REST API always answers 403,
 * as it does on a network that used up its 60 anonymous calls.
 */

"use strict";

const RELEASES_PAGE = "https://github.com/amd/gaia/releases";
const FEED_PAGE_SIZE = 10;

function manifestName() {
  if (process.platform === "win32") return "latest.yml";
  if (process.platform === "darwin") return "latest-mac.yml";
  return process.arch === "arm64" ? "latest-linux-arm64.yml" : "latest-linux.yml";
}

function installerName(version) {
  if (process.platform === "win32") return `gaia-agent-ui-${version}-x64-setup.exe`;
  if (process.platform === "darwin") return `gaia-agent-ui-${version}-arm64.zip`;
  const arch = process.arch === "arm64" ? "arm64" : "x86_64";
  return `gaia-agent-ui-${version}-${arch}.AppImage`;
}

function manifestFor(version, file) {
  return [
    `version: ${version}`,
    "files:",
    `  - url: ${file}`,
    "    sha512: c2hhNTEy",
    "    size: 100",
    `path: ${file}`,
    "sha512: c2hhNTEy",
    "releaseDate: '2026-06-01T00:00:00.000Z'",
    "",
  ].join("\n");
}

/**
 * A release as GitHub serves it, e.g. v0.25.2-rc3 with its manifest. `manifest`
 * and `installer` false leave that file out; `assets` replaces both. Evaluate
 * under the platform being tested: names follow process.platform at call time.
 */
function release(
  tag,
  { draft = false, manifest = true, installer = true, published = "2026-06-01T00:00:00Z", assets } = {}
) {
  const version = tag.replace(/^v/, "").replace(/-rc(\d+)$/, "-rc.$1");
  const file = installerName(version);
  const files = assets || {};
  if (!assets) {
    if (installer) files[file] = "";
    if (manifest) files[manifestName()] = manifestFor(version, file);
  }
  return { tag, draft, published, assets: files };
}

function atomEntry(r) {
  return `
  <entry>
    <id>tag:github.com,2008:Repository/903960898/${r.tag}</id>
    <updated>${r.published}</updated>
    <link rel="alternate" type="text/html" href="${RELEASES_PAGE}/tag/${r.tag}"/>
    <title>${r.tag}</title>
    <content type="html">&lt;p&gt;Release notes&lt;/p&gt;</content>
    <author><name>github-actions[bot]</name></author>
  </entry>`;
}

function atomFeed(releases) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/" xml:lang="en-US">
  <id>tag:github.com,2008:${RELEASES_PAGE}</id>
  <link type="text/html" rel="alternate" href="${RELEASES_PAGE}"/>
  <title>Release notes from gaia</title>
  <updated>2026-11-01T22:05:25Z</updated>${releases.map(atomEntry).join("")}
</feed>
`;
}

function httpResponse(status, body = "") {
  return { ok: status >= 200 && status < 300, status, text: async () => body };
}

function assetResponse(owner, file, method) {
  if (!owner || owner.draft || !(file in owner.assets)) return httpResponse(404, "Not Found");
  return httpResponse(200, method === "HEAD" ? "" : owner.assets[file]);
}

/**
 * Install the stand-in as global.fetch, serving `releases` newest first.
 * `respond(url, options)` may return a response to override a route, for the
 * failures a healthy GitHub never produces. A URL nothing here serves rejects,
 * so a new request can't pass by accident.
 */
function serveReleases(releases, { respond } = {}) {
  const published = releases.filter((r) => !r.draft);
  global.fetch = jest.fn(async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    const override = respond && respond(url, options);
    if (override) return override;

    if (url.includes("api.github.com")) return httpResponse(403, '{"message":"rate limit exceeded"}');

    const feed = /^https:\/\/github\.com\/amd\/gaia\/releases\.atom(?:\?after=(.+))?$/.exec(url);
    if (feed) {
      const after = feed[1] ? decodeURIComponent(feed[1]) : null;
      const start = after ? published.findIndex((r) => r.tag === after) + 1 : 0;
      return httpResponse(200, atomFeed(published.slice(start, start + FEED_PAGE_SIZE)));
    }

    const latest = /\/releases\/latest\/download\/([^/]+)$/.exec(url);
    if (latest) {
      const newestFinal = published.find((r) => /^v\d+\.\d+\.\d+$/.test(r.tag));
      return assetResponse(newestFinal, latest[1], options.method);
    }

    const asset = /\/releases\/download\/([^/]+)\/([^/]+)$/.exec(url);
    if (asset) {
      return assetResponse(published.find((r) => r.tag === asset[1]), asset[2], options.method);
    }

    throw new Error(`Unexpected request to ${url}`);
  });
}

/** Rejects every request, so a test that forgot its stand-in can't reach the network. */
function refuseFetch() {
  global.fetch = jest.fn(async (url) => {
    throw new Error(`Unexpected request to ${url}`);
  });
}

function fetchedUrls() {
  return global.fetch.mock.calls.map(([url]) => String(url));
}

module.exports = {
  httpResponse,
  installerName,
  manifestName,
  refuseFetch,
  release,
  serveReleases,
  fetchedUrls,
};
