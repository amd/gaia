// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { defineConfig, globalIgnores } = require('eslint/config');
const expo = require('eslint-config-expo/flat');

module.exports = defineConfig([expo, globalIgnores(['dist/*', '.expo/*'])]);
