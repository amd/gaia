# Electron Framework and Apps Testing

This directory contains automated tests for GAIA Electron applications that validate they will work correctly after dependency updates.

## Overview

The test suite validates multiple aspects to catch breakage from Dependabot updates:

### Structure Tests (`test_jira_app.js`, `test_example_app.js`)
- **App Configuration**: Validates app.config.json and package.json files
- **App Structure**: Ensures required files and directories exist  
- **Dependencies**: Verifies all required dependencies are present
- **Build Scripts**: Confirms npm scripts are properly configured

### Functional Tests (`test_functional.js`)  
- **Package Validity**: Ensures package.json dependencies have valid version formats
- **Entry Points**: Validates main files exist and have expected content
- **Version Consistency**: Checks Electron versions match across framework and apps
- **Service Modules**: Verifies framework services are present and loadable
- **Configuration Loading**: Tests that Forge configs can be loaded without errors

### Build Tests (GitHub Actions)
- **Dependency Installation**: Actually runs `npm ci` to verify dependencies install
- **Vulnerability Scanning**: Checks for high/critical security issues
- **App Packaging**: Attempts to package apps to catch build errors
- **Build Artifacts**: Verifies output files are created

## Why These Tests Matter for Dependabot

When Dependabot updates a package.json dependency:

1. **Structure tests** ensure the app still has all required files and configuration
2. **Functional tests** verify the package.json is valid and dependencies are compatible
3. **Build tests** actually install and build the apps to catch real runtime issues
4. **Vulnerability scans** alert on security problems introduced by updates

This three-layer approach catches different types of breakage:
- Structure tests run fast and catch obvious problems
- Functional tests validate internal consistency
- Build tests catch actual runtime/build failures

## Test Structure

```
tests/electron/
├── package.json               # Test dependencies and scripts
├── setup.js                   # Jest configuration and global test utilities
├── mocks/
│   └── electron.js           # Mock Electron APIs (for future unit tests)
├── test_jira_app.js          # Jira app structure tests (18 tests)
├── test_example_app.js       # Example app structure tests (11 tests)
└── test_functional.js        # Functional validation tests (11 tests)
```

**Total: 40 tests validating structure + functionality**

## Running Tests

### Prerequisites

```bash
# Install test dependencies
cd tests/electron
npm install
```

### Run All Tests

```bash
# From tests/electron directory
npm test
```

### Run with Coverage

```bash
# Coverage report
npm run test:coverage

# Watch mode for development
npm run test:watch
```

### Run Individual Test Files

```bash
# Run specific test file
npm test -- test_jira_app.js

# Run both app tests
npm test -- test_jira_app.js test_example_app.js
```

## GitHub Actions Integration

Tests run automatically on:
- Pull requests to `main` branch
- Pushes to `main` branch
- Changes to Electron framework or app files
- Dependency updates from Dependabot

### Workflow: `test_electron.yml`

The workflow includes multiple jobs:

1. **test-electron-framework**: Integration tests for Electron apps (configuration, structure, dependencies)
2. **test-apps-integration**: Same test suite run with different focus
3. **test-apps-build**: Verify app builds and packaging for each app
4. **dependency-audit**: Security audits for all packages

## Writing Tests

### Integration Tests

Integration tests verify file structure, configuration, and dependencies:

```javascript
const path = require('path');
const fs = require('fs');

describe('App Integration', () => {
  const appPath = path.join(__dirname, '../../src/gaia/apps/my-app/webui');
  
  it('should have valid configuration', () => {
    const configPath = path.join(appPath, 'app.config.json');
    expect(fs.existsSync(configPath)).toBe(true);
    
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    expect(config).toHaveProperty('name');
    expect(config).toHaveProperty('displayName');
  });

  it('should have required files', () => {
    expect(fs.existsSync(path.join(appPath, 'src/main.js'))).toBe(true);
    expect(fs.existsSync(path.join(appPath, 'package.json'))).toBe(true);
  });
});
```

### Future: Unit Tests

Unit tests for Electron framework components can be added using the provided mock Electron APIs in `mocks/electron.js`.

### Test Utilities

Global utilities are available via `global.testUtils`:

```javascript
// Create mock window
const mockWindow = global.testUtils.createMockWindow();

// Create mock config
const mockConfig = global.testUtils.createMockConfig({ name: 'my-app' });

// Wait for async operations
await global.testUtils.wait(100);

// Create mock MCP message
const message = global.testUtils.createMockMCPMessage('response', { data: 'test' });
```

## Coverage Requirements

Current coverage thresholds (configured in `package.json`):
- Branches: 50%
- Functions: 50%
- Lines: 50%
- Statements: 50%

To view coverage report:
```bash
npm run test:coverage
open coverage/lcov-report/index.html
```

## Debugging Tests

### Enable Debug Output

```bash
# Show console output during tests
DEBUG=true npm test

# Run Jest in debug mode
node --inspect-brk node_modules/.bin/jest --runInBand
```

### VS Code Debug Configuration

Add to `.vscode/launch.json`:

```json
{
  "type": "node",
  "request": "launch",
  "name": "Jest Current File",
  "program": "${workspaceFolder}/tests/electron/node_modules/.bin/jest",
  "args": [
    "${fileBasename}",
    "--config=${workspaceFolder}/tests/electron/package.json"
  ],
  "console": "integratedTerminal",
  "internalConsoleOptions": "neverOpen",
  "cwd": "${workspaceFolder}/tests/electron"
}
```

## Continuous Integration

### Dependabot Integration

Tests run automatically when Dependabot creates PRs for:
- Electron framework dependencies (`src/gaia/electron/package.json`)
- App dependencies (e.g., `src/gaia/apps/jira/webui/package.json`)

If tests pass, PRs can be automatically merged (requires workflow configuration).

### Manual Workflow Dispatch

Trigger tests manually via GitHub Actions:
1. Go to Actions → Test Electron Framework and Apps
2. Click "Run workflow"
3. Select branch and run

## Troubleshooting

### Common Issues

**Issue**: `Cannot find module 'electron'`
- **Solution**: Electron is mocked in tests. Ensure `moduleNameMapper` is configured in `package.json`

**Issue**: Tests fail with "No handler registered"
- **Solution**: Check that IPC handlers are properly mocked in test setup

**Issue**: Coverage is below threshold
- **Solution**: Add tests for uncovered code paths or adjust thresholds in `package.json`

### Getting Help

- Check existing test files for examples
- Review Jest documentation: https://jestjs.io/
- Review Electron testing guide: https://www.electronjs.org/docs/latest/tutorial/automated-testing

## Adding Tests for New Apps

When creating a new Electron app:

1. Create integration test file in `tests/apps/`
2. Add app to test matrix in `.github/workflows/test_electron.yml`
3. Verify configuration and structure
4. Test dependency versions
5. Validate npm scripts

Example:
```javascript
// tests/apps/test_my_app.js
describe('My App Integration', () => {
  const appPath = path.join(__dirname, '../../src/gaia/apps/my-app/webui');
  
  it('should have valid configuration', () => {
    // Test implementation
  });
});
```

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
