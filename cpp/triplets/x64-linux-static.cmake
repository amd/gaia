# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Overlay triplet: static x64 Linux. The runner's vcpkg has no built-in
# x64-linux-static, so we define it here (passed via --overlay-triplets /
# VCPKG_OVERLAY_TRIPLETS). Dependencies link statically; the C runtime (glibc)
# stays dynamic, which is the only portable option on Linux.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
