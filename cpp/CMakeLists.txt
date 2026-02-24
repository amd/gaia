# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

cmake_minimum_required(VERSION 3.14)
project(gaia-cpp VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ---------------------------------------------------------------------------
# Options -- default ON when this is the top-level project, OFF as a sub-
# project so consumers only get the library.
# ---------------------------------------------------------------------------
if(CMAKE_PROJECT_NAME STREQUAL PROJECT_NAME)
    option(GAIA_BUILD_TESTS    "Build gaia_core tests"   ON)
    option(GAIA_BUILD_EXAMPLES "Build gaia_core examples" ON)
else()
    option(GAIA_BUILD_TESTS    "Build gaia_core tests"   OFF)
    option(GAIA_BUILD_EXAMPLES "Build gaia_core examples" OFF)
endif()

# ---------------------------------------------------------------------------
# Dependencies -- prefer system packages, fall back to FetchContent
# ---------------------------------------------------------------------------
include(FetchContent)

# nlohmann/json (public dependency -- json types in the API)
find_package(nlohmann_json 3.11 QUIET)
if(NOT nlohmann_json_FOUND)
    message(STATUS "nlohmann_json not found -- fetching via FetchContent")
    FetchContent_Declare(
        json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG        v3.11.3
    )
    FetchContent_MakeAvailable(json)
endif()

# cpp-httplib (private dependency -- implementation detail)
find_package(httplib QUIET)
if(NOT httplib_FOUND)
    message(STATUS "cpp-httplib not found -- fetching via FetchContent")
    FetchContent_Declare(
        httplib
        GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
        GIT_TAG        v0.15.3
    )
    FetchContent_MakeAvailable(httplib)
endif()

# Google Test (only when building tests)
if(GAIA_BUILD_TESTS)
    find_package(GTest QUIET)
    if(NOT GTest_FOUND)
        message(STATUS "GTest not found -- fetching via FetchContent")
        FetchContent_Declare(
            googletest
            GIT_REPOSITORY https://github.com/google/googletest.git
            GIT_TAG        v1.14.0
        )
        set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
        FetchContent_MakeAvailable(googletest)
    endif()
endif()

# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------
add_library(gaia_core
    src/tool_registry.cpp
    src/console.cpp
    src/json_utils.cpp
    src/agent.cpp
    src/mcp_client.cpp
)

add_library(gaia::gaia_core ALIAS gaia_core)

include(GenerateExportHeader)
generate_export_header(gaia_core
    EXPORT_MACRO_NAME GAIA_API
    EXPORT_FILE_NAME  ${CMAKE_CURRENT_BINARY_DIR}/include/gaia/export.h
)

target_include_directories(gaia_core PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

set_target_properties(gaia_core PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

# Compiler warnings (PRIVATE -- consumers pick their own warning level)
target_compile_options(gaia_core PRIVATE
    $<$<CXX_COMPILER_ID:MSVC>:/W4>
    $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wall -Wextra -Wpedantic>
)

# Link libraries -- nlohmann_json is PUBLIC (part of the API),
# httplib is PRIVATE (implementation detail).
target_link_libraries(gaia_core
    PUBLIC  nlohmann_json::nlohmann_json
    PRIVATE httplib::httplib
)

# Platform-specific libraries
if(UNIX)
    target_link_libraries(gaia_core PUBLIC pthread)
endif()

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------
if(GAIA_BUILD_EXAMPLES)
    add_executable(simple_agent examples/simple_agent.cpp)
    target_link_libraries(simple_agent PRIVATE gaia::gaia_core)
endif()

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
if(GAIA_BUILD_TESTS)
    enable_testing()

    add_executable(gaia_tests
        tests/test_types.cpp
        tests/test_tool_registry.cpp
        tests/test_json_utils.cpp
        tests/test_agent.cpp
        tests/test_mcp_client.cpp
        tests/test_console.cpp
    )

    target_link_libraries(gaia_tests PRIVATE
        gaia::gaia_core
        GTest::gtest_main
    )

    include(GoogleTest)
    gtest_discover_tests(gaia_tests)
endif()

# ---------------------------------------------------------------------------
# Install + package config
# ---------------------------------------------------------------------------
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(TARGETS gaia_core
    EXPORT  gaia_coreTargets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)

install(DIRECTORY include/gaia DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

install(FILES ${CMAKE_CURRENT_BINARY_DIR}/include/gaia/export.h
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/gaia
)

install(EXPORT gaia_coreTargets
    FILE      gaia_coreTargets.cmake
    NAMESPACE gaia::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/gaia_core
)

configure_package_config_file(
    cmake/gaia_coreConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/gaia_coreConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/gaia_core
)

write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/gaia_coreVersion.cmake
    VERSION     ${PROJECT_VERSION}
    COMPATIBILITY AnyNewerVersion
)

install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/gaia_coreConfig.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/gaia_coreVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/gaia_core
)
