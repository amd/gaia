// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const express = require('express');
const path = require('path');
const fs = require('fs');
const cors = require('cors');
const dotenv = require('dotenv');

class DevServer {
  constructor(appConfig, appPath) {
    this.appConfig = appConfig;
    this.appPath = appPath;
    this.app = express();
    this.server = null;
    this.port = appConfig.devServer?.port || 3000;

    // Load environment variables
    this.loadEnvironmentVariables();
  }

  loadEnvironmentVariables() {
    // Load .env.development first (defaults)
    const envDevPath = path.join(this.appPath, '.env.development');
    if (fs.existsSync(envDevPath)) {
      dotenv.config({ path: envDevPath });
      console.log('📋 Loaded .env.development');
    }

    // Load .env second (overrides) - force override existing values
    const envPath = path.join(this.appPath, '.env');
    if (fs.existsSync(envPath)) {
      dotenv.config({ path: envPath, override: true });
      console.log('📋 Loaded .env (custom overrides)');
      console.log('   GAIA_MCP_URL:', process.env.GAIA_MCP_URL);
    }
  }

  initialize() {
    // Enable CORS for development
    this.app.use(cors());

    // Parse JSON bodies
    this.app.use(express.json());

    // Serve static files from the app's public directory (except index.html)
    const publicPath = path.join(this.appPath, 'public');
    if (fs.existsSync(publicPath)) {
      // Serve all static files except index.html
      this.app.use(express.static(publicPath, { index: false }));
    }

    // Serve the main HTML file with injected environment variables
    this.app.get('/', (req, res) => {
      const indexPath = path.join(this.appPath, 'public', 'index.html');
      if (fs.existsSync(indexPath)) {
        let html = fs.readFileSync(indexPath, 'utf8');

        // Inject environment variables
        const envScript = `<script>
    // Environment variables injected by dev server
    window.ENV = {
      GAIA_MCP_URL: "${process.env.GAIA_MCP_URL || 'http://localhost:8765'}"
    };
  </script>`;

        // Insert before closing </head> tag
        html = html.replace('</head>', `${envScript}\n</head>`);

        res.send(html);
      } else {
        res.status(404).send(`
          <html>
            <body>
              <h1>App Not Found</h1>
              <p>Please ensure index.html exists in ${this.appConfig.name}/public/</p>
            </body>
          </html>
        `);
      }
    });

    // API endpoint for app config
    this.app.get('/api/config', (req, res) => {
      res.json({
        name: this.appConfig.name,
        displayName: this.appConfig.displayName,
        version: this.appConfig.version,
        environment: 'development'
      });
    });

    // Note: Direct connections work fine since MCP server has proper CORS headers
    // The MCP server sends 'access-control-allow-origin: *' header, allowing direct browser connections
    // No proxy endpoints needed - apps should connect directly to MCP URL from environment variables

    // Start the server
    this.server = this.app.listen(this.port, () => {
      console.log(`✅ ${this.appConfig.displayName} dev server running at:`);
      console.log(`   http://localhost:${this.port}`);
      console.log(`   Press Ctrl+C to stop`);
    });

    // Handle shutdown gracefully
    process.on('SIGINT', () => {
      this.cleanup();
      process.exit(0);
    });
  }

  cleanup() {
    if (this.server) {
      console.log(`\n⏹️  Stopping ${this.appConfig.displayName} dev server...`);
      this.server.close();
    }
  }
}

// Main execution
if (require.main === module) {
  const APP_NAME = process.argv[2];

  if (!APP_NAME) {
    console.error('❌ No app name provided.');
    console.error('   Usage: node dev-server.js <app-name>');
    process.exit(1);
  }

  // Find app path
  function findAppPath(appName) {
    const possiblePaths = [
      // Now that we're in apps/_shared/, go up one level to find sibling app directories
      path.resolve(__dirname, '..', appName, 'webui'),
      // Fallback to searching from project root
      path.resolve(process.cwd(), 'src', 'gaia', 'apps', appName, 'webui')
    ];

    for (const appPath of possiblePaths) {
      const configPath = path.join(appPath, 'app.config.json');
      if (fs.existsSync(configPath)) {
        return appPath;
      }
    }
    return null;
  }

  const appPath = findAppPath(APP_NAME);
  if (!appPath) {
    console.error(`❌ App '${APP_NAME}' not found.`);
    console.error('   Make sure the app exists in src/gaia/apps/' + APP_NAME + '/webui/');
    process.exit(1);
  }

  // Load app config
  const appConfigPath = path.join(appPath, 'app.config.json');
  let appConfig;
  try {
    appConfig = JSON.parse(fs.readFileSync(appConfigPath, 'utf8'));
  } catch (error) {
    console.error(`❌ Failed to load app configuration: ${error.message}`);
    process.exit(1);
  }

  console.log(`🚀 Starting ${appConfig.displayName} development server...`);

  const devServer = new DevServer(appConfig, appPath);
  devServer.initialize();
}

module.exports = DevServer;