# GAIA Copilot Instructions

Use this file as the Copilot-facing bridge to the repository's canonical guidance:

- `CLAUDE.md` — project conventions, coding rules, validation bar, docs policy
- `AGENTS.md` — multi-agent coordination and PR workflow rules
- `REVIEW.md` — PR review severity, nit budget, and review tone

When guidance conflicts, follow this order:

1. Direct user instructions
2. `CLAUDE.md`
3. `AGENTS.md`
4. `REVIEW.md`
5. Default Copilot behavior

## How to work in GAIA

- Write for humans first. Be concise, direct, and action-oriented.
- Keep changes scope-clean. Do not reformat or refactor unrelated files.
- Reuse existing GAIA patterns before inventing new ones, especially under `src/gaia/agents/base/` and `src/gaia/agents/registry.py`.
- Do not add silent fallbacks, swallowed exceptions, or quiet degradation paths. Fail loudly with actionable errors.
- Do not add AI attribution such as "Generated with Claude Code", "Written by AI", or `Co-Authored-By` trailers for AI tools.
- Prefer existing project commands and tooling over ad hoc scripts.

## Tests and validation

- New logic needs tests.
- New external surfaces need integration coverage.
- Do not ship placeholder skips such as `pytest.skip("not yet implemented")`.
- Run the repository's existing lint and test commands before calling work complete.
- Read back your changes and check for contradictions between docs, code, tests, and examples.

## Documentation rules

- User-visible or public-surface changes must update the docs that describe them.
- For GAIA docs, use the existing `docs/` structure and update `docs/docs.json` when new pages are added.
- If the same behavior is described in multiple bundled docs, update all of them in the same change.

## Pull requests and reviews

- Lead PR descriptions and review comments with the user-visible impact, not an implementation walkthrough.
- Keep PR descriptions short: why this matters, then a concrete test plan.
- Review correctness first. Do not bury real bugs under style nits.
- Keep review feedback high-signal and concise.

## Security and safety

- Never expose secrets, tokens, or credentials.
- If a public discussion appears to report a security issue, direct the reporter to GitHub Security Advisories instead of discussing exploit details in public.
- Tag the security maintainer on any security concerns raised in review or issue triage.
