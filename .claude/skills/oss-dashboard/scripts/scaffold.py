#!/usr/bin/env python3
"""Create a configured dashboard project from the template and a probe report.

    python scaffold.py --probe probe.json --out ./dashboard \\
        (--route "example.org/dashboard*" [--zone example.org] | --workers-dev) [--name "My Project"]

Copies ../template, writes src/config.ts and wrangler.toml from the probe, and
writes the project's own tests over its real data: test/assets.test.ts (every
release file must be classified) and test/tags.test.ts (RELEASE_TAG must match
the release tags). Copies audit_numbers.py and verify_deploy.py into scripts/.
Only src/config.ts is edited; test/example-config.ts stays the template's copy.
Refuses to write into a directory that is not empty.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "template"
COPIED_SCRIPTS = ("audit_numbers.py", "verify_deploy.py")

# Tokens that describe a build rather than the product, dropped when grouping file names.
BUILD_TOKENS = re.compile(
    r"^(v?\d[\w.]*|windows|win|win32|win64|linux|darwin|macos|mac|osx|apple|pc|unknown|gnu|gnueabihf|musl|msvc|"
    r"x86|x64|x86_64|amd64|arm64|aarch64|i386|i686|armv6|armv7|armhf|universal|setup|installer|portable|"
    r"en|us|py3|py2|none|any|cp\d+|abi3|manylinux\w*|android|freebsd|openbsd|netbsd|dragonfly|solaris|illumos|"
    r"arm|armv\w+|386|mips\w*|ppc64\w*|s390x|riscv64|loong64|wasm|js|full|delta|static|bin|binary|release)$",
    re.I,
)
EXT = re.compile(
    r"\.(tar\.gz|tar\.xz|tar\.bz2|tgz|zip|exe|msi|msix|dmg|pkg|deb|rpm|appimage|whl|apk|vsix|jar|gem|nupkg|snap|flatpak|7z|zst)$",
    re.I,
)
META = re.compile(
    r"(^|[-_.])symbols([-_.]|$)|\.(snupkg|pdb|dsym|cer|crt|nupkg)$|^(changelog|changes|readme|license|notice|release[-_]?notes)(\.[a-z]+)?$|^latest(-[a-z0-9-]+)?\.(ya?ml|json)$|^builder-(debug|effective-config)\.ya?ml$|\.blockmap$|\.(sig|asc|pem|cert|sigstore|minisig|sha1|sha256|sha512|md5)$|sigstore\.json$|intoto\.jsonl$|sums?(\.txt)?$|checksums?|sbom|\.(spdx|cdx)(\.json)?$|^RELEASES$",
    re.I,
)

# The template's tag patterns, exactly as src/config.ts writes them.
VERSION_SRC = r"v?\d+(\.\d+){1,3}([-.]?(rc|alpha|beta|a|b|pre|next|dev)\.?\d+)?"
RELEASE_TAG_LINE = r"export const RELEASE_TAG = /^v?\d+(\.\d+){1,3}([-.]?(rc|alpha|beta|a|b|pre|next|dev)\.?\d+)?$/i;"
RC_TAG_LINE = r"export const RC_TAG = /[-.]?(rc|alpha|beta|a|b|pre|next|dev)\.?\d+$/i;"
CODE_STATS_LINE = "export const CODE_STATS = true;"
TRAFFIC_LINE = "export const TRAFFIC = true;"
CHANNEL_KEYS = (
    "homebrew",
    "homebrewCasks",
    "docker",
    "rubygems",
    "nuget",
    "conda",
    "packagist",
)
CHANNELS_LINE = (
    "export const CHANNELS: { homebrew: string[]; homebrewCasks: string[]; docker: string[]; rubygems: string[]; nuget: string[]; conda: string[]; packagist: string[] }"
    " = { homebrew: [], homebrewCasks: [], docker: [], rubygems: [], nuget: [], conda: [] };"
)
# Keys added to probe_repo.py's report after its first version; an older report must be re-probed.
REQUIRED_PROBE_KEYS = ("code_stats_available", "traffic_access", "channels")
DEFAULT_RELEASE_TAG = re.compile("^" + VERSION_SRC + "$", re.I)
VERSION = re.compile(VERSION_SRC, re.I)
# Second-level labels under a two-letter country code that are not themselves registrable (example.co.uk).
CCTLD_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu"}


def stem(name: str) -> str | None:
    base = EXT.sub("", name)
    tokens = [t for t in re.split(r"[-_.\s]+", base) if t]
    keep = []
    for t in tokens:
        if BUILD_TOKENS.match(t):
            break
        # A version glued to the name ("Json120r1", "app2024") ends the family name; "python3-tool" keeps its 3.
        glued = re.match(r"^([A-Za-z][A-Za-z]+?)\d{2,}", t)
        if glued:
            keep.append(glued.group(1))
            break
        keep.append(t)
    return "-".join(keep).lower() or None


def product_rules(asset_names: list[str]) -> list[tuple[str, str]]:
    """(product label, regex source) per file-name family, most files first."""
    fam = Counter(s for n in asset_names if not META.search(n) and (s := stem(n)))
    rules = []
    for s, _ in fam.most_common(5):
        label = s.replace("-", " ").strip().capitalize()
        parts = [re.escape(p) for p in s.split("-")]
        rules.append((label, "^" + "[-_.]".join(parts) + r"(?=[-_.]|$)"))
    # Longest prefixes first, so "app-cli" is not swallowed by "app".
    rules.sort(key=lambda r: -len(r[1]))
    return rules


def js_regex_escape(s: str) -> str:
    """Escape a literal for a JavaScript regex literal (/.../), including the delimiter."""
    return "".join("\\" + c if c in "\\^$.|?*+()[]{}/" else c for c in s)


def release_tag_line(tags: list[str]) -> str | None:
    """The RELEASE_TAG line for these tags, or None to keep the template's. Prints the choice."""
    if not tags:
        print(
            "RELEASE_TAG: no release tags yet; kept the template default (plain version numbers)."
        )
        return None
    default_hits = sum(1 for t in tags if DEFAULT_RELEASE_TAG.match(t))
    if default_hits >= 0.8 * len(tags):
        print(
            f"RELEASE_TAG: template default kept; it matches {default_hits} of {len(tags)} release tags."
        )
        return None
    prefixes = Counter(t[: m.start()] for t in tags if (m := VERSION.search(t)))
    if not prefixes:
        print(
            f"RELEASE_TAG: no release tag carries a version number (e.g. {', '.join(tags[:3])}); kept the template default. Set RELEASE_TAG by hand."
        )
        return None
    prefix = prefixes.most_common(1)[0][0]
    chosen = re.compile("^" + re.escape(prefix) + VERSION_SRC + "$", re.I)
    hits = [t for t in tags if chosen.match(t)]
    missed = [t for t in tags if not chosen.match(t)]
    shown = f"/^{js_regex_escape(prefix)}{VERSION_SRC}$/i"
    note = f"matches {len(hits)} of {len(tags)} release tags ({100 * len(hits) / len(tags):.0f}%)"
    if missed:
        note += f"; leaves out e.g. {', '.join(missed[:5])}"
    if prefix == "":
        print(
            f"RELEASE_TAG: template default kept (the most common tag form has no prefix); it {note}. Check it by hand."
        )
        return None
    print(
        f"RELEASE_TAG: {shown} (prefix {prefix!r}, the most common of {len(prefixes)}); it {note}. RC_TAG unchanged."
    )
    return f"export const RELEASE_TAG = {shown};"


def ts_str(s: str) -> str:
    return json.dumps(s)


def subn_once(pat: str, new: str, s: str, flags: int = 0) -> str:
    s, n = re.subn(pat, lambda _m: new, s, count=1, flags=flags)
    if n != 1:
        raise SystemExit(
            f"scaffold: template config.ts no longer contains {pat!r}; update scaffold.py with the template."
        )
    return s


def replace_line(line: str, new: str, s: str) -> str:
    if s.count(line) != 1:
        raise SystemExit(
            f"scaffold: template config.ts no longer contains {line!r} (exactly once); update scaffold.py with the template."
        )
    return s.replace(line, new, 1)


def render_config(s: str, probe: dict, name: str, base_path: str) -> str:
    """The template's src/config.ts text configured from the probe. Exits if the template has drifted."""
    pk = probe.get("packages", {})

    def arr(xs: list[str]) -> str:
        return ", ".join(ts_str(x) for x in xs)

    rules = product_rules(probe.get("asset_names", []))
    rules_ts = (
        "".join(f"  {{ product: {ts_str(l)}, pattern: /{r}/i }},\n" for l, r in rules)
        or "  // No release files found; add rules when the project starts publishing them.\n"
    )
    for pat, new in (
        (r'name: "Example",', f"name: {ts_str(name)},"),
        (r'repo: "example-org/example",', f"repo: {ts_str(probe['repo'])},"),
        (
            r'defaultBranch: "main",',
            f"defaultBranch: {ts_str(probe['default_branch'])},",
        ),
        (r'basePath: "/admin",', f"basePath: {ts_str(base_path)},"),
        (r'npm: \["example"\],', f"npm: [{arr(pk.get('npm', []))}],"),
        (r"pypi: \[\],", f"pypi: [{arr(pk.get('pypi', []))}],"),
        (r"crates: \[\],", f"crates: [{arr(pk.get('crates', []))}],"),
        (
            r"export const STAR_HISTORY = true;",
            f"export const STAR_HISTORY = {'true' if probe.get('stars', 0) < 40_000 else 'false'};",
        ),
    ):
        s = subn_once(pat, new, s)

    # TESTS: only workflow and framework are set here; `jobs` and anything else in the block stay as they are.
    workflow, framework = probe.get("test_workflow"), probe.get("test_framework")
    if not (workflow and framework):
        workflow, framework = "", "pytest"
        print(
            'Tests source off: the probe found no test workflow with a recognised runner (TESTS.workflow is empty; framework "pytest" is a placeholder).'
        )
    m = re.search(r"export const TESTS: [^=]*= \{\n(.*?)\n\};", s, re.S)
    if not m:
        raise SystemExit(
            "scaffold: TESTS block not found in template config.ts; update scaffold.py with the template."
        )
    block = subn_once(
        r'workflow: "[^"]*",', f"workflow: {ts_str(workflow)},", m.group(1)
    )
    block = subn_once(r'framework: "[^"]*",', f"framework: {ts_str(framework)},", block)
    s = s[: m.start(1)] + block + s[m.end(1) :]

    code_stats, traffic, channels = (
        probe["code_stats_available"],
        probe["traffic_access"],
        probe["channels"],
    )
    s = replace_line(
        CODE_STATS_LINE,
        f"export const CODE_STATS = {'true' if code_stats else 'false'};",
        s,
    )
    if not code_stats:
        print(
            f"CODE_STATS = false: {probe['commits']:,} commits, past the 10,000 GitHub computes code-frequency statistics for."
        )
    s = replace_line(
        TRAFFIC_LINE, f"export const TRAFFIC = {'true' if traffic else 'false'};", s
    )
    if not traffic:
        print(
            "TRAFFIC = false: the probe's token could not read the traffic API (it needs push access; fine-grained: Administration, read). "
            "Set it to true once the deployed token has that access."
        )
    literal = (
        "{\n"
        + "".join(f"  {k}: [{arr(channels.get(k, []))}],\n" for k in CHANNEL_KEYS)
        + "}"
    )
    # The declaration's value, however the template formats it (one line or one key per line).
    m = re.search(
        r"(" + re.escape(CHANNELS_LINE.rsplit(" = ", 1)[0]) + r" = )\{[^}]*\};", s
    )
    if not m:
        raise SystemExit(
            "scaffold: template config.ts no longer declares CHANNELS as expected; update scaffold.py with the template."
        )
    s = s[: m.start()] + m.group(1) + literal + ";" + s[m.end() :]

    if RELEASE_TAG_LINE not in s or RC_TAG_LINE not in s:
        raise SystemExit(
            "scaffold: template config.ts no longer has the expected RELEASE_TAG / RC_TAG lines; update scaffold.py with the template."
        )
    line = release_tag_line(probe.get("tags", []))
    if line:
        s = s.replace(RELEASE_TAG_LINE, line, 1)

    s, n = re.subn(
        r"(export const PRODUCT_RULES: \{ product: string; pattern: RegExp \}\[\] = \[\n)(.*?)(\];)",
        lambda m: m.group(1)
        + "  // Generated from the release file names; rename the products and merge families as you see fit.\n"
        + rules_ts
        + m.group(3),
        s,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit(
            "scaffold: PRODUCT_RULES block not found in template config.ts."
        )
    return s


def print_extensions(asset_names: list[str]) -> None:
    """The most common release-file extensions, metadata left out: a quick look at what the OS rules must cover."""

    def ext(n: str) -> str:
        m = EXT.search(n) or re.search(
            r"\.[a-z][a-z0-9]{0,6}$", n, re.I
        )  # not ".10" from a bare version
        return m.group(0).lower() if m else "(none)"

    exts = Counter(ext(n) for n in asset_names if not META.search(n))
    if not exts:
        return
    print(
        f"Release files by extension ({sum(exts.values())} install files, metadata left out): "
        + ", ".join(f"{e} {c}" for e, c in exts.most_common(8))
    )
    print("  check test/assets.test.ts output for the per-file OS each file gets")


def zone_of(host: str) -> str:
    """The registrable domain: the last two labels, or three under a ccTLD second level (example.co.uk)."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in CCTLD_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def write_wrangler(out: Path, worker: str, route: str | None, zone: str | None) -> None:
    p = out / "wrangler.toml"
    s = p.read_text(encoding="utf-8").replace("__WORKER_NAME__", worker)
    if route:
        block = (
            "# The dashboard's route. Make it more specific than any route already on the zone.\n"
            "# No workers.dev hostname: one hostname is one less thing for Access to cover.\n"
            f"routes = [{{ pattern = {json.dumps(route)}, zone_name = {json.dumps(zone)} }}]\nworkers_dev = false"
        )
    else:
        block = (
            "# Served on <name>.<account>.workers.dev. The Worker refuses every request until\n"
            "# Access is on: deploy, then Worker > Access > Protect this Worker (README step 3).\n"
            "workers_dev = true"
        )
    if "# __ROUTE_BLOCK__" not in s:
        raise SystemExit(
            "scaffold: template wrangler.toml no longer contains '# __ROUTE_BLOCK__'; update scaffold.py with the template."
        )
    p.write_text(s.replace("# __ROUTE_BLOCK__", block), encoding="utf-8")


ASSET_TEST = """import { describe, expect, it } from "vitest";
import { classifyAsset } from "../src/classify";
import names from "./fixtures/release-assets.json";

/**
 * Every file the repository has published must be classified: an install with a
 * product and an OS, or metadata (update feed, checksum, signature). A new file
 * name fails here until a rule in src/config.ts covers it, so installs are never
 * guessed. Files that truly have no OS (a universal archive) go in ACCEPTED_NO_OS.
 * test/fixtures/release-assets.json is the list probe_repo.py found.
 */
const ACCEPTED_NO_OS: string[] = [];
const files = names as string[];

describe("this repository's release files", () => {
  if (files.length === 0) it.skip("no release files published yet", () => {});
  for (const name of files) {
    it(name, () => {
      const c = classifyAsset(name);
      if (c.kind === "meta") return;
      expect(c.product, "add a PRODUCT_RULES entry in src/config.ts").not.toBe("Unclassified");
      if (!ACCEPTED_NO_OS.includes(name)) expect(c.os, "add an OS_RULES entry in src/config.ts, or list the file in ACCEPTED_NO_OS").not.toBe("Unclassified");
    });
  }
});
"""

TAGS_TEST = """import { describe, expect, it } from "vitest";
import { RELEASE_TAG } from "../src/config";
import tags from "./fixtures/release-tags.json";

/**
 * RELEASE_TAG picks the main product line out of the release tags. A pattern that
 * matches none of them leaves the Users page without releases. The tags are the
 * ones probe_repo.py found (test/fixtures/release-tags.json).
 */
const all = tags as string[];
const matching = all.filter((t) => RELEASE_TAG.test(t));

describe("this repository's release tags", () => {
  if (all.length === 0) it.skip("no release tags yet", () => {});
  else
    it(`RELEASE_TAG matches ${matching.length} of ${all.length} release tags`, () => {
      expect(matching.length, "RELEASE_TAG in src/config.ts matches none of the release tags").toBeGreaterThan(0);
    });
});
"""


def main() -> int:
    # Workflow names and warnings can hold emoji; a piped Windows console is cp1252 and would crash on them.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", required=True, help="report from probe_repo.py")
    ap.add_argument(
        "--out", required=True, help="new directory for the dashboard project"
    )
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--route",
        help='Cloudflare route, e.g. "example.org/dashboard*" (the zone must be on the account)',
    )
    where.add_argument(
        "--workers-dev",
        action="store_true",
        help="serve on the workers.dev hostname instead of a route",
    )
    ap.add_argument(
        "--zone",
        help="Cloudflare zone of the route (default: the route host's registrable domain)",
    )
    ap.add_argument("--name", help="display name (default: the repository name)")
    ap.add_argument(
        "--worker-name", help="Worker and D1 name (default: <repo>-dashboard)"
    )
    a = ap.parse_args()

    probe = json.loads(Path(a.probe).read_text(encoding="utf-8"))
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        print(f"scaffold: {out} is not empty; choose a new directory.", file=sys.stderr)
        return 2
    if a.zone and not a.route:
        print("scaffold: --zone applies to --route only.", file=sys.stderr)
        return 2
    base_path, zone = "/admin", None
    if a.route:
        if "/" not in a.route or not a.route.endswith("*"):
            print(
                'scaffold: --route must look like "example.org/dashboard*".',
                file=sys.stderr,
            )
            return 2
        base_path = "/" + a.route.split("/", 1)[1].rstrip("*").strip("/")
        if base_path == "/":
            print(
                "scaffold: give the route a path (example.org/dashboard*), not the whole site.",
                file=sys.stderr,
            )
            return 2
        host = a.route.split("/", 1)[0]
        zone = a.zone or zone_of(host)
        if not (host == zone or host.endswith("." + zone)):
            print(
                f"scaffold: route host {host} is not in zone {zone}.", file=sys.stderr
            )
            return 2
    worker = (
        a.worker_name
        or re.sub(r"[^a-z0-9-]+", "-", probe["name"].lower()).strip("-") + "-dashboard"
    )

    missing = [k for k in REQUIRED_PROBE_KEYS if k not in probe]
    if missing:
        print(
            f"scaffold: {a.probe} has no {', '.join(missing)}: it comes from an older probe_repo.py. Run the probe again.",
            file=sys.stderr,
        )
        return 2
    # Rendered before copying, so a template that has drifted from this script leaves no half-made project.
    config = render_config(
        (TEMPLATE / "src" / "config.ts").read_text(encoding="utf-8"),
        probe,
        a.name or probe["name"],
        base_path,
    )
    shutil.copytree(
        TEMPLATE,
        out,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".wrangler", ".dev.vars"),
    )
    (out / "src" / "config.ts").write_text(config, encoding="utf-8")
    write_wrangler(out, worker, a.route, zone)
    fixtures = out / "test" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "release-assets.json").write_text(
        json.dumps(probe.get("asset_names", []), indent=1), encoding="utf-8"
    )
    (fixtures / "release-tags.json").write_text(
        json.dumps(probe.get("tags", []), indent=1), encoding="utf-8"
    )
    (out / "test" / "assets.test.ts").write_text(ASSET_TEST, encoding="utf-8")
    (out / "test" / "tags.test.ts").write_text(TAGS_TEST, encoding="utf-8")
    (out / "scripts").mkdir(exist_ok=True)
    for f in COPIED_SCRIPTS:
        shutil.copy2(HERE / f, out / "scripts" / f)

    where_txt = (
        f"route {a.route} in zone {zone}" if a.route else "the workers.dev hostname"
    )
    print(
        f"Created {out} (Worker and database '{worker}', served at {base_path} on {where_txt})."
    )
    print_extensions(probe.get("asset_names", []))
    print("Next:")
    print(
        f"  cd {out} && npm install && npm test        # assets.test.ts lists any release file the rules miss"
    )
    print(
        "  edit src/config.ts: product names, TARGETS (references/metrics.md), BENCHMARKS"
    )
    print("  then follow README.md: run locally, deploy, sign-in, backfill")
    for w in probe.get("warnings", []):
        print(f"  probe warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
