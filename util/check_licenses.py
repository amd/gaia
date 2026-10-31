#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail if GAIA ships any copyleft-licensed dependency.

GAIA must not ship AGPL, GPL or LGPL code (or other strong/network copyleft).
This scans the three ecosystems GAIA releases:

    python util/check_licenses.py python                     # the current interpreter's packages
    python util/check_licenses.py npm path/to/package-lock.json [...]
    python util/check_licenses.py go path/to/go-module-dir [...]

Every component must resolve to an allowed licence. A copyleft licence, or one
that cannot be classified, fails the run; reviewed exceptions live in
``REVIEWED`` with the reason they are acceptable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Copyleft families GAIA must not ship. Matched against SPDX ids and free text.
DENIED_PATTERNS = [
    (re.compile(r"\bAGPL|AFFERO", re.I), "AGPL"),
    (re.compile(r"\bLGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC", re.I), "LGPL"),
    (re.compile(r"\bGPL|GENERAL PUBLIC LICEN", re.I), "GPL"),
    (re.compile(r"\bSSPL|SERVER SIDE PUBLIC", re.I), "SSPL"),
    (re.compile(r"\bEUPL", re.I), "EUPL"),
    (re.compile(r"\bOSL-", re.I), "OSL"),
    (re.compile(r"\bCC-BY-SA|CC-BY-NC|ShareAlike|NonCommercial", re.I), "CC share-alike/non-commercial"),
]

# Licences GAIA may ship. MPL-2.0 is file-level copyleft: unmodified use is allowed.
ALLOWED_IDS = {
    "0BSD", "AFL-2.1", "Apache-2.0", "Artistic-2.0", "BlueOak-1.0.0", "BSD-1-Clause", "BSD-2-Clause",
    "BSD-3-Clause", "BSD-3-Clause-Clear", "BSL-1.0", "bzip2-1.0.6", "CC-BY-3.0", "CC-BY-4.0", "CC0-1.0",
    "CNRI-Python", "HPND", "ISC", "MIT", "MIT-0", "MIT-CMU", "MPL-2.0", "NCSA", "OFL-1.1", "PSF-2.0",
    "Python-2.0", "Python-2.0.1", "Unicode-3.0", "Unicode-DFS-2016", "Unlicense", "W3C", "WTFPL", "X11",
    "Zlib", "ZPL-2.1",
}

# Free-text licence strings and classifiers mapped to SPDX ids.
TEXT_ALIASES = {
    "mit": "MIT", "mit license": "MIT", "the mit license": "MIT", "mit licence": "MIT", "expat": "MIT",
    "bsd": "BSD-3-Clause", "bsd license": "BSD-3-Clause", "new bsd license": "BSD-3-Clause",
    "bsd-3": "BSD-3-Clause", "3-clause bsd": "BSD-3-Clause", "bsd 3-clause": "BSD-3-Clause",
    "simplified bsd": "BSD-2-Clause", "bsd-2": "BSD-2-Clause",
    "apache 2.0": "Apache-2.0", "apache-2": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0", "apache license, version 2.0": "Apache-2.0", "apache2": "Apache-2.0",
    "apache 2": "Apache-2.0", "apache": "Apache-2.0", "apache 2.0 license": "Apache-2.0",
    "isc license": "ISC", "isc license (iscl)": "ISC",
    "psf": "PSF-2.0", "python software foundation license": "PSF-2.0", "psf license": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0", "mpl 2.0": "MPL-2.0", "mpl-2": "MPL-2.0",
    "the unlicense (unlicense)": "Unlicense", "public domain": "Unlicense", "zlib/libpng license": "Zlib",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    "boost software license 1.0 (bsl-1.0)": "BSL-1.0", "mit no attribution license (mit-0)": "MIT-0",
}

# Components whose metadata cannot be classified automatically, reviewed by hand.
# Key: (ecosystem, name). Value: (versions reviewed as [min, max), SPDX licence, why).
# A release outside the reviewed range is unclassified again: licences change.
REVIEWED: dict[tuple[str, str], tuple[tuple[str, str], str, str]] = {
    ("python", "matplotlib"): (("3.0", "4.0"), "PSF-2.0", "Matplotlib licence is PSF-based (LICENSE/LICENSE in the wheel)"),
    ("python", "python-dateutil"): (
        ("2.8", "4.0"),
        "Apache-2.0 AND BSD-3-Clause",
        "metadata says 'Dual License'; LICENSE file is Apache-2.0 plus BSD-3-Clause",
    ),
    ("python", "kiwisolver"): (("1.0", "2.0"), "BSD-3-Clause", "metadata is the licence text; BSD classifier"),
    ("python", "reportlab"): (("4.0", "6.0"), "BSD-3-Clause", "metadata says 'BSD license (see license.txt)'"),
    ("python", "cycler"): (("0.10", "1.0"), "BSD-3-Clause", "metadata is a copyright line; BSD classifier"),
    ("python", "sherpa-onnx"): (
        ("1.13", "3.0"),
        "Apache-2.0",
        "metadata says 'Apache licensed, as found in the LICENSE file'",
    ),
    ("python", "pypdfium2"): (
        ("5.0", "6.0"),
        "Apache-2.0 AND BSD-3-Clause",
        "metadata adds 'dependency licenses'; the wheel's 15 bundled PDFium licences are permissive "
        "(FreeType under FTL; ICU's GPL text covers only an Autoconf build script)",
    ),
}

# Installer tooling present in every venv that never ships inside GAIA. PyInstaller is
# deliberately NOT here: the frozen agent binaries bundle parts of it.
BUILD_ONLY = {("python", "pip"), ("python", "wheel")}


@dataclass
class Finding:
    ecosystem: str
    name: str
    version: str
    licence: str
    verdict: str  # "ok" | "denied" | "unknown"
    detail: str = ""


def _denied_family(text: str) -> str | None:
    for pattern, family in DENIED_PATTERNS:
        if pattern.search(text):
            return family
    return None


def _normalise(term: str) -> str | None:
    """Map an SPDX id or a known free-text name to an allowed SPDX id, else None."""
    term = term.strip().strip("()").strip()
    if term in ALLOWED_IDS:
        return term
    alias = TEXT_ALIASES.get(term.lower().rstrip("."))
    if alias:
        return alias
    for allowed in ALLOWED_IDS:
        if term.lower() == allowed.lower():
            return allowed
    return None


_SPDX_TOKEN = re.compile(r"(\(|\)|[A-Za-z0-9.+\-:]+)")
_OPERATORS = {"AND", "OR", "WITH"}


def _looks_like_spdx_id(token: str) -> bool:
    return bool(
        re.fullmatch(r"LicenseRef-[A-Za-z0-9.\-]+|[A-Za-z][A-Za-z0-9.+\-]*\d[A-Za-z0-9.+\-]*|[A-Z0-9\-]{2,}", token)
    )


def _tokenise_spdx(text: str) -> list[str] | None:
    """Split an SPDX expression into tokens, or return None if it is free text."""
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _SPDX_TOKEN.match(text, pos)
        if not m:
            return None
        tokens.append(m.group(1))
        pos = m.end()
    # Exception names after WITH (e.g. LLVM-exception) are not licence ids.
    operands = [
        t for i, t in enumerate(tokens)
        if t not in _OPERATORS and t not in ("(", ")") and not (i and tokens[i - 1] == "WITH")
    ]
    # Free text such as "MIT License" or "BSD license (see license.txt)" is not an expression.
    if not operands or any(_normalise(t) is None and not _looks_like_spdx_id(t) for t in operands):
        return None
    return tokens


class _Parser:
    """Recursive descent over SPDX: parentheses bind tightest, then WITH, then AND, then OR."""

    def __init__(self, tokens: list[str]):
        self.tokens, self.pos = tokens, 0

    def parse(self):
        node = self._or()
        if self.pos != len(self.tokens):
            raise ValueError(f"unexpected {self.tokens[self.pos]!r}")
        return node

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return tok

    def _or(self):
        node = self._and()
        while self._peek() == "OR":
            self._take()
            node = ("OR", node, self._and())
        return node

    def _and(self):
        node = self._with()
        while self._peek() == "AND":
            self._take()
            node = ("AND", node, self._with())
        return node

    def _with(self):
        node = self._atom()
        if self._peek() == "WITH":
            self._take()
            node = ("WITH", node, self._take())
        return node

    def _atom(self):
        tok = self._take()
        if tok == "(":
            node = self._or()
            if self._take() != ")":
                raise ValueError("missing ')'")
            return node
        if tok in _OPERATORS or tok == ")":
            raise ValueError(f"unexpected {tok!r}")
        return tok


def _evaluate(node) -> tuple[str, str]:
    if isinstance(node, str):
        normalised = _normalise(node)
        if normalised:
            return "ok", normalised
        family = _denied_family(node)
        return ("denied", f"{node} ({family})") if family else ("unknown", node)
    op, left, right = node
    if op == "WITH":
        # An exception never turns a copyleft licence into a permissive one.
        verdict, detail = _evaluate(left)
        return verdict, f"{detail} WITH {right}"
    lv, ld = _evaluate(left)
    rv, rd = _evaluate(right)
    if op == "AND":
        if lv == rv == "ok":
            return "ok", f"{ld} AND {rd}"
        if "denied" in (lv, rv):
            return "denied", "; ".join(d for v, d in ((lv, ld), (rv, rd)) if v == "denied")
        return "unknown", "; ".join(d for v, d in ((lv, ld), (rv, rd)) if v != "ok")
    # OR: GAIA may take any permissive alternative.
    if lv == "ok":
        return "ok", ld
    if rv == "ok":
        return "ok", rd
    if "unknown" in (lv, rv):
        return "unknown", f"{ld}; {rd}"
    return "denied", f"{ld}; {rd}"


def classify(licence: str) -> tuple[str, str]:
    """Classify a licence expression or free-text string.

    Returns ``(verdict, detail)`` where verdict is ``ok``, ``denied`` or ``unknown``.
    SPDX expressions are parsed with proper precedence: an ``OR`` is allowed if any
    alternative is, an ``AND`` needs every part, and ``WITH`` keeps its licence's
    verdict. Free text is stricter: naming any copyleft family denies it, and only a
    known whole-string alias or a list of known licences passes.
    """
    text = (licence or "").strip()
    if not text:
        return "unknown", "no licence metadata"
    tokens = _tokenise_spdx(text)
    if tokens is not None:
        try:
            return _evaluate(_Parser(tokens).parse())
        except ValueError as e:
            return "unknown", f"unparseable licence expression {text!r}: {e}"
    family = _denied_family(text)
    if family:
        return "denied", f"free text names {family}: {text!r}"
    whole = _normalise(text)
    if whole:
        return "ok", whole
    parts = [p.strip() for p in re.split(r"\s*[;,]\s*", text) if p.strip()]
    mapped = [_normalise(p) for p in parts]
    if len(parts) > 1 and all(mapped):
        return "ok", " AND ".join(mapped)
    return "unknown", f"unrecognised licence text {text!r}"


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (0,)


def _classify_component(ecosystem: str, name: str, version: str, licence: str, extra: list[str] | None = None) -> Finding:
    key = (ecosystem, name.lower())
    classifiers = [c.split(" :: ")[-1] for c in (extra or [])]
    copyleft_classifiers = [c for c in classifiers if _denied_family(c)]
    if key in REVIEWED:
        (low, high), spdx, why = REVIEWED[key]
        if not _version_tuple(low) <= _version_tuple(version) < _version_tuple(high):
            return Finding(
                ecosystem, name, version, licence, "unknown",
                f"REVIEWED covers {low} <= version < {high}; re-check the licence of {version} and update the entry",
            )
        verdict, detail = classify(spdx)
        return Finding(ecosystem, name, version, spdx, verdict, f"reviewed: {why}" if verdict == "ok" else detail)
    verdict, detail = classify(licence)
    # A copyleft classifier counts unless the declared expression offers that family as an OR alternative.
    offered_as_alternative = verdict == "ok" and " OR " in licence and _denied_family(licence)
    if copyleft_classifiers and not offered_as_alternative:
        return Finding(ecosystem, name, version, "; ".join(classifiers), "denied", f"classifier: {copyleft_classifiers[0]}")
    if verdict == "unknown" and classifiers:
        mapped = [_normalise(c) for c in classifiers]
        if all(mapped):
            return Finding(ecosystem, name, version, " AND ".join(mapped), "ok", "from classifiers")
    return Finding(ecosystem, name, version, licence, verdict, detail)


def scan_python() -> list[Finding]:
    from importlib import metadata

    findings = []
    seen = set()
    for dist in metadata.distributions():
        name = dist.metadata["Name"] or ""
        key = name.lower().replace("_", "-")
        if not name or key in seen or ("python", key) in BUILD_ONLY:
            continue
        seen.add(key)
        meta = dist.metadata
        expression = meta.get("License-Expression") or ""
        raw = meta.get("License") or ""
        # Long License fields are the full licence text; keep the first line only.
        licence = expression or (raw.strip().splitlines()[0] if raw.strip() and len(raw) < 200 else "")
        classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
        classifiers = [c for c in classifiers if c != "License :: OSI Approved"]
        if not licence and raw and len(raw) >= 200 and ("python", key) not in REVIEWED:
            # The field holds the full licence text: identify it the same way as a LICENSE file.
            licence = _licence_from_text(raw)
            if not licence:
                findings.append(Finding("python", key, dist.version, raw.strip().splitlines()[0], "unknown", "unrecognised licence text"))
                continue
        findings.append(_classify_component("python", key, dist.version, licence, classifiers))
    return findings


def scan_npm(lockfile: Path, production_only: bool = False) -> list[Finding]:
    """Scan a lockfile. ``production_only`` skips dev entries: only for packages that ship
    their runtime dependencies as-is (a bundler pulls devDependencies into the output)."""
    packages = json.loads(lockfile.read_text(encoding="utf-8")).get("packages", {})
    findings = []
    for path, info in packages.items():
        if not path or (production_only and (info.get("dev") or info.get("devOptional"))):
            continue
        name = path.split("node_modules/")[-1]
        licence = info.get("license") or ""
        if isinstance(licence, dict):
            licence = licence.get("type", "")
        findings.append(_classify_component("npm", name, info.get("version", ""), licence))
    return findings


def _licence_from_text(text: str) -> str:
    """Identify a full licence text (LICENSE file or long metadata field)."""
    flat = " ".join(text.split())[:6000]
    family = _denied_family(flat[:600])
    if family:
        return family
    if "Permission is hereby granted, free of charge" in flat:
        return "MIT"
    if "Apache License" in flat and "Version 2.0" in flat:
        return "Apache-2.0"
    if "Redistribution and use" in flat:
        return "BSD-3-Clause" if ("Neither the name" in flat or "names of its" in flat) else "BSD-2-Clause"
    if "Mozilla Public License" in flat and "2.0" in flat:
        return "MPL-2.0"
    return ""


def scan_go(module_dir: Path) -> list[Finding]:
    # Platform-specific modules (e.g. Windows console input) only appear for their GOOS.
    out = ""
    for goos in ("linux", "windows", "darwin"):
        out += subprocess.run(
            ["go", "list", "-deps", "-f", "{{with .Module}}{{.Path}}\t{{.Version}}\t{{.Dir}}{{end}}", "./..."],
            cwd=module_dir, capture_output=True, text=True, check=True, env={**os.environ, "GOOS": goos},
        ).stdout
    findings, seen = [], set()
    main_module = subprocess.run(["go", "list", "-m"], cwd=module_dir, capture_output=True, text=True, check=True).stdout.strip()
    for line in out.splitlines():
        if not line or line in seen:
            continue
        seen.add(line)
        path, version, directory = (line.split("\t") + ["", ""])[:3]
        if path == main_module:
            continue
        licence = ""
        d = Path(directory)
        files = sorted(p for p in d.glob("*") if re.match(r"(?i)(licen[cs]e|copying)", p.name)) if directory else []
        if files:
            licence = _licence_from_text(files[0].read_text(encoding="utf-8", errors="replace"))
        elif (d / "README.md").exists():
            readme = (d / "README.md").read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?im)^#+\s*licen[cs]e\s*\n+\s*([A-Za-z0-9.\- ]+)", readme)
            licence = m.group(1).strip() if m else ""
        findings.append(_classify_component("go", path, version, licence))
    return findings


def report(findings: list[Finding]) -> int:
    if not findings:
        print(
            "No components found to scan. The install step probably failed or pointed at the wrong "
            "environment; a licence check over nothing proves nothing. Check the install output above."
        )
        return 2
    bad = [f for f in findings if f.verdict != "ok"]
    print(f"Scanned {len(findings)} components: {len(findings) - len(bad)} ok, "
          f"{sum(f.verdict == 'denied' for f in bad)} copyleft, {sum(f.verdict == 'unknown' for f in bad)} unclassified.")
    for f in sorted(bad, key=lambda x: (x.verdict, x.ecosystem, x.name)):
        print(f"  {f.verdict.upper():8} {f.ecosystem}:{f.name} {f.version}  licence={f.licence!r}  ({f.detail})")
    if bad:
        print(
            "\nGAIA must not ship AGPL/GPL/LGPL or unclassified licences. Replace the dependency, or if the "
            "licence is actually permissive, add it to REVIEWED in util/check_licenses.py with the evidence. "
            "Policy: REVIEW.md (Licences)."
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="ecosystem", required=True)
    sub.add_parser("python", help="scan the current interpreter's installed distributions")
    npm = sub.add_parser("npm", help="scan package-lock.json files")
    npm.add_argument("lockfiles", nargs="*", type=Path, help="lockfiles scanned in full, dev entries included")
    npm.add_argument(
        "--production-only", action="append", type=Path, default=[], metavar="LOCKFILE",
        help="lockfile whose dev entries never ship (no bundler); repeatable",
    )
    go = sub.add_parser("go", help="scan Go module directories")
    go.add_argument("dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.ecosystem == "python":
        findings = scan_python()
    elif args.ecosystem == "npm":
        findings = [f for lock in args.lockfiles for f in scan_npm(lock)]
        findings += [f for lock in args.production_only for f in scan_npm(lock, production_only=True)]
    else:
        findings = [f for d in args.dirs for f in scan_go(d)]
    return report(findings)


if __name__ == "__main__":
    sys.exit(main())
