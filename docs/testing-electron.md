# Testing Guide for GAIA Electron Apps

This guide explains how to test GAIA Electron applications and ensure they work correctly with dependency updates.

## Overview

GAIA uses automated testing to validate Electron framework and applications:
- **Unit Tests**: Test individual components in isolation
- **Integration Tests**: Verify app structure and configuration
- **Build Tests**: Ensure apps can be packaged correctly
- **Dependency Audits**: Check for security vulnerabilities

## Quick Start

### Running Tests Locally

```bash
# Install test dependencies
cd tests/electron
npm install

# Run all tests
npm test

# Run specific test suites
npm run test:electron    # Framework tests only
npm run test:apps        # App integration tests only

# Watch mode for development
npm run test:watch

# Generate coverage report
npm run test:coverage
```

## Test Structure

### Framework Tests (`tests/electron/`)

Unit tests for core Electron framework modules:

- `test_app_controller.js` - AppController lifecycle and initialization
- `test_window_manager.js` - Window creation and management
- `test_mcp_client.js` - WebSocket communication and MCP protocol

### App Tests (`tests/apps/`)

Integration tests for Electron apps:

- `test_jira_app.js` - Jira app configuration and structure
- `test_example_app.js` - Example app template verification

## What Gets Tested

### Electron Framework

✅ **AppController**
- Configuration loading
- Service initialization
- MCP connection setup
- Error handling
- Lifecycle management

✅ **WindowManager**
- Window creation with correct options
- Preload script loading
- Event handler setup
- Window state management

✅ **MCPClient**
- WebSocket connection
- Message sending/receiving
- Reconnection logic
- Error handling

### Electron Apps

✅ **Configuration**
- Valid `app.config.json`
- Valid `package.json`
- Required dependencies present
- Version compatibility

✅ **Structure**
- Main entry point exists
- Preload scripts present
- Required directories exist
- Build configuration valid

✅ **Dependencies**
- Compatible Electron version
- No high-severity vulnerabilities
- Matching framework versions
- Valid npm scripts

## GitHub Actions Integration

### Automated Testing Workflow

Tests run automatically on:
- **Pull Requests**: Changes to Electron framework or apps
- **Dependency Updates**: Dependabot PRs
- **Main Branch**: After merges
- **Manual Trigger**: Via GitHub Actions UI

### Workflow Jobs

1. **test-electron-framework**
   - Runs unit tests for core modules
   - Generates code coverage report
   - Uploads coverage artifacts

2. **test-apps-integration**
   - Validates app configurations
   - Checks file structure
   - Verifies dependency versions

3. **test-apps-build**
   - Tests app packaging
   - Validates npm scripts
   - Checks build configuration

4. **dependency-audit**
   - Scans for vulnerabilities
   - Generates audit reports
   - Uploads results as artifacts

## Testing New Apps

When creating a new Electron app, follow these steps:

### 1. Create App Structure

```
src/gaia/apps/my-app/webui/
├── app.config.json       # App configuration
├── package.json          # Dependencies and scripts
├── src/
│   ├── main.js          # Entry point
│   ├── preload.js       # Preload script
│   └── services/        # App-specific services
└── public/              # Static assets
```

### 2. Add Dependabot Configuration

Edit `.github/dependabot.yml`:

```yaml
  # JavaScript - My App (monitor only, no PRs)
  - package-ecosystem: "npm"
    directory: "/src/gaia/apps/my-app/webui"
    schedule:
      interval: "monthly"
    labels:
      - "dependencies"
      - "javascript"
      - "my-app"
    open-pull-requests-limit: 0
    groups:
      my-app-dependencies:
        patterns:
          - "*"
```

### 3. Create Integration Tests

Create `tests/apps/test_my_app.js`:

```javascript
// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { jest } = require('@jest/globals');
const path = require('path');
const fs = require('fs');

describe('My App Integration', () => {
  const appPath = path.join(__dirname, '../../src/gaia/apps/my-app/webui');
  
  describe('app configuration', () => {
    it('should have valid app.config.json', () => {
      const configPath = path.join(appPath, 'app.config.json');
      expect(fs.existsSync(configPath)).toBe(true);
      
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config).toHaveProperty('name');
      expect(config).toHaveProperty('displayName');
      expect(config).toHaveProperty('version');
    });

    it('should have valid package.json', () => {
      const packagePath = path.join(appPath, 'package.json');
      expect(fs.existsSync(packagePath)).toBe(true);
      
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg).toHaveProperty('name');
      expect(pkg).toHaveProperty('scripts');
      expect(pkg.scripts).toHaveProperty('start');
    });
  });

  describe('app structure', () => {
    it('should have required files', () => {
      expect(fs.existsSync(path.join(appPath, 'src/main.js'))).toBe(true);
      expect(fs.existsSync(path.join(appPath, 'src/preload.js'))).toBe(true);
    });
  });
});
```

### 4. Update Test Workflow

Add your app to `.github/workflows/test_electron.yml`:

```yaml
matrix:
  app:
    - name: jira
      path: src/gaia/apps/jira/webui
    - name: example
      path: src/gaia/apps/example/webui
    - name: my-app
      path: src/gaia/apps/my-app/webui  # Add your app here
```

### 5. Verify Tests

```bash
# Run your new tests
cd tests/electron
npm test -- test_my_app.js

# Check coverage
npm run test:coverage
```

## Debugging Tests

### Enable Debug Output

```bash
# Show all console output
DEBUG=true npm test

# Run specific test with verbose output
npm test -- test_app_controller.js --verbose

# Debug in Node.js
node --inspect-brk node_modules/.bin/jest --runInBand
```

### VS Code Debugging

Add to `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "node",
      "request": "launch",
      "name": "Jest - Current File",
      "program": "${workspaceFolder}/tests/electron/node_modules/.bin/jest",
      "args": [
        "${fileBasename}",
        "--runInBand"
      ],
      "console": "integratedTerminal",
      "cwd": "${workspaceFolder}/tests/electron"
    }
  ]
}
```

## Continuous Integration Best Practices

### 1. Run Tests Before Committing

```bash
# Quick test before commit
cd tests/electron
npm test

# Full test with coverage
npm run test:coverage
```

### 2. Monitor Test Results

- Check GitHub Actions tab for PR status
- Review test artifacts for failures
- Examine coverage reports

### 3. Keep Tests Fast

- Mock expensive operations
- Use `--maxWorkers=2` for CI
- Skip E2E tests in unit test runs

### 4. Update Tests with Code Changes

- Add tests for new features
- Update tests when refactoring
- Maintain coverage thresholds

## Common Issues

### Issue: Cannot find module 'electron'

**Cause**: Electron is mocked in tests but import path is incorrect

**Solution**: Ensure `moduleNameMapper` is configured in `package.json`:
```json
"moduleNameMapper": {
  "^electron$": "<rootDir>/tests/electron/mocks/electron.js"
}
```

### Issue: Tests timeout

**Cause**: Async operations not completing or no timeout set

**Solution**: 
```javascript
// Increase timeout for slow tests
jest.setTimeout(10000);

// Or per-test
it('slow test', async () => {
  // test code
}, 10000);
```

### Issue: Coverage below threshold

**Cause**: Uncovered code paths

**Solution**: 
1. Run `npm run test:coverage`
2. Open `coverage/lcov-report/index.html`
3. Add tests for red-highlighted code
4. Or adjust thresholds in `package.json`

## Security Audits

### Running Audits Manually

```bash
# Audit Electron framework
cd src/gaia/electron
npm audit

# Audit specific app
cd src/gaia/apps/jira/webui
npm audit

# Fix vulnerabilities automatically
npm audit fix
```

### Automated Audits

Security audits run automatically in CI:
- On every PR
- Weekly scheduled scans
- After dependency updates

Results are uploaded as artifacts and visible in GitHub Security tab.

## Advanced Testing

### E2E Testing with Spectron

For full end-to-end testing:

```javascript
const { Application } = require('spectron');
const path = require('path');

describe('App E2E', () => {
  let app;

  beforeEach(async () => {
    app = new Application({
      path: '/path/to/electron',
      args: [path.join(__dirname, '../../src/gaia/apps/jira/webui')]
    });
    await app.start();
  });

  afterEach(async () => {
    if (app && app.isRunning()) {
      await app.stop();
    }
  });

  it('should open window', async () => {
    const count = await app.client.getWindowCount();
    expect(count).toBe(1);
  });
});
```

### Performance Testing

Monitor app startup time and memory usage:

```javascript
it('should start quickly', async () => {
  const start = Date.now();
  await appController.initialize();
  const duration = Date.now() - start;
  
  expect(duration).toBeLessThan(5000); // 5 seconds
});
```

## Resources

- [Jest Documentation](https://jestjs.io/)
- [Electron Testing Guide](https://www.electronjs.org/docs/latest/tutorial/automated-testing)
- [GitHub Actions](https://docs.github.com/en/actions)
- [Dependabot](https://docs.github.com/en/code-security/dependabot)

## Getting Help

If you encounter issues:
1. Check this guide and README.md in tests/electron/
2. Review existing test files for examples
3. Check GitHub Actions logs for CI failures
4. Ask in team discussions

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
