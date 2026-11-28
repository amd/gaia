// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// How a tool tells a person to connect an account, for the front-end they use.
// Ported from src/gaia/agents/tools/connect_card.py. In the Agent UI a tool that
// finds no usable account puts connectCard() in its result under kConnectCardKey,
// and the app draws a Connect button from it. Other front-ends get the remedy in
// words. A surface is "agent_ui", "tui", "cli" or "" (unknown).

#pragma once

#include <map>
#include <string>
#include <utility>
#include <vector>

#include "gaia/connectors.h"
#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia::connect {

constexpr const char* kConnectCardKind = "connect_account";
constexpr const char* kConnectCardKey = "connect_card";
constexpr const char* kAgentUi = "agent_ui";
constexpr const char* kTui = "tui";
constexpr const char* kConnectCardSummary = "Needs a connected account";
constexpr const char* kConnectorsSettings = "Settings > Connectors";

GAIA_API extern const char* const kConnectButtonNote;
GAIA_API extern const char* const kConnectInAppNote;

/// The accounts a card may offer.
GAIA_API const std::vector<std::string>& connectCardProviders();

using ScopeMap = std::map<std::string, std::vector<std::string>>;
using AccountMap = std::map<std::string, std::string>;

/// What a failed call says signing in would fix: the providers to connect,
/// those of them already connected, the access the call needs, the account it
/// used, and whether account setup stopped it before it ran.
struct ConnectFault {
    std::vector<std::string> connect;
    std::vector<std::string> reconnect;
    ScopeMap scopes;
    AccountMap account;
    bool setup = false;
};

/// Whether `surface` is one of the apps, where a command is no remedy.
GAIA_API bool inApp(const std::string& surface);

/// What to tell the model about connecting `providers` on `surface`.
GAIA_API std::string connectNote(const std::vector<std::string>& providers, const std::string& agentId,
                                 const std::string& surface);

/// The card payload offering `providers`, granting `agentId` on sign-in.
/// Throws std::invalid_argument for no provider or one a card can't offer.
GAIA_API json connectCard(const std::vector<std::string>& providers, const std::string& agentId,
                          const std::vector<std::string>& reconnect = {}, const ScopeMap& scopes = {},
                          const AccountMap& account = {});

/// (message, needsReconnect) for a token the connectors refused, worded for the apps.
GAIA_API std::pair<std::string, bool> tokenFault(const std::string& name, const connectors::ConnectorsError& error);

/// `payload` with how to connect `providers` added to its `field`; the card
/// rides along only in the Agent UI. Unchanged when there is nothing to connect.
GAIA_API json& withConnectRemedy(json& payload, const std::vector<std::string>& providers, const std::string& agentId,
                                 const std::string& surface, const std::string& field = "error",
                                 const std::vector<std::string>& reconnect = {}, const ScopeMap& scopes = {},
                                 const AccountMap& account = {});

/// A failed result plus the remedy `fault` names. `scopes` is what this kind of
/// call needs; the fault's own scopes win for a provider they name. A call
/// stopped by account setup is marked as never run.
GAIA_API json& withConnectCard(json& payload, const ConnectFault& fault, const std::string& agentId,
                               const std::string& surface, const ScopeMap& scopes = {});

}  // namespace gaia::connect
