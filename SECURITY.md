# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.** Public issues disclose
the exploit before a fix exists.

Instead, report privately through **[GitHub Security Advisories](https://github.com/amd/gaia/security/advisories/new)**
("Report a vulnerability"). This creates a private channel with the maintainers and
carries first-class CVSS and CWE fields. Please include:

- affected version / commit,
- a description and, if possible, a minimal proof of concept,
- the impact you observed (what an attacker gains).

For AMD product-security process, findings are tracked by AMD PSIRT; a maintainer
(@kovtcharov-amd) will route the advisory. We aim to acknowledge within a few
business days.

## Severity

We score vulnerabilities with **CVSS 4.0** (the [FIRST v4 calculator](https://www.first.org/cvss/calculator/4.0)).
The base score comes from the reviewed vector; the vector itself (attack vector,
required precondition, user interaction, impact) is a judgment call confirmed by a
maintainer before disclosure.

## Automated security review

Two proactive checks guard the tree, in addition to reactive PR review:

- **Suppression gate** (`util/check_security_gates.py`, run by `util/lint.py`):
  every `# noqa: S<n>` / `# nosec` must be justified in `.security-suppressions.json`,
  so no security finding is silenced without human review.
- **Security audit** (`.github/workflows/claude-security-audit.yml`): a scheduled
  deterministic (semgrep) + reasoning (Claude taint/authz/suppression) sweep whose
  findings land in the repo's private **Security → Code scanning** tab, scored with
  CVSS 4.0. It never posts findings to a public issue.

## Scope

In scope: the `gaia` core (`src/gaia/`), the hub agents (`hub/agents/`), the hub
installer, the API/UI servers, and the MCP surface. Out of scope: issues requiring a
already-compromised host, and vulnerabilities in third-party dependencies (report
those upstream; we track them via Dependabot).
