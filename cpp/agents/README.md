<!--
Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# C++ agent packaging manifests

Each subdirectory here holds a `gaia-agent.yaml` packaging manifest for a native
(C++) agent. The manifests are the contract between the C++ sources in
[`../examples/`](../examples) and the Agent Hub: they declare identity, target
platforms, and the packaged binary filename per platform.

| id | source | platforms |
|----|--------|-----------|
| `health` | `examples/health_agent.cpp` | win-x64 |
| `wifi` | `examples/wifi_agent.cpp` | win-x64 |
| `process` | `examples/process_agent.cpp` | win-x64 |
| `security-demo` | `examples/security_demo.cpp` | win-x64, linux-x64, darwin-arm64 |
| `vlm` | `examples/vlm_agent.cpp` | win-x64, linux-x64, darwin-arm64 |
| `bash` | `agents/bash/` | win-x64, linux-x64, darwin-arm64 |

`health`, `wifi`, and `process` use Windows-only APIs (`windows.h`, `psapi`,
`netsh`/PowerShell), so they are built only on the `win-x64` matrix leg.
`security-demo`, `vlm`, and `bash` are portable and prove the Linux and macOS
legs. `bash` is the full coding agent (multi-file, in `agents/bash/`); the
others are single-file demos in `examples/`.

## How packaging works

[`.github/workflows/build_agents.yml`](../../.github/workflows/build_agents.yml)
builds the binaries with **static linking** (`BUILD_SHARED_LIBS=OFF`, vcpkg
`*-static` triplets, static MSVC runtime) so the artifacts have no runtime
DLL/`.so` dependency. Then [`packaging/package_agents.py`](../packaging/package_agents.py)
produces, per agent:

```
dist/<id>/
  <id>-<platform>[.exe]   # the binary, renamed to match cpp.binaries
  gaia-agent.yaml          # this manifest
  checksums.sha256         # SHA-256 of the binary
```

The packaged binary filename must match the `cpp.binaries.<platform>` value in
the manifest — the packaging script fails loudly otherwise.

## Mapping a manifest to its CMake target

The manifest `id` maps to a CMake executable target via the `TARGET_BY_ID`
table in [`packaging/package_agents.py`](../packaging/package_agents.py). When
you add a new C++ agent, add an entry there and a matching manifest directory.
