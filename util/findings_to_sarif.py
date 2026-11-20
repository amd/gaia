# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Convert Claude security-audit findings into SARIF 2.1.0 for GitHub code scanning.

The security-audit workflow writes findings as JSON; this renders them as SARIF so
they upload to the repo's **private** Security > Code scanning tab (not a public
issue), where GitHub deduplicates them and shows a CVSS-derived severity.

Two properties matter for that pipeline:

* ``security-severity`` on each rule — GitHub maps the CVSS base score to the alert
  severity (>=9.0 critical, 7.0-8.9 high, 4.0-6.9 medium, <4.0 low). The score is
  computed here from the finding's reviewed CVSS 4.0 vector via ``util/cvss4.py`` —
  never an LLM's guessed number.
* ``partialFingerprints`` from the finding's stable ``dedup_key`` (path + symbol,
  never a line number) — so an alert tracks the same weakness across runs instead
  of re-firing every time a line moves.

Finding schema (one object per weakness)::

    {"findings": [{
        "path": "src/gaia/hub/installer.py",
        "line": 382,                       # optional
        "symbol": "_install_cpp_artifact", # for the dedup_key / message
        "cwe": "CWE-22",                   # optional
        "title": "...", "why": "...", "evidence": "...",
        "remediation": "...",              # optional
        "confidence": "high",              # optional
        "cvss_vector": "CVSS:4.0/AV:N/.../SA:N",
        "dedup_key": "security:src/gaia/hub/installer.py:_install_cpp_artifact"
    }]}

Code scanning treats each upload as the complete result set for its category, so a
weakness one night's sampled audit doesn't re-report would be closed as "fixed".
``--carry-forward`` takes the category's open alerts (JSON lines, one alert per line,
from the code-scanning alerts API) and re-emits each one the current run didn't
report, unless its file is gone from the checkout.

Usage:
    python util/findings_to_sarif.py findings-*.json -o security.sarif \
        [--carry-forward open-alerts.jsonl --repo-root .]
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    from cvss4 import score as cvss_score
except ImportError:  # pragma: no cover - resolved when run from util/ or tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cvss4 import score as cvss_score

REQUIRED_FIELDS = ("path", "title", "cvss_vector", "dedup_key")
FINGERPRINT_KEY = "gaiaSecurityAudit/v1"
TOOL_NAME = "gaia-claude-security-audit"
TOOL_URI = (
    "https://github.com/amd/gaia/blob/main/.github/workflows/claude-security-audit.yml"
)


def _level_for(severity: str) -> str:
    """SARIF level from CVSS qualitative severity."""
    return {
        "Critical": "error",
        "High": "error",
        "Medium": "warning",
        "Low": "note",
        "None": "note",
    }.get(severity, "warning")


def load_findings(paths: List[str]) -> List[Dict[str, Any]]:
    """Read and concatenate the ``findings`` arrays from one or more JSON files."""
    findings: List[Dict[str, Any]] = []
    for pattern in paths:
        for fname in sorted(glob.glob(pattern)) or [pattern]:
            p = Path(fname)
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            findings.extend(data.get("findings", []))
    return findings


def _validate(finding: Dict[str, Any]) -> None:
    missing = [f for f in REQUIRED_FIELDS if not finding.get(f)]
    if missing:
        raise ValueError(
            f"Finding {finding.get('dedup_key') or finding.get('title') or finding!r} "
            f"is missing required field(s): {', '.join(missing)}."
        )


def to_sarif(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Render findings as a SARIF 2.1.0 document (one rule + result per finding)."""
    rules: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    seen_rule_ids: set[str] = set()

    for f in findings:
        _validate(f)
        scored = cvss_score(f["cvss_vector"])
        rule_id = f["dedup_key"]

        if rule_id not in seen_rule_ids:
            seen_rule_ids.add(rule_id)
            rules.append(
                {
                    "id": rule_id,
                    "name": (f.get("cwe") or "SecurityFinding").replace("-", ""),
                    "shortDescription": {"text": f["title"]},
                    "properties": {
                        # GitHub reads this to set the alert's severity.
                        "security-severity": str(scored["base_score"]),
                        "cwe": f.get("cwe", ""),
                        "tags": ["security"] + ([f["cwe"]] if f.get("cwe") else []),
                    },
                }
            )

        text = f["title"]
        for label in ("why", "evidence", "remediation"):
            if f.get(label):
                text += f"\n\n{label.capitalize()}: {f[label]}"
        text += (
            f"\n\nCVSS 4.0: {scored['base_score']} ({scored['severity']}) "
            f"[{scored['vector']}] — proposed, confirm the vector before disclosure."
        )

        region: Dict[str, Any] = {}
        if f.get("line"):
            region["startLine"] = int(f["line"])

        results.append(
            {
                "ruleId": rule_id,
                "level": _level_for(scored["severity"]),
                "message": {"text": text},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f["path"]},
                            **({"region": region} if region else {}),
                        }
                    }
                ],
                "partialFingerprints": {FINGERPRINT_KEY: rule_id},
                "properties": {
                    "security-severity": str(scored["base_score"]),
                    "cvss_vector": scored["vector"],
                    "confidence": f.get("confidence", ""),
                },
            }
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": TOOL_URI,
                        "version": "1.0.0",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


_CVSS_SCORE_RE = re.compile(r"CVSS 4\.0: (\d+(?:\.\d+)?) ")


def load_alerts(path: str) -> List[Dict[str, Any]]:
    """Read code-scanning alerts written as a stream of JSON objects (``gh --jq``)."""
    text = Path(path).read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    alerts: List[Dict[str, Any]] = []
    pos = 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            return alerts
        alert, pos = decoder.raw_decode(text, pos)
        alerts.append(alert)


def carry_forward(
    sarif: Dict[str, Any], alerts: List[Dict[str, Any]], repo_root: Path
) -> int:
    """Re-emit open alerts this run didn't report so the upload doesn't close them.

    An alert is dropped only when its file no longer exists. Two lenses can file the
    same ``dedup_key`` as separate alerts; each is carried. Returns how many were
    carried forward.
    """
    run = sarif["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    reported = {r["ruleId"] for r in run["results"]}
    rule_ids = {r["id"] for r in rules}
    carried = 0

    for alert in alerts:
        rule = alert["rule"]
        rule_id = rule["id"]
        instance = alert["most_recent_instance"]
        location = instance["location"]
        path = location["path"]
        if rule_id in reported or not (repo_root / path).exists():
            continue

        text = instance["message"]["text"]
        match = _CVSS_SCORE_RE.search(text)
        if not match:
            raise ValueError(
                f"Open alert #{alert.get('number')} ({rule_id}) has no 'CVSS 4.0: <score>' "
                "in its message, so its severity can't be carried forward. Dismiss or "
                "fix the alert in Security > Code scanning, or update this parser."
            )
        severity = match.group(1)

        if rule_id not in rule_ids:
            rule_ids.add(rule_id)
            rules.append(
                {
                    "id": rule_id,
                    "name": rule.get("name") or "SecurityFinding",
                    "shortDescription": {"text": rule.get("description") or rule_id},
                    "properties": {
                        "security-severity": severity,
                        "tags": rule.get("tags") or ["security"],
                    },
                }
            )

        region: Dict[str, Any] = {}
        if location.get("start_line"):
            region["startLine"] = int(location["start_line"])
        run["results"].append(
            {
                "ruleId": rule_id,
                "level": rule.get("severity") or "warning",
                "message": {"text": text},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": path},
                            **({"region": region} if region else {}),
                        }
                    }
                ],
                "partialFingerprints": {FINGERPRINT_KEY: rule_id},
                "properties": {"security-severity": severity, "carried_forward": True},
            }
        )
        carried += 1

    return carried


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert findings JSON to SARIF.")
    parser.add_argument("findings", nargs="+", help="findings JSON file(s) or globs")
    parser.add_argument("-o", "--output", help="write SARIF here (default: stdout)")
    parser.add_argument(
        "--carry-forward",
        metavar="ALERTS_JSONL",
        help="open code-scanning alerts (one JSON object per line) to keep open",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="checkout used to tell whether a carried alert's file still exists",
    )
    args = parser.parse_args(argv)

    findings = load_findings(args.findings)
    try:
        sarif = to_sarif(findings)
        if args.carry_forward:
            carried = carry_forward(
                sarif, load_alerts(args.carry_forward), Path(args.repo_root)
            )
            print(f"Carried forward {carried} open alert(s)", file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    out = json.dumps(sarif, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {len(sarif['runs'][0]['results'])} result(s) to {args.output}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
