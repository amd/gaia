# Agent UI Electron Tests

Tests for the Agent UI's Electron shell (`src/gaia/apps/webui/`): main-process services, packaging and installer readiness, and app structure.

```bash
cd tests/electron
npm install
npm test                                   # every Jest suite
npm test -- test_electron_chat_app.js      # one suite
npm run test:coverage                      # coverage report in coverage/

# node:test helpers that don't fit Jest's CommonJS mocking
node --test test_port_manager.mjs test_loadapp_query.mjs
```

- `setup.js` holds shared Jest setup; `mocks/` maps `electron` and `electron-updater` to test doubles.
- `.github/workflows/test_electron.yml` runs these on every PR that touches `src/gaia/apps/webui/` or `tests/electron/`.

The full guide, including CI jobs and debugging, is [Testing GAIA's Electron App](https://amd-gaia.ai/docs/deployment/testing-electron).

## License

Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
