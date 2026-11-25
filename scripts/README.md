<!--
Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Scripts

Scripts for running GAIA from a checkout and for the scheduled Claude audits. Repo
checks, release tooling and eval helpers live in [`util/`](../util/README.md).

| Path | What it is |
|---|---|
| `dev/` | Run the terminal UI against this checkout (`run-tui.sh`, `run-tui.bat`); `bin/gaia-agent` starts the Python harness from source |
| `audit/` | The nightly code, security and docs audits: rotation, deduplication and issue filing |
| `cloud_bootstrap.sh` | Creates the editable install when a Claude Code cloud session starts |
| `test-llm-gateway.ps1`, `diagnose-gateway-auth.ps1` | Hand-run checks of the AMD LLM gateway connection |
| `video-demo/` | How to upload documentation videos to Cloudflare R2 |
