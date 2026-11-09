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
#include <gaia/skill_library.h>

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

    Options options_;
    std::unique_ptr<SkillLibrary> skills_;
    std::unique_ptr<LemonadeClient> embedClient_;
    std::unique_ptr<DocumentIndex> docs_;
    std::unique_ptr<MemoryStore> memory_;
};

} // namespace gaia
