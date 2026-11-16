// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const path = require('node:path');
const { getDefaultConfig } = require('expo/metro-config');

const webui = path.resolve(__dirname, '../webui/src');

const config = getDefaultConfig(__dirname);

// All of webui's source, so any `@webui/*` import resolves; Metro bundles only what the app imports.
config.watchFolders = [...(config.watchFolders ?? []), webui];
// Packages for files under webui (an imported png's asset registry, too) resolve from the app, as in jest.
config.resolver.nodeModulesPaths = [path.resolve(__dirname, 'node_modules')];

module.exports = config;
