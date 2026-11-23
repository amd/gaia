// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// What a GAIA install set up for this user, read the way the Python harness
// reads it: GAIA's own model server (<GAIA_HOME>/lemonade) and the chosen chat
// model (config.json). Used when no flag or variable names them.

#pragma once

#include <filesystem>
#include <iosfwd>
#include <string>

#include "gaia/lemonade_client.h"
#include "gaia/types.h"

namespace gaia {

/// The model every GAIA agent falls back to when nothing chose one.
inline constexpr const char* kGaiaFallbackModel = "Gemma-4-E4B-it-GGUF";

/// What chose the model server, for an error that says where to fix it.
enum class ServerSource {
    Flag,           // --base-url
    GaiaBaseUrlEnv, // GAIA_BASE_URL
    SettingsFile,   // gaiacpp's settings file
    Provider,       // --provider / "provider"
    LemonadeEnv,    // LEMONADE_BASE_URL the user set
    SavedExternal,  // lemonade_mode "external" in GAIA's config.json
    GaiaRunning,    // GAIA's own server, running
    GaiaStopped,    // GAIA's own server, installed but not running
    Default,        // nothing found: Lemonade's standard port
};

/// Where the person reading a remedy is, so it names something they can do there.
enum class Surface {
    Terminal,  // a shell, the REPL or the terminal UI
    App,       // the GAIA desktop app, which is open and has its own Restart
};

struct ServerOrigin {
    ServerSource source = ServerSource::Default;
    std::string label;  // e.g. "--base-url", "Settings file /x/config.json"
    std::string keyVar;  // the variable whose key requests carry, "" when none; never the key
    bool gaiaKey = false;  // requests carry GAIA's own server's key
};

/// GAIA's server for this user, and the key its running instance requires.
struct GaiaServer {
    std::string baseUrl;
    ServerOrigin origin;
    std::filesystem::path lemonadeDir;  // <GAIA_HOME>/lemonade
    int runningPort = 0;                // the running instance's port, 0 when none
    std::string runningKey;             // its API key
    bool savedKeyElsewhere = false;     // external server whose key GAIA keeps in its credential store
    // LEMONADE_API_KEY isn't the user's: GAIA's own export (env.sh), or it came with a
    // LEMONADE_BASE_URL the app chose GAIA's own server over.
    bool envKeyForeign = false;
    std::filesystem::path configFile;   // GAIA's config.json
};

/// <GAIA_HOME>/lemonade, else ~/.gaia/lemonade (as GAIA's embedded server lays it out).
std::filesystem::path gaiaLemonadeDir();

/// GAIA's config.json: GAIA_CONFIG_FILE, then GAIA_CONFIG_DIR, then GAIA_HOME, then ~/.gaia.
std::filesystem::path gaiaConfigFile();

/// GAIA's running server alone (<GAIA_HOME>/lemonade/state.json, its process
/// alive): runningPort and runningKey, or 0 and "" when none runs.
GaiaServer runningGaiaServer();

/// The server a run uses when no flag, GAIA_BASE_URL or settings file names one:
/// LEMONADE_BASE_URL (unless it is GAIA's own export), GAIA's saved external
/// server, GAIA's running server, GAIA's stopped server on its last port, then
/// Lemonade's standard port. Throws when GAIA's config.json can't be read.
GaiaServer resolveGaiaServer();

/// The chat model a run uses when nothing chose one: config.json's
/// default_model, then last_model, then kGaiaFallbackModel. "hybrid" runs on
/// hybrid_cheap_model; a Claude id is skipped, as gaiacpp has no Claude provider.
std::string resolveGaiaModel();

/// GAIA's running server's key when `url` is that server (http, loopback, its
/// port), else "". The key never goes anywhere else.
std::string embeddedKeyFor(const GaiaServer& server, const std::string& url);

/// Unset LEMONADE_API_KEY when it isn't the user's (envKeyForeign), so no
/// request, and no command a tool runs, carries it to a server it wasn't for.
void dropForeignLemonadeKey(const GaiaServer& server);

/// The key a request to `url` carries when no flag or settings file names one,
/// in resolveApiKey's order: GAIA_API_KEY; else `lemonadeKey` (the user's
/// LEMONADE_API_KEY) to a loopback address only; else, when the user set
/// neither, GAIA's own key to GAIA's own server. Notes the source in `origin`.
std::string keyForNamedServer(const GaiaServer& own, const std::string& url, const std::string& lemonadeKey,
                              ServerOrigin& origin);

/// "" when the model server answers its health check. Else why not, by what
/// chose it: how to start it when nothing answers, or the key or address to
/// fix when something answers with an error. Endpoints that are not Lemonade
/// are not asked.
std::string serverDownReason(LemonadeClient& client, const ServerOrigin& origin,
                             Surface surface = Surface::Terminal, bool* nothingAnswered = nullptr);

/// Which key `cfg` sends, recorded in `origin` for an error that has to name it.
void noteKeySource(ServerOrigin& origin, const AgentConfig& cfg, bool gaiaKey);

/// The terminal a download question is asked at.
struct DownloadPrompt {
    bool interactive = false;  // stdin and stderr are both a terminal
    std::istream* in = nullptr;
    std::ostream* err = nullptr;
};

/// The process's own terminal.
DownloadPrompt terminalPrompt();

/// Before a run: the server answers as Lemonade does, and `model` is on disk.
/// A server that replies with an error or something else is reported as that
/// (its status, the key or address to fix), never as a server that is down. A
/// model that is not downloaded is downloaded only after a yes (`yes`, or the
/// question answered y); otherwise this throws and nothing is downloaded.
/// Endpoints that are not Lemonade, and cloud models, are left to the server.
void checkServerAndModel(LemonadeClient& client, const std::string& model, const ServerOrigin& origin,
                         bool yes, const DownloadPrompt& prompt);

}  // namespace gaia
