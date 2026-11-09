// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// GaiaAgent (C++): the general-purpose desktop agent — files, documents, data,
// code, shell and web — built on gaia_core. The native counterpart of the
// Python flagship, packaged for embedding (e.g. `lemonade agents`).

#pragma once

#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/document_index.h>
#include <gaia/lemonade_client.h>
#include <gaia/memory_store.h>
#include <gaia/process.h>
#include <gaia/skill_library.h>
#include <gaia/workspace.h>

#include <atomic>

namespace gaia {

class GaiaAgent : public Agent {
public:
    struct Options {
        bool webTools = true;  // web_fetch (still CONFIRM-gated)
        bool skills = true;    // SKILL.md catalog, list_skills, load_skill
        // Folders the tools may touch; empty = the working directory. Ignored
        // with fullAccess, which lets the tools reach anywhere the user can.
        std::vector<std::string> workspaceRoots;
        bool fullAccess = false;
        bool documents = true;       // index_document / query_documents
        std::string embeddingModel;  // empty = keyword-only retrieval
        bool memory = true;          // remember / recall across sessions
        std::string memoryPath;      // empty = MemoryStore::defaultPath()
        bool vision = true;          // analyze_image
        std::string visionModel;     // empty = the agent's own model
        std::string transcribeModel; // empty = no transcribe_audio
        std::string audioBaseUrl;    // empty = the agent's endpoint
        std::string imageModel;      // empty = no generate_image
        bool screenshots = false;    // take_screenshot (off unless asked: privacy)
        // The shell this agent's commands run in; unset = one per process.
        std::shared_ptr<ShellSession> shell;
        // Read on every call, so a chat can flip them mid-turn. Unset = on.
        std::shared_ptr<std::atomic<bool>> internetOff;    // nothing that goes online runs
        std::shared_ptr<std::atomic<bool>> memoryOff;      // memory neither read nor written
        std::shared_ptr<std::atomic<bool>> memoryPrivate;  // memory read, nothing saved
    };

    DocumentIndex* documents() { return docs_.get(); }

    explicit GaiaAgent(const AgentConfig& config);
    GaiaAgent(const AgentConfig& config, Options options);
    ~GaiaAgent() override;

protected:
    void registerTools() override;
    std::string getSystemPrompt() const override;

    /// Sends an answer back once when it says a file was saved that doesn't exist.
    std::optional<std::string> reviewFinalAnswer(const std::string& answer) override;

public:
    /// Files the answer says were created or saved, as written in it.
    static std::vector<std::string> claimedFiles(const std::string& answer);

private:
    /// OS, working directory and date, appended to the system prompt.
    std::string systemContext() const;

    /// The chat's switches, checked before a tool can ask for approval.
    std::optional<json> switchRefusal(const ToolInfo& tool, const json& args) const;
    bool memoryOff() const { return options_.memoryOff && options_.memoryOff->load(); }

    Options options_;
    std::unique_ptr<SkillLibrary> skills_;
    std::unique_ptr<LemonadeClient> embedClient_;
    std::unique_ptr<DocumentIndex> docs_;
    std::unique_ptr<DocumentIndex> code_;

    void registerCodeTools(std::shared_ptr<const Workspace> ws);
    std::unique_ptr<MemoryStore> memory_;
    std::unique_ptr<LemonadeClient> visionClient_;

    void registerVisionTool(std::shared_ptr<const Workspace> ws);
};

} // namespace gaia
