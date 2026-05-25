Hardware recipe vocabulary — confirmation needed

Summary

The current PR implements runtime validation for agent-declared hardware requirements.
When we later wire the resolved "recipe" through to Lemonade server startup (dispatch),
we need to confirm the canonical recipe vocabulary supported by the Lemonade server.

Current mapping (in code):

- `amd_npu` -> `oga-hybrid`
- `amd_igpu` -> `oga-hybrid`
- `amd_dgpu` -> `oga-hybrid`
- `cpu` -> `oga-cpu`

Question for the lemonade specialist (@kovtcharov-amd):

1. Is `oga-hybrid` the intended recipe for all AMD hybrid-capable hosts (NPU/IGPU/dGPU)?
2. Are there device-specific recipes we should prefer for pure NPU or dGPU hosts (e.g., `oga-npu`, `oga-dgpu`)?
3. If device-specific recipes exist, please provide the canonical names and any recommended rules for mapping device capabilities to recipes.

Suggested acceptance criteria before enabling dispatch:

- The allow-list in `src/gaia/llm/lemonade_manager.py` enumerates the exact recipe strings supported by Lemonade.
- Tests (or integration checks) validate the mapping for sample `get_system_info()` payloads.
- Documentation in the PR explains the mapping and links to Lemonade server docs or the authoritative source for recipe names.

Please tag @kovtcharov-amd or the lemonade-specialist alias in the review to confirm.
