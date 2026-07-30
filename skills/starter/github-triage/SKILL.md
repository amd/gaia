---
name: github-triage
description: Triage GitHub issues through the GitHub connector — read the backlog, group duplicates, judge severity, and draft the reply. Use when the user asks to triage issues, review a backlog, or work out what to fix first in a repository.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - mcp:connect:mcp-github
    requirements:
      env_vars:
        - GITHUB_TOKEN
    provenance:
      source: starter-pack
---

# GitHub Triage

The connector example. This skill declares one permission —
`mcp:connect:mcp-github` — which GAIA resolves to a real connector requirement.
Its tools do not come from the skill or from a mixin; they come from the GitHub
MCP server the connector starts.

## Setup

```bash
gaia connectors configure mcp-github --set GITHUB_TOKEN=<your-token>
gaia connectors list          # confirm mcp-github is configured
```

The token needs `repo` scope for private repositories; a read-only token is
enough for triage that stops short of commenting. GAIA stores it in the OS
keyring — never put it in this file.

Once configured, the GitHub MCP server's tools (issue search, issue read,
comment, label) are registered into the agent's tool registry. Use the names the
agent actually lists; they are defined by the MCP server, not by this skill.

## Procedure

1. **Pull the backlog.** Fetch open issues for the repository the user named,
   newest first. Ask for the repository if they did not say.
2. **Group before judging.** Cluster issues that describe the same underlying
   problem. Report the cluster, not each issue — a backlog of 40 is usually 12
   real problems.
3. **Judge each cluster on two axes:**
   - **Severity** — data loss > silent wrong answer > crash > annoyance.
   - **Reach** — everyone, one platform, or one reporter.
   Rank by the pair. A silent wrong answer affecting everyone outranks a crash
   affecting one person.
4. **Say what is missing.** For anything you cannot reproduce from the issue
   text, list the specific facts needed — version, platform, exact command.
   "Please provide more information" is not triage.
5. **Draft, do not send.** Produce the comment or label change and show it to the
   user. Never write to a repository without explicit confirmation for that
   specific action.

## Rules

- Never close an issue on your own judgement.
- Do not paste tokens, private URLs, or user emails into a public comment.
- If the repository is one you cannot read, say so — do not guess from the name.

## Fork this

Point it at your own repo's labels and severity ladder. The same shape works for
any MCP-server connector in `gaia connectors list`: declare
`mcp:connect:<connector-id>`, document the setup command, and write the
procedure against the tools that connector provides.
