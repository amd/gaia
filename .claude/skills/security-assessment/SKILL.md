---
name: "security-assessment"
description: "Assess a reported security vulnerability in GAIA and fill a PSIRT / JIRA triage: decide if it is valid & exploitable, whether it needs a CVE + bulletin, and produce the CVSS 4.0 score, CWE, and CVE description. Use when triaging a researcher report, a security advisory, or a claude-security-audit finding — anytime you must answer 'is this a CVE?' or produce a CVSS score/vector/severity. Always COMPUTES the score from a reviewed vector with util/cvss4.py (matches the FIRST 4.0 calculator); never guess the number — an LLM's guessed CVSS number is untrustworthy."
---

# Security Assessment (PSIRT / CVSS triage)

For scoring a reported GAIA vulnerability and filling the PSIRT triage template. The
one hard rule: **the CVSS number is arithmetic on a reviewed vector, never a guess.**

## Rule 0 — never guess a CVSS score. Compute it.

The *vector* is the human judgment call. The *number* is math. Pick the vector, then run:

```bash
python util/cvss4.py "CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:A/VC:L/VI:H/VA:H/SC:N/SI:N/SA:N"
# -> {"vector": "...", "base_score": 5.3, "severity": "Medium"}
```

`util/cvss4.py` is a thin, tested wrapper over the `cvss` pip package (in the `[dev]`
extra) whose 4.0 output matches https://www.first.org/cvss/calculator/4.0 exactly
(anchored in `tests/unit/test_cvss4.py`). A malformed vector is a loud `ValueError`,
never a silent 0.0.

**Why this rule exists (real GAIA cases):**
- An AI triage on the `find -exec` ticket asserted **"CVSS 6.9 Medium"** for the vector
  `CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N` — which actually
  computes to **8.4 High**.
- The security-audit workflow's prior anchor: a triage guessed **7.3** for a vector that
  scores **8.7**.

Guessed numbers are off by a full severity band. Always run the tool and quote its output.

## The GAIA vector rubric (which metric to pick)

This mirrors the rubric baked into `.github/workflows/claude-security-audit.yml` (keep the
two consistent). Full form:
`CVSS:4.0/AV:_/AC:_/AT:_/PR:_/UI:_/VC:_/VI:_/VA:_/SC:_/SI:_/SA:_`.

- **AV** — Network only if untrusted data crosses a network boundary to reach the sink
  (hub artifact, HTTP request). A local CLI/file input on the same host is **Local**.
- **AC** — Low unless a real, specific condition (not just "attacker must try") is needed.
- **AT** — Present if exploitation needs a precondition outside the attacker's control (a
  specific agent installed, victim points at a non-default hub); else None.
- **PR** — None unless GAIA privilege/auth is required to trigger it.
- **UI** — **This is where GAIA agent-tool findings usually turn.** Active if the user must
  click through a warning or **approve a confirmation-gated tool**
  (`TOOLS_REQUIRING_CONFIRMATION`); Passive if they must merely initiate a command/install;
  None only if fully automatic. Most `run_shell_command` / write-tool findings are **UI:A**
  because the operator sees and approves the literal command first.
- **VC** disclosure/reads · **VI** writes/tampering · **VA** crash/DoS. Set the **subsequent**
  scope metrics (SC/SI/SA) only if the impact escapes into a *separate* security scope (VM,
  container, downstream client) — an in-process capability gain does not.

**Incremental-impact check:** score the capability the finding *adds*, not what the tool
could already do. The `find -exec` bypass's novel gain is write/delete/exec (**VI/VA**);
file *reads* were already reachable via other whitelisted commands under the same path
controls, so its **VC is Low**, not High. Overstating VC:H is what pushed the AI vector to
a High score for a Medium-in-practice issue.

## The confirmation gate changes the answer to "is this a CVE?"

Many GAIA agent tools are gated behind explicit per-command user approval
(`TOOLS_REQUIRING_CONFIRMATION` in `src/gaia/agents/base/agent.py`). When the user sees and
approves the literal command before it runs, that human-approval step is the real security
boundary. A bypass of a *secondary* control (e.g. an incomplete command allowlist) layered
on top of an intact primary control is **defense-in-depth hardening, not a standalone
exploitable vulnerability** — it typically does **not** warrant a CVE + public bulletin.

Check before deciding:
- Is the tool in `TOOLS_REQUIRING_CONFIRMATION`? (grep it.)
- Does it get denied in non-interactive mode, or only run under an opt-in like
  `GAIA_AUTO_APPROVE_TOOLS=1`? The opt-in is a documented, user-accepted risk.
- Is there a code path (API server, programmatic SDK) that skips the gate? If yes, the gate
  is not universal and the CVE bar may be met after all — verify, don't assume.

Fix it regardless (defense-in-depth), but let the gate drive the CVE decision.

## CWE — name the root cause, not the impact

Rank the **root cause** first, the consequence second. For the `find -exec` bypass:
**CWE-184 (Incomplete List of Disallowed Inputs)** is the root cause (the allowlist check is
incomplete); CWE-78 (OS Command Injection) is only the *consequence*. An AI triage that leads
with CWE-78 has described the symptom, not the defect.

## Filling the PSIRT / JIRA triage

Answer in this order (the template PSIRT sends):

1. **Valid, exploitable vulnerability? Yes/No** — be honest about the bypass even if a gate
   limits it; qualify exploitability rather than denying the weakness.
2. **Needs a CVE ID + public Security Bulletin? Yes/No** — apply the confirmation-gate test
   above. If No, state whether a **security brief** is required (usually optional).
3. **If new CVE:** CVSS 4.0 (Score + Vector — from `util/cvss4.py`, not guessed), CWE
   (root-cause first), and CVE description in the required shape:
   `<Weakness> in <component> could allow <attacker> to <exploit> potentially resulting in <CIA>`.
4. **Mitigation delivery / AMD deliverables** — for a GAIA code fix this is the `amd-gaia`
   package (PyPI + GitHub) only; **None** for PI / SEV FW / uCode / ROCm / Radeon / Adrenalin
   / uProf / Chipset drivers. Not a Linux-upstream issue.

Then move the JIRA ticket **Opened → Assessed**.

## Worked example — the `find -exec` triage (2026-07)

| Vector | `util/cvss4.py` says | Note |
|---|---|---|
| `CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N` | **8.4 High** | the AI's vector — it *claimed* 6.9 |
| `CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:A/VC:L/VI:H/VA:H/SC:N/SI:N/SA:N` | **5.3 Medium** | corrected: UI:A (approval gate), VC:L (reads already possible), VA:H (`-delete`) |

Both rows are complete vectors — paste either straight into `util/cvss4.py`.

Outcome: valid weakness, **No CVE** (confirmation gate is the real boundary), fixed as
hardening in PR #2740. The corrected vector — computed, not guessed — lands a full band below
the AI's assertion.

## Writing it up

The triage you hand back follows [CLAUDE.md → How You
Communicate](../../../CLAUDE.md#how-you-communicate): open with the verdict in plain words —
is it real, is it exploitable, does it need a CVE — then the CVSS vector, CWE, and evidence
underneath. A reviewer deciding whether to file should not have to parse the vector string to
learn the answer. State the computed score, never a guessed one.
