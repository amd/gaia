# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# vcpkg-auto-setup.cmake
# ----------------------
# Locate vcpkg so that find_package(OpenSSL) works when GAIA_ENABLE_SSL=ON.
# Include this file BEFORE project() in CMakeLists.txt.
#
# Priority order:
#   1. CMAKE_TOOLCHAIN_FILE already set by user -> respect it, do nothing
#   2. VCPKG_ROOT env var                       -> use that vcpkg
#   3. VCPKG_INSTALLATION_ROOT env var          -> GitHub Actions runner vcpkg
#   4. Windows only: fail with install instructions
#   5. Linux/macOS: skip; system OpenSSL works via find_package
#
# NOTE: avoid CMake macros here -- macro arguments undergo text substitution
# which causes CMake to re-parse Windows backslash paths as string literals,
# triggering "Invalid character escape '\U'" errors.  Use direct $ENV{}
# references with file(TO_CMAKE_PATH) instead.

# 1. If the caller already provided a toolchain, honour it unconditionally.
if(DEFINED CMAKE_TOOLCHAIN_FILE)
    message(STATUS "vcpkg-auto-setup: using caller-supplied toolchain: ${CMAKE_TOOLCHAIN_FILE}")
    return()
endif()

# 2. VCPKG_ROOT environment variable (user's own vcpkg installation).
if(DEFINED ENV{VCPKG_ROOT} AND NOT "$ENV{VCPKG_ROOT}" STREQUAL "")
    message(STATUS "vcpkg-auto-setup: found VCPKG_ROOT=$ENV{VCPKG_ROOT}")
    file(TO_CMAKE_PATH "$ENV{VCPKG_ROOT}" _gaia_vcpkg_root)
    set(CMAKE_TOOLCHAIN_FILE "${_gaia_vcpkg_root}/scripts/buildsystems/vcpkg.cmake"
        CACHE STRING "vcpkg toolchain" FORCE)
    message(STATUS "vcpkg-auto-setup: toolchain -> ${CMAKE_TOOLCHAIN_FILE}")
    return()
endif()

# 3. VCPKG_INSTALLATION_ROOT — set by GitHub Actions windows-latest runners.
if(DEFINED ENV{VCPKG_INSTALLATION_ROOT} AND NOT "$ENV{VCPKG_INSTALLATION_ROOT}" STREQUAL "")
    message(STATUS "vcpkg-auto-setup: found VCPKG_INSTALLATION_ROOT=$ENV{VCPKG_INSTALLATION_ROOT}")
    file(TO_CMAKE_PATH "$ENV{VCPKG_INSTALLATION_ROOT}" _gaia_vcpkg_root)
    set(CMAKE_TOOLCHAIN_FILE "${_gaia_vcpkg_root}/scripts/buildsystems/vcpkg.cmake"
        CACHE STRING "vcpkg toolchain" FORCE)
    message(STATUS "vcpkg-auto-setup: toolchain -> ${CMAKE_TOOLCHAIN_FILE}")
    return()
endif()

# 4. Windows local dev: vcpkg not found -- warn and continue without HTTPS.
if(WIN32)
    message(WARNING
        "\n"
        "  vcpkg not found -- HTTPS support will be unavailable.\n"
        "\n"
        "  To enable HTTPS, install vcpkg with:\n"
        "    winget install Microsoft.Vcpkg\n"
        "\n"
        "  Then reopen your terminal and re-run CMake so that VCPKG_ROOT is set.\n"
        "\n"
        "  HTTP-only mode works fine for local Lemonade endpoints.\n")
endif()

# 5. Non-Windows (Linux / macOS): rely on system OpenSSL via find_package.
message(STATUS "vcpkg-auto-setup: non-Windows platform, skipping vcpkg "
               "(install libssl-dev / openssl-devel for SSL support)")
