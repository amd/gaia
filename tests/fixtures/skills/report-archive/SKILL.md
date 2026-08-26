---
name: report-archive
description: Archive a generated report to the user's reports folder and register it with the team's report index. Use when the user asks to file, archive, or publish a finished report.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - filesystem:write:~/reports
      - network:write:reports.example.com
      - env:read
    requirements:
      python: ">=3.10"
      dependencies:
        - requests>=2.31.0
      env_vars:
        - REPORT_API_TOKEN
    tools:
      - name: archive_report
        description: Write a report to the archive folder and register it
        parameters:
          title: {type: string, required: true}
          markdown: {type: string, required: true}
        returns: {type: object}
      - name: read_archived_report
        description: Read a previously archived report back
        parameters:
          title: {type: string, required: true}
        returns: {type: object}
---

# Report Archive

Files a finished report in `~/reports/` and registers it with the team index so
it shows up for everyone else.

## When to use it

Use `archive_report` once the user is happy with a report and wants it kept.
Use `read_archived_report` to pull an earlier one back for reference.

## What it touches

- **Files** — reads and writes only under `~/reports/`. Nothing else.
- **Network** — one `POST` to the team report index, and only that host.
- **Secrets** — the index needs `REPORT_API_TOKEN`. Set it in the environment
  before the skill runs; the skill reads that one variable by name and never
  looks at anything else in the environment.

## Notes

Always tell the user where the file landed — never archive silently, and never
skip the confirmation the agent asks for before overwriting an existing report.
