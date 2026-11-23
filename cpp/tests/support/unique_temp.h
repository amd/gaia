// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include <string>

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

namespace testsupport {

/// `name` with this process's id on the end. ctest runs each test in its own
/// process, so two tests run side by side never share a temp folder or file.
inline std::string perProcess(const std::string& name) {
#ifdef _WIN32
    return name + "_" + std::to_string(_getpid());
#else
    return name + "_" + std::to_string(getpid());
#endif
}

}  // namespace testsupport
