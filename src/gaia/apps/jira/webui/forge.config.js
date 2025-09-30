// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const path = require('path');

module.exports = {
  packagerConfig: {
    name: 'JAX - Jira Agent Experience',
    executableName: 'gaia-jax-webui',
    appBundleId: 'com.amd.gaia-jax',
    appCategoryType: 'public.app-category.productivity',
    icon: path.join(__dirname, 'src', 'assets', 'icons', 'icon'), // Will look for icon.ico, icon.icns, etc.
    platform: 'all',
    out: 'out',
    overwrite: true,
    asar: true,
    extraMetadata: {
      name: 'JAX - Jira Agent Experience'
    }
  },
  rebuildConfig: {},
  makers: [
    // Windows installer
    {
      name: '@electron-forge/maker-squirrel',
      config: {
        name: 'gaia_jax_webui',
        authors: 'AMD AI Group',
        description: 'JAX - AI-powered Jira Agent Experience using MCP integration',
        setupIcon: path.join(__dirname, 'src', 'assets', 'icons', 'icon.ico'),
        setupExe: 'JAX-Setup.exe',
        noMsi: true
      },
      platforms: ['win32']
    },
    // Linux DEB package
    {
      name: '@electron-forge/maker-deb',
      config: {
        maintainer: 'AMD AI Group',
        homepage: 'https://github.com/aigdat/gaia',
        description: 'JAX - AI-powered Jira Agent Experience using MCP integration',
        productDescription: 'JAX (Jira Agent Experience) provides natural language interface to JIRA using AMD GAIA AI framework',
        genericName: 'JAX',
        categories: ['Office', 'Development']
      },
      platforms: ['linux']
    },
    // Linux RPM package
    {
      name: '@electron-forge/maker-rpm',
      config: {
        summary: 'JAX - AI-powered Jira Agent Experience',
        description: 'JAX (Jira Agent Experience) provides natural language interface to JIRA using AMD GAIA AI framework',
        homepage: 'https://github.com/aigdat/gaia',
        license: 'MIT',
        categories: ['Office', 'Development']
      },
      platforms: ['linux']
    },
    // ZIP archives for all platforms
    {
      name: '@electron-forge/maker-zip',
      platforms: ['darwin', 'linux', 'win32']
    }
  ],
  publishers: [
    {
      name: '@electron-forge/publisher-github',
      config: {
        repository: {
          owner: 'aigdat',
          name: 'gaia'
        },
        prerelease: false,
        draft: true,
        tagPrefix: 'gaia-jax-v'
      }
    }
  ],
  plugins: [
    {
      name: '@electron-forge/plugin-auto-unpack-natives',
      config: {}
    }
  ],
  hooks: {
    packageAfterPrune: async (config, buildPath) => {
      // Custom packaging steps if needed
      console.log('📦 Package after prune hook executed');
    }
  }
};