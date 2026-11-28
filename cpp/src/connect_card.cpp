// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/connect_card.h"

#include <algorithm>
#include <stdexcept>

namespace gaia::connect {

const char* const kConnectButtonNote =
    "GAIA is showing the user a Connect button for this in the chat, and "
    "the same button is in Settings > Connectors. Tell the user what is "
    "missing and that the button is on screen, and to ask again once they "
    "have signed in. Accounts connect only through that button: never give "
    "or run a terminal command for it.";

const char* const kConnectInAppNote =
    "Accounts are connected in the GAIA desktop app, under Settings > "
    "Connectors. Tell the user what is missing and to connect it there, then "
    "ask again. Never give or run a terminal command for it.";

namespace {

std::vector<std::string> unique(const std::vector<std::string>& items) {
    std::vector<std::string> out;
    for (const auto& item : items) {
        if (std::find(out.begin(), out.end(), item) == out.end()) out.push_back(item);
    }
    return out;
}

bool contains(const std::vector<std::string>& items, const std::string& item) {
    return std::find(items.begin(), items.end(), item) != items.end();
}

}  // namespace

const std::vector<std::string>& connectCardProviders() {
    static const std::vector<std::string> providers{"google", "microsoft", "microsoft_work"};
    return providers;
}

bool inApp(const std::string& surface) { return surface == kAgentUi || surface == kTui; }

std::string connectNote(const std::vector<std::string>& providers, const std::string& agentId,
                        const std::string& surface) {
    if (surface == kAgentUi) return kConnectButtonNote;
    if (surface == kTui) return kConnectInAppNote;
    std::string commands;
    for (const auto& p : providers) {
        if (!commands.empty()) commands += " or ";
        commands += "`gaia connectors connect " + p + " --grant-agent " + agentId + "`";
    }
    return "Connect it with " + commands + ", or in the GAIA desktop app under " + kConnectorsSettings +
           ", then ask again.";
}

json connectCard(const std::vector<std::string>& providers, const std::string& agentId,
                 const std::vector<std::string>& reconnect, const ScopeMap& scopes, const AccountMap& account) {
    const std::vector<std::string> names = unique(providers);
    if (names.empty()) throw std::invalid_argument("a connect card needs at least one provider");
    std::vector<std::string> unknown;
    for (const auto& p : names) {
        if (!contains(connectCardProviders(), p)) unknown.push_back(p);
    }
    if (!unknown.empty()) {
        std::string list;
        for (const auto& p : unknown) list += (list.empty() ? "'" : ", '") + p + "'";
        throw std::invalid_argument(
            "a connect card can offer only ['google', 'microsoft', 'microsoft_work'], not [" + list + "]");
    }
    json card{{"kind", kConnectCardKind}, {"providers", names}, {"agent_id", agentId}};
    std::vector<std::string> again;
    for (const auto& p : names) {
        if (contains(reconnect, p)) again.push_back(p);
    }
    if (!again.empty()) card["reconnect"] = again;
    json asked = json::object();
    for (const auto& p : names) {
        auto it = scopes.find(p);
        if (it == scopes.end()) continue;
        const std::vector<std::string> list = unique(it->second);
        if (!list.empty()) asked[p] = list;
    }
    if (!asked.empty()) card["scopes"] = asked;
    json used = json::object();
    for (const auto& [p, a] : account) {
        if (contains(names, p) && !a.empty()) used[p] = a;
    }
    if (!used.empty()) card["account"] = used;
    return card;
}

std::pair<std::string, bool> tokenFault(const std::string& name, const connectors::ConnectorsError& error) {
    if (error.kind() == "ConnectionBusyError") {
        return {"The " + name +
                    " connection is being saved by another GAIA operation right now. Retry in a moment.",
                false};
    }
    return {"GAIA could not use the " + name + " connection (" + error.kind() + "), so it needs reconnecting.", true};
}

json& withConnectRemedy(json& payload, const std::vector<std::string>& providers, const std::string& agentId,
                        const std::string& surface, const std::string& field,
                        const std::vector<std::string>& reconnect, const ScopeMap& scopes,
                        const AccountMap& account) {
    const std::vector<std::string> names = unique(providers);
    if (names.empty()) return payload;
    const std::string note = connectNote(names, agentId, surface);
    const bool has = payload.contains(field) && payload[field].is_string() && !payload[field].get<std::string>().empty();
    payload[field] = has ? payload[field].get<std::string>() + " " + note : note;
    if (surface == kAgentUi) payload[kConnectCardKey] = connectCard(names, agentId, reconnect, scopes, account);
    return payload;
}

json& withConnectCard(json& payload, const ConnectFault& fault, const std::string& agentId,
                      const std::string& surface, const ScopeMap& scopes) {
    if (fault.setup) payload["executed"] = false;
    ScopeMap merged = scopes;
    for (const auto& [p, s] : fault.scopes) merged[p] = s;
    return withConnectRemedy(payload, fault.connect, agentId, surface, "error", fault.reconnect, merged,
                             fault.account);
}

}  // namespace gaia::connect
