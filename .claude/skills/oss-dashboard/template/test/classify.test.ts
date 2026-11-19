import { describe, expect, it } from "vitest";
import { classifyAsset, isMainOrRcTag, isRcTag, osOf } from "../src/classify";

// Release file names as real projects publish them (Rust, Go, Electron, Python, Node, Deno, Tauri).
const OS_CASES: [string, string][] = [
  ["ripgrep-14.1.1-x86_64-pc-windows-msvc.zip", "Windows"],
  ["ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz", "Linux"],
  ["ripgrep-14.1.1-aarch64-apple-darwin.tar.gz", "macOS"],
  ["ripgrep_14.1.1-1_amd64.deb", "Linux"],
  ["gh_2.62.0_windows_amd64.msi", "Windows"],
  ["gh_2.62.0_macOS_universal.zip", "macOS"],
  ["gh_2.62.0_linux_arm64.rpm", "Linux"],
  ["App-Setup-1.2.0.exe", "Windows"],
  ["App-1.2.0-arm64.dmg", "macOS"],
  ["App-1.2.0.AppImage", "Linux"],
  ["app_1.2.0_x64_en-US.msi", "Windows"],
  ["app_1.2.0_aarch64.dmg", "macOS"],
  ["deno-x86_64-pc-windows-msvc.zip", "Windows"],
  ["uv-x86_64-unknown-linux-gnu.tar.gz", "Linux"],
  ["ollama-darwin.tgz", "macOS"],
  ["OllamaSetup.exe", "Windows"],
  ["mypkg-1.0.0-py3-none-any.whl", "Cross-platform"],
  ["mypkg-1.0.0.tar.gz", "Cross-platform"],
  ["extension-1.0.0.vsix", "Cross-platform"],
  ["app-release.apk", "Android"],
  ["app-arm64-v8a-android.apk", "Android"],
  ["gum_0.1.0_linux_amd64.apk", "Linux"],
  ["gum_0.14.0_aarch64.apk", "Linux"],
  ["tool-freebsd-amd64.tar.gz", "BSD"],
  ["glow_2.0.0_openbsd_arm.tar.gz", "BSD"],
  ["tool-x86_64.zip", "Unclassified"],
  ["Json120r1.zip", "Unclassified"],
  ["mypkg-1.0.0.zip", "Unclassified"],
];

describe("osOf", () => {
  for (const [name, os] of OS_CASES) it(`${name} is ${os}`, () => expect(osOf(name)).toBe(os));
});

describe("classifyAsset", () => {
  const META = [
    "latest.yml", "latest-mac.yml", "latest-linux-arm64.yml", "latest-mac.json", "latest.json", "builder-debug.yml", "builder-effective-config.yaml", "App-Setup-1.2.0.exe.blockmap",
    "SHA256SUMS", "sha256sums.txt", "checksums.txt", "ripgrep-14.1.1-x86_64-pc-windows-msvc.zip.sha256",
    "app.tar.gz.sig", "app.tar.gz.asc", "app.intoto.jsonl", "multiple.intoto.jsonl", "app.sigstore.json",
    "sbom.spdx.json", "app-1.0.0.cdx.json", "app_1.0.0_sbom.json", "RELEASES", "App-1.2.0-full.nupkg", "gcm-symbols-2.6.0.tar.gz", "Foo.2.0.0.snupkg", "signing.cer",
  ];
  for (const name of META) {
    it(`${name} is metadata, not an install`, () => expect(classifyAsset(name).kind).toBe("meta"));
  }

  // The template's example PRODUCT_RULES: installers are "Desktop app", archives "CLI".
  it("uses the first product rule that matches", () => {
    expect(classifyAsset("App-Setup-1.2.0.exe")).toEqual({ kind: "install", os: "Windows", product: "Desktop app" });
    expect(classifyAsset("uv-x86_64-unknown-linux-gnu.tar.gz")).toEqual({ kind: "install", os: "Linux", product: "CLI" });
  });

  it("leaves a file no rule matches Unclassified instead of guessing", () => {
    expect(classifyAsset("tool-linux-arm64")).toEqual({ kind: "install", os: "Linux", product: "Unclassified" });
  });
});

describe("tags", () => {
  it("recognises release and pre-release tags, rejects sub-package and nightly tags", () => {
    for (const t of ["v1.2.3", "1.2.3", "v1.2", "v1.2.3.4", "v1.2.3-rc1", "v1.2.3-rc.2", "v2.0.0-beta.1", "v2.0.0-alpha3", "8.0.0rc1", "1.0.0a1", "2.0.dev3", "v3.0.0-next.4"]) {
      expect(isMainOrRcTag(t), t).toBe(true);
    }
    for (const t of ["nightly", "plugin-v1.0.0", "v1.2.3-dev", "latest", "v1"]) expect(isMainOrRcTag(t), t).toBe(false);
  });

  it("tells release candidates from finals by the tag, not GitHub's prerelease flag", () => {
    expect(isRcTag("v1.2.3-rc1")).toBe(true);
    expect(isRcTag("v1.2.3-beta.2")).toBe(true);
    expect(isRcTag("v1.2.3")).toBe(false);
    expect(isRcTag("8.0.0rc1")).toBe(true);
    expect(isRcTag("1.0.0b2")).toBe(true);
    expect(isRcTag("v2.1")).toBe(false);
  });
});
