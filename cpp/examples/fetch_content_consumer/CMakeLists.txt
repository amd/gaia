# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Minimal example of consuming gaia_core from a third-party CMake project.
#
# Two integration methods — controlled by USE_FIND_PACKAGE option:
#   A) FetchContent  (default; no install step needed)
#   B) find_package  (set -DUSE_FIND_PACKAGE=ON; requires cmake --install first)

cmake_minimum_required(VERSION 3.14)
project(gaia_consumer_example CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(USE_FIND_PACKAGE "Use find_package instead of FetchContent" OFF)

if(USE_FIND_PACKAGE)
    # ---------------------------------------------------------------------------
    # Method B: find_package
    # Requires: cmake --install <build-dir> --prefix <prefix>
    # Then configure with: -DCMAKE_PREFIX_PATH=<prefix>
    # ---------------------------------------------------------------------------
    find_package(gaia_core REQUIRED)
else()
    # ---------------------------------------------------------------------------
    # Method A: FetchContent (recommended)
    # Pulls gaia from GitHub at configure time. No install step needed.
    # GAIA_BUILD_TESTS and GAIA_BUILD_EXAMPLES are OFF automatically (sub-project).
    # ---------------------------------------------------------------------------
    include(FetchContent)
    FetchContent_Declare(
        gaia_core
        GIT_REPOSITORY https://github.com/amd/gaia.git
        GIT_TAG        main
        SOURCE_SUBDIR  cpp          # gaia root is a Python project; C++ lives in cpp/
    )
    FetchContent_MakeAvailable(gaia_core)
endif()

# ---------------------------------------------------------------------------
# Your target — link against gaia::gaia_core (same name for both methods)
# ---------------------------------------------------------------------------
add_executable(my_agent main.cpp)
target_link_libraries(my_agent PRIVATE gaia::gaia_core)
