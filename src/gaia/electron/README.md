# Electron Framework

Universal wrapper framework for GAIA desktop applications.

## Overview

The Electron framework is a shared infrastructure that dynamically loads and runs GAIA apps as desktop applications. Instead of each app implementing its own Electron setup, this framework provides a single, reusable wrapper that can launch any properly configured app.

## Architecture

### Dynamic App Loading

The framework uses the `GAIA_APP_NAME` environment variable to determine which app to load:

```javascript
// Set by npm scripts
process.env.GAIA_APP_NAME = 'jira'

// Framework finds and loads the app
const appPath = `src/gaia/apps/${appName}/webui/`
```

### Configuration Management

Each app provides an `app.config.json` file that specifies:
- Window dimensions
- Display name and metadata
- Development server port
- Version information

Example:
```json
{
  "name": "jira",
  "displayName": "JIRA Dashboard",
  "version": "1.0.0",
  "window": {
    "width": 1200,
    "height": 800
  },
  "devServer": {
    "port": 4022
  }
}
```

## How It Works

1. **NPM Script Execution**: User runs `npm run app:jira:run:electron`
2. **Environment Setup**: Script sets `GAIA_APP_NAME=jira`
3. **Framework Launch**: Electron starts with `src/gaia/electron/src/main.js`
4. **App Discovery**: Framework looks for app at `src/gaia/apps/jira/webui/`
5. **Configuration Loading**: Reads `app.config.json` for window settings
6. **Window Creation**: Creates Electron window with specified dimensions
7. **App Loading**: Loads the app's `public/index.html`

## File Structure

```
electron/
├── src/
│   ├── main.js              # Main Electron process
│   ├── preload/
│   │   └── preload.js       # Preload script for renderer
│   ├── services/
│   │   └── window-manager.js # Window management
│   └── app-controller.js    # App loading logic
└── package.json
```

## CI/CD Integration

The framework is central to the CI/CD pipeline:

### Automatic App Discovery

GitHub Actions scans for apps with `app.config.json`:

```bash
for app_dir in src/gaia/apps/*/webui; do
  if [ -f "$app_dir/app.config.json" ]; then
    # App is added to build matrix
  fi
done
```

### Build Process

1. **Discovery**: CI/CD finds all apps with proper structure
2. **Matrix Build**: Each app is built in parallel
3. **Framework Packaging**: Uses electron-forge to create installers
4. **Platform Builds**: Creates packages for Windows, Linux, macOS

### Key Benefits

- **No Manual Configuration**: Apps are automatically discovered and built
- **Shared Infrastructure**: One framework serves all apps
- **Consistent Builds**: All apps use the same build process
- **Version Management**: Versions come from `app.config.json`

## Development Workflow

### Running Apps in Development

```bash
# Web mode (for development)
npm run app:jira:run:dev

# Electron mode (for testing desktop features)
npm run app:jira:run:electron
```

### Adding a New App

1. Create app structure in `src/gaia/apps/[name]/webui/`
2. Add `app.config.json` with app metadata
3. Add npm scripts to root `package.json`
4. CI/CD automatically discovers and builds the app

## Technical Details

### IPC Communication

The framework provides IPC channels for:
- Configuration access
- Window management
- System integration

### Preload Scripts

Preload scripts expose safe APIs to renderer processes:
```javascript
contextBridge.exposeInMainWorld('electronAPI', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  // Other APIs...
})
```

### Security

- Context isolation enabled
- Node integration disabled in renderer
- Preload scripts provide controlled API access

## See Also

- [Apps Documentation](../../../docs/apps.md) - Building GAIA applications
- [JIRA WebUI](../apps/jira/webui/README.md) - Example app implementation

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT