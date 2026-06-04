# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Overlay triplet: static arm64 macOS. The runner's vcpkg has no built-in
# arm64-osx-static (only the dynamic arm64-osx), so we define it here (passed
# via --overlay-triplets / VCPKG_OVERLAY_TRIPLETS). Dependencies link
# statically; libSystem stays dynamic, as Apple requires.
set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Darwin)
set(VCPKG_OSX_ARCHITECTURES arm64)
