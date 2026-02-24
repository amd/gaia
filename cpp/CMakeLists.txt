cmake_minimum_required(VERSION 3.14)
project(gaia-cpp VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Compiler warnings
if(MSVC)
    add_compile_options(/W4)
else()
    add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# Fetch nlohmann/json (header-only)
include(FetchContent)
FetchContent_Declare(
    json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
)
FetchContent_MakeAvailable(json)

# Fetch Google Test
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG        v1.14.0
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

# Fetch cpp-httplib (header-only HTTP client/server)
FetchContent_Declare(
    httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    GIT_TAG        v0.15.3
)
FetchContent_MakeAvailable(httplib)

# ---- Library ----
add_library(gaia_core
    src/tool_registry.cpp
    src/console.cpp
    src/json_utils.cpp
    src/agent.cpp
    src/mcp_client.cpp
)

target_include_directories(gaia_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(gaia_core PUBLIC
    nlohmann_json::nlohmann_json
    httplib::httplib
)

# Platform-specific libraries
if(WIN32)
    # No extra libs needed for CreateProcess
elseif(UNIX)
    target_link_libraries(gaia_core PUBLIC pthread)
endif()

# ---- Examples ----
add_executable(simple_agent examples/simple_agent.cpp)
target_link_libraries(simple_agent PRIVATE gaia_core)

# ---- Tests ----
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
    gaia_core
    GTest::gtest_main
)

include(GoogleTest)
gtest_discover_tests(gaia_tests)
