# gaia-agent-email

Standalone GAIA agent — read, triage, organize, and reply to email through your
connected Google (Gmail) or personal Microsoft (Outlook.com) account. All email
content is processed locally on Lemonade — no cloud inference. Depends on the
published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-email              # from PyPI (once published)
pip install -e hub/agents/python/email    # editable, for development
```

Installing registers the `email` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically. The agent ships its own
REST surface (`gaia_agent_email.api_routes`) and MCP stdio server
(`gaia_agent_email.mcp_server`).

## Usage

```bash
gaia email "Triage my inbox"      # one-shot query
gaia email --interactive          # interactive session
gaia email --spec                 # write + open the REST endpoint spec
```

Requires the Google (or Microsoft) connector — connect it once with
`gaia connectors` so the agent is grant-checked for mailbox access.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/python/email/tests/ -x
```

## License

Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
