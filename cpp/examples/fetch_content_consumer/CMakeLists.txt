# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Minimal example of consuming gaia_core from a third-party CMake project.
#
# Two integration methods are shown — pick one:
#   A) FetchContent  (no install step, recommended for most projects)
#   B) find_package  (requires cmake --install first)

cmake_minimum_required(VERSION 3.14)
project(gaia_consumer_example CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

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

# ---------------------------------------------------------------------------
# Method B: find_package
# Uncomment these two lines and comment out the FetchContent block above
# after running: cmake --install <build-dir> --prefix /usr/local
# ---------------------------------------------------------------------------
# find_package(nlohmann_json REQUIRED)   # public dependency of gaia_core
# find_package(gaia_core     REQUIRED)

# ---------------------------------------------------------------------------
# Your target — link against gaia::gaia_core (same name for both methods)
# ---------------------------------------------------------------------------
add_executable(my_agent main.cpp)
target_link_libraries(my_agent PRIVATE gaia::gaia_core)
