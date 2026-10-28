// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Runtime defaults shared with the Python flagship: where GAIA keeps its
// state, which model it runs, and how its environment switches parse.
// Mirrors src/gaia/config.py and resolve_default_chat_model().

#pragma once

#include <optional>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/json_utils.h"
#include "gaia/types.h"

namespace gaia {

/// Python's DEFAULT_MODEL_NAME — the model every PC can run.
inline constexpr const char* kFlagshipDefaultModel = "Gemma-4-E4B-it-GGUF";
/// FLAGSHIP_MAX_STEPS: desktop tasks need ~60-70 steps; 150 leaves headroom.
inline constexpr int kFlagshipMaxSteps = 150;
/// GPU_CTX_SIZE — the window Gemma-4-E4B loads with when memory allows.
inline constexpr int kFlagshipContextSize = 65536;

/// The user's home directory (USERPROFILE on Windows, HOME elsewhere).
/// @throws std::runtime_error when neither is set.
GAIA_API std::string userHomeDir();

/// GAIA's state directory: GAIA_HOME, else GAIA_CONFIG_DIR, else ~/.gaia.
GAIA_API std::string gaiaHomeDir();

/// Path of the persistent config: GAIA_CONFIG_FILE, else
/// GAIA_CONFIG_DIR/config.json, else gaiaHomeDir()/config.json.
GAIA_API std::string gaiaConfigFile();

/// Where user-installed skills live: GAIA_CONFIG_DIR (or ~/.gaia) + /skills.
/// Python's user_skills_dir() ignores GAIA_HOME here, and so does this.
GAIA_API std::string userSkillsDir();

/// Parsed config.json, or an empty object when the file does not exist.
/// @throws std::runtime_error naming the path when it exists but is not a JSON object.
GAIA_API json loadGaiaConfig();

/// `default_model` from config.json, else kFlagshipDefaultModel.
GAIA_API std::string resolveDefaultChatModel();

/// True for 1/true/yes/on, false for 0/false/no/off (case-insensitive),
/// nullopt when unset or empty.
/// @throws std::invalid_argument naming the variable for any other value.
GAIA_API std::optional<bool> envFlag(const std::string& name);

/// A positive integer from the environment, nullopt when unset.
/// @throws std::invalid_argument naming the variable when it is not one.
GAIA_API std::optional<int> envPositiveInt(const std::string& name);

/// The directories in GAIA_ALLOWED_PATHS (split on the platform path
/// separator), or nullopt when unset.
/// @throws std::invalid_argument when an entry is not an existing directory.
GAIA_API std::optional<std::vector<std::string>> envAllowedPaths();

/// Where the flagship's Lemonade is and the key it wants.
struct GAIA_API LemonadeEndpoint {
    std::string baseUrl;
    std::string apiKey;   ///< empty when none is needed
    std::string source;   ///< "env", "config", "embedded" or "default"
};

/// Python's resolution order: LEMONADE_BASE_URL (ignored under
/// GAIA_LEMONADE_EMBEDDED=1), then config.json `lemonade_mode: external` with
/// `lemonade_base_url`, then GAIA's embedded server from
/// <gaiaHomeDir()>/lemonade/state.json when its process is alive, then
/// http://localhost:13305/api/v1. The embedded key is only ever sent to that
/// server's own localhost port.
/// @throws std::runtime_error when external mode is saved without a URL.
GAIA_API LemonadeEndpoint resolveLemonadeEndpoint();

/// Native tool calling for the flagship: on unless the model is one the
/// mirrored table marks as unable (Python treats unknown models as capable).
GAIA_API NativeToolCalls flagshipNativeToolCalls(const std::string& modelId);

/// Full path of @p name found on PATH (PATHEXT extensions tried on Windows),
/// or "" when it is not installed.
GAIA_API std::string findExecutable(const std::string& name);

}  // namespace gaia
