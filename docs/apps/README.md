<!--
Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# GAIA Apps Documentation

Documentation for GAIA desktop applications - Electron-based apps that provide rich user interfaces for GAIA's AI capabilities.

## What are GAIA Apps?

GAIA apps are Electron-based desktop applications built on the GAIA framework, combining modern web UIs with GAIA's AI agent capabilities via MCP integration.

## Documentation in this Directory

- [**App Development Guide**](dev.md) - Complete guide to building GAIA applications
- [**Jira App**](jira.md) - Natural language interface for Jira issue management

## Essential Context

- [**MCP Documentation**](../mcp.md) - Model Context Protocol integration (required for apps)
- [**Main Documentation**](../README.md) - Complete GAIA documentation index
- [**Project README**](../../README.md) - Project overview and setup

## Quick Architecture Overview

```
GAIA App
├── Electron Frontend (webui/)
├── GAIA Agent Backend (src/gaia/agents/)
└── MCP Bridge (WebSocket communication)
```

For detailed architecture and development instructions, see [dev.md](dev.md).

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
