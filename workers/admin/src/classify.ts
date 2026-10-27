// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Sorts a GitHub release asset into "what is it" (product), "which OS", and
 * whether it is an install file at all. Update-metadata and checksum files are
 * fetched by the desktop updater and by verification scripts, so counting them
 * as downloads would overstate installs by more than half.
 */

export type Os = "Windows" | "Linux" | "macOS" | "Cross-platform" | "Unclassified";
export type Product =
  | "Installer"
  | "Agent UI"
  | "CLI / TUI"
  | "Python package"
  | "Agents"
  | "Unclassified";

export interface AssetClass {
  kind: "install" | "meta";
  os: Os;
  product: Product;
}

const META_PATTERNS: RegExp[] = [
  /^latest(-[a-z]+)?\.yml$/i, // electron-updater feed, polled on every app start
  /\.blockmap$/i, // differential-update data
  /\.sigstore\.json$/i,
  /sha256sums/i,
  /^gaia-agent-release\.json$/i,
  /^gaia-agent\.yaml$/i,
];

function productOf(name: string): Product {
  if (/^amd_gaia/i.test(name)) return "Python package";
  if (/^gaia-agent-ui-/i.test(name)) return "Agent UI";
  if (/^gaia_agent_/i.test(name) || /^gaia-agent-(linux|win32|darwin)/i.test(name)) return "Agents";
  if (/^(jira|example)-.*setup\.exe$/i.test(name)) return "Agents";
  if (/^gaia-(windows|linux|darwin)-(setup|amd64|arm64)/i.test(name)) {
    return /setup/i.test(name) ? "Installer" : "CLI / TUI";
  }
  if (/^Gaia(Hybrid)?Installer/i.test(name) || /^gaia-windows-installer-/i.test(name)) return "Installer";
  if (/^gaia[-_]v?\d/i.test(name)) return "Installer";
  return "Unclassified";
}

function osOf(name: string): Os {
  if (/\.(whl|tar\.gz)$/i.test(name)) return "Cross-platform";
  if (/\.exe$/i.test(name) || /windows|win32|win-x64/i.test(name) || /^Gaia(Hybrid)?Installer/i.test(name)) {
    return "Windows";
  }
  if (/\.(deb|appimage|rpm)$/i.test(name) || /linux/i.test(name)) return "Linux";
  if (/\.(dmg|pkg)$/i.test(name) || /darwin|mac/i.test(name)) return "macOS";
  return "Unclassified";
}

export function classifyAsset(name: string): AssetClass {
  if (META_PATTERNS.some((p) => p.test(name))) {
    return { kind: "meta", os: osOf(name), product: "Unclassified" };
  }
  return { kind: "install", os: osOf(name), product: productOf(name) };
}

const MAIN_TAG_RE = /^v\d+(\.\d+){2,3}(-rc\d+)?$/;

export function isMainOrRcTag(tag: string): boolean {
  return MAIN_TAG_RE.test(tag);
}

export function isRcTag(tag: string): boolean {
  return /-rc\d+$/.test(tag);
}
