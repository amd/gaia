// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// FlagshipAgent — the native build of the GAIA flagship: conversation, files,
// data, web research, memory across sessions, and the skill library, with the
// Python flagship's tool names, defaults and skill catalogue.

#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/flagship_tools.h>
#include <gaia/memory.h>
#include <gaia/rag.h>
#include <gaia/skill_manager.h>

namespace gaia {

struct FlagshipOptions {
    /// Conversation only: no tools, no skills, no memory tools (GAIA_FAST).
    bool fast = false;
    /// Folders the file tools may use; empty means the user's home.
    std::vector<std::string> allowedPaths;
    /// Where relative paths and the shell start; empty means the current folder.
    std::string workDir;
    /// Memory store file; empty disables memory for this session.
    std::string memoryDb;
    /// Scratchpad tables file.
    std::string scratchpadDb = ":memory:";
    /// Register fetch_webpage and search_web.
    bool web = true;
    /// Session id recorded with each logged turn.
    std::string sessionId;
    /// The terminal channel for request_user_input; unset means nobody can answer.
    std::function<std::optional<std::string>(const std::string&, const std::vector<std::string>&)> askUser;
    /// Vision for images and scanned pages. Empty uses the session's model.
    VisionReader vision;
    /// Text → vectors for memory and documents. Empty uses Lemonade's
    /// /embeddings with the default embedding model.
    Embedder embedder;
    /// Skills compiled into the binary.
    std::vector<EmbeddedSkill> embeddedSkills;
    /// Extra discovery roots after the defaults (tests point these at temp dirs).
    std::vector<SkillRoot> skillRoots;
    /// Use only skillRoots, not ~/.gaia/skills and the .claude imports.
    bool skipDefaultSkillRoots = false;
};

/// Flagship defaults for @p base: resolved model, 64K context, 150 steps and
/// the loop behaviors the Python flagship has. Environment overrides
/// (GAIA_AGENT_MAX_STEPS, GAIA_CTX_SIZE) are strict: a bad value throws.
AgentConfig flagshipConfig(AgentConfig base = {});

class FlagshipAgent : public Agent {
public:
    FlagshipAgent(const AgentConfig& config, FlagshipOptions options);
    ~FlagshipAgent() override;

    SkillManager& skillManager() { return *skillManager_; }
    SkillRuntime& skills() { return *skillRuntime_; }
    MemoryStore* memory() { return memory_.get(); }
    FlagshipTools& toolbelt() { return *tools_; }
    DocumentIndex& documents() { return *documents_; }
    CodeIndex& code() { return *code_; }
    const FlagshipOptions& options() const { return options_; }

    /// Load the manifest's always-on skills (gaia-voice) and @p requested ones.
    /// @throws SkillError when a requested skill is missing or refused.
    void loadStartupSkills(const std::vector<std::string>& requested);

protected:
    void registerTools() override;
    std::string getSystemPrompt() const override;
    /// Logs both sides of the turn for search_past_conversations.
    void onTurnComplete(const std::string& userInput, const std::string& answer) override;
    /// Looks up memories close to this turn's request for its context.
    void onTurnStart(const std::string& userInput) override;
    /// Working folder, allowed folders and this turn's memories.
    std::string turnContext() const override;
    /// Drops skill instructions from load_skill results; the system prompt carries them.
    void beforeHistorySave(std::vector<Message>& messages) override;

private:
    VisionReader visionReader();

    FlagshipOptions options_;
    std::unique_ptr<SkillManager> skillManager_;
    std::unique_ptr<SkillRuntime> skillRuntime_;
    std::unique_ptr<MemoryStore> memory_;
    std::unique_ptr<FlagshipTools> tools_;
    std::unique_ptr<DocumentIndex> documents_;
    std::unique_ptr<CodeIndex> code_;
    std::vector<std::string> turnMemories_;
};

}  // namespace gaia
