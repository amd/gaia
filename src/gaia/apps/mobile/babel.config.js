// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

module.exports = function (api) {
    api.cache(true);
    return { presets: ['babel-preset-expo'] };
};
