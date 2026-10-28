# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# zig-toolchain.cmake
# -------------------
# Build with `zig cc` / `zig c++`: one self-contained compiler that targets
# Windows, Linux and macOS from any host, with a static libc++ and (for Linux)
# a static musl libc, so the result is a single binary with no runtime DLLs.
#
#   pip install ziglang cmake ninja
#   cmake --preset tiny                                  # host target
#   cmake --preset tiny -DGAIA_ZIG_TARGET=x86_64-linux-musl
#
# GAIA_ZIG_TARGET is a zig target triple. Left empty, it follows the host.
# zig is found on PATH, else as the `ziglang` Python package's zig executable.

if(NOT DEFINED GAIA_ZIG_TARGET OR GAIA_ZIG_TARGET STREQUAL "")
    if(CMAKE_HOST_WIN32)
        set(GAIA_ZIG_TARGET "x86_64-windows-gnu")
    elseif(CMAKE_HOST_APPLE)
        set(GAIA_ZIG_TARGET "aarch64-macos")
    else()
        set(GAIA_ZIG_TARGET "x86_64-linux-musl")
    endif()
endif()
set(GAIA_ZIG_TARGET "${GAIA_ZIG_TARGET}" CACHE STRING "zig target triple")

if(NOT GAIA_ZIG_EXE AND DEFINED ENV{GAIA_ZIG_EXE})
    set(GAIA_ZIG_EXE "$ENV{GAIA_ZIG_EXE}" CACHE FILEPATH "zig executable" FORCE)
endif()
find_program(GAIA_ZIG_EXE zig)
if(NOT GAIA_ZIG_EXE)
    # The first Python on PATH that has the `ziglang` package (a venv's, usually).
    foreach(_py python3 python py)
        find_program(_gaia_py_${_py} ${_py})
        if(NOT _gaia_py_${_py})
            continue()
        endif()
        execute_process(
            COMMAND "${_gaia_py_${_py}}" -c
                    "import ziglang, os; print(os.path.join(os.path.dirname(ziglang.__file__), 'zig'))"
            OUTPUT_VARIABLE _gaia_zig_base
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
        foreach(_cand "${_gaia_zig_base}.exe" "${_gaia_zig_base}")
            if(_gaia_zig_base AND EXISTS "${_cand}" AND NOT IS_DIRECTORY "${_cand}")
                set(GAIA_ZIG_EXE "${_cand}" CACHE FILEPATH "zig executable" FORCE)
                break()
            endif()
        endforeach()
        if(GAIA_ZIG_EXE)
            break()
        endif()
    endforeach()
endif()
file(TO_CMAKE_PATH "${GAIA_ZIG_EXE}" GAIA_ZIG_EXE)
if(NOT GAIA_ZIG_EXE)
    message(FATAL_ERROR
        "zig-toolchain.cmake: zig not found on PATH or as the `ziglang` Python package.\n"
        "Install it with `pip install ziglang`, or put zig on PATH, then re-run CMake.")
endif()

if(GAIA_ZIG_TARGET MATCHES "windows")
    set(CMAKE_SYSTEM_NAME Windows)
elseif(GAIA_ZIG_TARGET MATCHES "macos")
    set(CMAKE_SYSTEM_NAME Darwin)
else()
    set(CMAKE_SYSTEM_NAME Linux)
endif()
string(REGEX MATCH "^[^-]+" CMAKE_SYSTEM_PROCESSOR "${GAIA_ZIG_TARGET}")

# CMake wants a single executable per tool, and `zig cc` is two words, so write
# one-line launchers into the build tree.
set(_gaia_zig_wrap "${CMAKE_BINARY_DIR}/zig-wrappers")
file(MAKE_DIRECTORY "${_gaia_zig_wrap}")
foreach(_tool cc c++ ar ranlib rc)
    set(_args "${_tool}")
    if(_tool STREQUAL "cc" OR _tool STREQUAL "c++")
        set(_args "${_tool} -target ${GAIA_ZIG_TARGET}")
    endif()
    string(REPLACE "+" "x" _name "zig-${_tool}")
    if(CMAKE_HOST_WIN32)
        file(WRITE "${_gaia_zig_wrap}/${_name}.cmd" "@\"${GAIA_ZIG_EXE}\" ${_args} %*\r\n")
        set(_gaia_zig_${_name} "${_gaia_zig_wrap}/${_name}.cmd")
    else()
        file(WRITE "${_gaia_zig_wrap}/${_name}" "#!/bin/sh\nexec \"${GAIA_ZIG_EXE}\" ${_args} \"$@\"\n")
        file(CHMOD "${_gaia_zig_wrap}/${_name}"
             PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE
                         WORLD_READ WORLD_EXECUTE)
        set(_gaia_zig_${_name} "${_gaia_zig_wrap}/${_name}")
    endif()
endforeach()

set(CMAKE_C_COMPILER   "${_gaia_zig_zig-cc}")
set(CMAKE_CXX_COMPILER "${_gaia_zig_zig-cxx}")
set(CMAKE_AR           "${_gaia_zig_zig-ar}" CACHE FILEPATH "" FORCE)
set(CMAKE_RANLIB       "${_gaia_zig_zig-ranlib}" CACHE FILEPATH "" FORCE)
# Used instead of CMAKE_AR/RANLIB when interprocedural optimization (LTO) is on.
foreach(_lang C CXX)
    set(CMAKE_${_lang}_COMPILER_AR     "${_gaia_zig_zig-ar}" CACHE FILEPATH "" FORCE)
    set(CMAKE_${_lang}_COMPILER_RANLIB "${_gaia_zig_zig-ranlib}" CACHE FILEPATH "" FORCE)
endforeach()
if(CMAKE_SYSTEM_NAME STREQUAL "Windows")
    set(CMAKE_RC_COMPILER "${_gaia_zig_zig-rc}")
endif()

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
