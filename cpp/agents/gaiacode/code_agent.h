// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// CodeAgent: gaiacode's coding agent on gaia_core. It reads, edits and runs a
// repository's code, and holds its answer to what the turn actually ran.

#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/file_snapshots.h>
#include <gaia/process.h>
#include <gaia/skill_library.h>
#include <gaia/workspace.h>

namespace gaia::code {

/// What a tool call of the turn did, as the test-claim check needs it, in order.
enum class TurnEvent { Edit, TestsPassed, TestsFailed };

class CodeAgent : public Agent {
public:
    struct Options {
        /// Folders the tools may touch; empty = the working directory. Ignored with fullAccess.
        std::vector<std::string> workspaceRoots;
        bool fullAccess = false;
        /// The shell commands and checks run in; unset = one per process.
        std::shared_ptr<ShellSession> shell;
        /// GitHub tools (gh_*); off for a run that must not reach GitHub.
        bool github = true;
        /// "owner/name" to work on instead of the git remotes' repository.
        std::string repo;
        /// GitHub writes this run may make: "comment", "pr".
        std::set<std::string> githubWrites;
        /// Load SKILL.md skills (bundled beside the binary, and the user's).
        bool skills = true;
        /// Set while the chat has internet access off: GitHub refuses until it clears.
        std::shared_ptr<std::atomic<bool>> internetOff;
    };

    CodeAgent(const AgentConfig& config, Options options);
    ~CodeAgent() override;

    /// Whether the answer says the tests (or the build) pass.
    static bool claimsTestsPass(const std::string& answer);

    /// The answer says the tests pass and no test run after the turn's last edit passed:
    /// a note to send the answer back with; nullopt when the claim holds or none is made.
    static std::optional<std::string> testClaimNote(const std::string& answer, const std::vector<TurnEvent>& events);

    /// What a finished tool call did: an edit, a passing or failing test run, or nothing
    /// (nullopt). Read from the tool's whole result as it returns, never a clipped copy.
    static std::optional<TurnEvent> classify(const std::string& tool, const json& args, const json& result);

    /// The test result a tool call reported, if it ran tests; nullopt when it ran none or its
    /// result can't be read. Says nothing about edits.
    static std::optional<TurnEvent> testResult(const std::string& tool, const json& args, const json& result);

    /// A fingerprint of every file git would see in `folder`'s repository (tracked and untracked,
    /// .gitignore honored), taken through a throwaway index; nullopt outside a git repository.
    static std::optional<std::string> treeFingerprint(const std::string& folder);

    /// What this turn's tool calls did so far, in order.
    std::vector<TurnEvent> events() const;

protected:
    void registerTools() override;
    std::string getSystemPrompt() const override;
    void onTurnStart() override;
    std::optional<std::string> reviewFinalAnswer(const std::string& answer) override;
    std::optional<std::string> unresolvedClaims(const std::string& answer) override;

public:
    bool recordsChanges() const override { return true; }
    void setChangeRecorder(std::shared_ptr<TurnRecorder> recorder) override;

protected:

private:
    /// `tool` with its callback reporting what it did to the turn's events.
    ToolInfo observed(ToolInfo tool);
    /// Why a shell command or file write must not run, by the checks gaiacpp shares; "" when it may.
    std::string guardRefusal(const std::string& tool, const json& args) const;

    Options options_;
    std::unique_ptr<SkillLibrary> skills_;
    /// The folders the tools are confined to; null with full access.
    std::shared_ptr<const Workspace> ws_;
    mutable std::mutex recorderMu_;
    std::shared_ptr<TurnRecorder> recorder_;
    mutable std::mutex eventsMu_;
    std::vector<TurnEvent> events_;
};

}  // namespace gaia::code
