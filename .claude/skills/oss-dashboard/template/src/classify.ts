/**
 * Sorts a GitHub release file into "what is it" (product), "which OS", and
 * whether it is an install at all. Update feeds, checksums and signatures are
 * fetched by updaters and verification scripts, so counting them as downloads
 * would overstate installs, often by more than half.
 */

import { META_FILES, OS_RULES, PRODUCT_RULES, RC_TAG, RELEASE_TAG } from "./config";

export type Os = "Windows" | "Linux" | "macOS" | "Android" | "BSD" | "Cross-platform" | "Unclassified";

export interface AssetClass {
  kind: "install" | "meta";
  os: Os;
  product: string;
}

export function osOf(name: string): Os {
  const rule = OS_RULES.find((r) => r.pattern.test(name));
  if (rule) return rule.os;
  const n = name.toLowerCase();
  const namesOs = /(linux|darwin|mac|osx|win|bsd|dragonfly|android|apple|musl|gnu)/.test(n);
  // Platform-neutral packages, and source tarballs that name no platform. A zip that names none stays Unclassified:
  // installer zips are as often Windows-only, so OS_RULES must say.
  if (/\.(whl|jar|vsix|crate|gem)$/.test(n) || (/\.(tar\.gz|tgz)$/.test(n) && !namesOs && !/(x86|x64|amd64|arm64|aarch64|i686|riscv)/.test(n))) {
    return "Cross-platform";
  }
  // .apk is also Alpine Linux's package format (goreleaser/nfpm emit it): Linux unless the name says android.
  if (/\.apk$/.test(n) && !/android/.test(n) && /(linux|musl|alpine|x86_64|amd64|arm64|aarch64|armv\d|i386|386|riscv64|ppc64le|s390x)/.test(n)) return "Linux";
  if (/\.(apk|aab)$|android/.test(n)) return "Android";
  if (/(freebsd|openbsd|netbsd|dragonfly)/.test(n)) return "BSD";
  if (/\.(exe|msi|msix|appx)$/.test(n) || /(^|[-_.])(windows|win32|win64|win|msvc|mingw)([-_.]|$)|pc-windows/.test(n)) return "Windows";
  if (/\.(dmg|pkg)$/.test(n) || /(^|[-_.])(darwin|macos|mac|osx|apple)([-_.]|$)|apple-darwin/.test(n)) return "macOS";
  if (/\.(deb|rpm|appimage|snap|flatpak)$/.test(n) || /(^|[-_.])(linux|musl|gnu|ubuntu|debian|fedora)([-_.]|$)|unknown-linux/.test(n)) return "Linux";
  return "Unclassified";
}

export function classifyAsset(name: string): AssetClass {
  if (META_FILES.some((p) => p.test(name))) return { kind: "meta", os: osOf(name), product: "Unclassified" };
  const rule = PRODUCT_RULES.find((r) => r.pattern.test(name));
  return { kind: "install", os: osOf(name), product: rule?.product ?? "Unclassified" };
}

export function isMainOrRcTag(tag: string): boolean {
  return RELEASE_TAG.test(tag);
}

export function isRcTag(tag: string): boolean {
  return RC_TAG.test(tag);
}
