// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { SourceSkips } = require('expo/fingerprint');

// Expo's "balanced" preset, plus npm scripts and .gitignore: neither reaches the native binary.
/** @type {import('expo/fingerprint').Config} */
module.exports = {
    sourceSkips:
        SourceSkips.ExpoConfigVersions |
        SourceSkips.ExpoConfigRuntimeVersionIfString |
        SourceSkips.EasJson |
        SourceSkips.Easignore |
        SourceSkips.AutolinkingConfigPaths |
        SourceSkips.PackageJsonScriptsAll |
        SourceSkips.GitIgnore,
};
