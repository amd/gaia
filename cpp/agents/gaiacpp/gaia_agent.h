// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// GaiaAgent (C++): the general-purpose desktop agent — files, documents, data,
// code, shell and web — built on gaia_core. The native counterpart of the
// Python flagship, packaged for embedding (e.g. `lemonade agents`).

#pragma once

#include <memory>
#include <mutex>
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
#include <filesystem>

namespace gaia {

class GaiaAgent : public Agent {
public:
    struct Options {
        bool webTools = true;  // web_fetch (still CONFIRM-gated)
        bool webOff = false;   // the model is told not to use the web, and shell fetches are refused
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
        // delegate_task: -1 = as many helpers at once as the model serves, 0 = no such tool,
        // N = that many at once (and the tool is offered to a compact model too).
        int maxHelpers = -1;
        int helperMaxSteps = 30;         // one helper's step budget
        int helperTimeoutSeconds = 600;  // one helper's wall-clock limit
    };

    /// delegate_task for this agent: runs read-only helpers on stated tasks.
    ToolInfo delegateTaskTool();

    /// What this turn's helpers did and used, for the run's JSON output.
    json helperStats() const;

    DocumentIndex* documents() { return docs_.get(); }

    explicit GaiaAgent(const AgentConfig& config);
    GaiaAgent(const AgentConfig& config, Options options);
    ~GaiaAgent() override;

protected:
    void registerTools() override;
    std::string getSystemPrompt() const override;

    /// Sends an answer back once when it says a file was saved that doesn't exist.
    std::optional<std::string> reviewFinalAnswer(const std::string& answer) override;
    std::optional<std::string> unresolvedClaims(const std::string& answer) override;
    std::optional<std::string> unconfirmedClaims(const std::string& answer) override;
    std::optional<std::string> announcedStepNote(const std::string& answer) override;

    /// Lists the working folder once, so every model call of the turn shows it as it began.
    void onTurnStart() override;

    UsageStats sideUsage() const override;

public:
    /// Files the answer says were created or saved, as written in it.
    static std::vector<std::string> claimedFiles(const std::string& answer);

    /// Whether the user's own words this turn forbid acting ("use no tools", "plan only").
    static bool asksForNoAction(const std::string& request);

    /// A tool call of the turn, as the answer checks below see it.
    struct CallSeen {
        std::string name;
        json args;
        std::string output;  // what the tool returned, clipped
        bool succeeded = true;
    };

    /// Why the turn's record doesn't back the answer's report that tests passed: no test
    /// ran, the latest run of each failed, the latest run of some test command after the
    /// last code change failed and the answer doesn't plainly say a test still fails (a
    /// narrower passing rerun doesn't hide it, and neither does "the 2 failing tests now pass"), every passing run came before a code change
    /// (a file tool or a shell edit), or ran in a folder apart from the changed file. A run
    /// whose result could be lost (`pytest | head -3`, `pytest || true`, `pytest; echo $?`,
    /// `pytest > out.txt`, `! pytest`, `pytest &`) counts only by its own summary, and is
    /// ignored when it has none. `first` is the claim as worded, `second` the reason.
    /// nullopt when there is no such claim, or the runs after the last change back it. A
    /// command counts as a test run when it names a runner (not to install it or ask its
    /// version) or its output holds a runner's summary that the command didn't just print.
    static std::optional<std::pair<std::string, std::string>> unsupportedTestClaim(
        const std::string& answer, const std::vector<CallSeen>& calls);

    /// A file or page the turn read holds text addressed to an AI assistant, and
    /// the answer doesn't tell the user about it.
    static std::optional<std::string> injectionNote(const std::string& answer, const std::vector<CallSeen>& calls);

    /// Why a write to path must not go ahead unasked: a lock file beside it, or a
    /// header saying another program owns it. "" when nothing guards it.
    static std::string guardedFileReason(const std::filesystem::path& path);

    /// Names under roots (two levels, dependency and hidden folders skipped) holding shell syntax.
    static std::vector<std::string> hostileNames(const std::vector<std::string>& roots);

    /// Why command must not run: it uses one of names outside any quotes, where the shell would
    /// run part of the name. "" when it is safe.
    static std::string unquotedHostileName(const std::string& command, const std::vector<std::string>& names);

    /// Why command must not run: it pastes five or more rows of a CSV in roots into a script
    /// instead of reading the file. "" when it doesn't.
    /// Why a one-line `python -c` script cannot run (a block statement after a semicolon); empty if it can.
    static std::string oneLineBlockRefusal(const std::string& command);

    static std::string pastedDataRefusal(const std::string& command, const std::vector<std::string>& roots);

    /// The small CSV a command names that file_read has not returned this turn; empty when there is none.
    static std::string unreadTable(const std::string& command, const std::vector<CallSeen>& calls,
                                   const std::vector<std::string>& roots);

    /// command with a backslash put before each ${name} or $name that a POSIX shell would
    /// replace, usually with nothing, inside an inline script it expands (python -c "...", or an
    /// unquoted heredoc). Unchanged when there is none, or when command or earlier (the turn's
    /// previous shell commands) sets that variable.
    static std::string keepScriptDollars(const std::string& command, const std::string& earlier = "");

    /// Why command must not run: the last three shell commands began the same way and printed
    /// the same output. "" otherwise.
    static std::string repeatedCommandRefusal(const std::string& command, const std::vector<CallSeen>& calls);

    /// How much text `dir` holds, for the prompt: "about 86,000 tokens of text in 16 files". Counts
    /// text and source files two levels deep; "" when there are fewer than two.
    static std::string textSizeOf(const std::filesystem::path& dir);

    /// text with every path-like token (one holding a slash) blanked out.
    static std::string withoutPaths(const std::string& text);

    /// The answer says something is absent while files under roots that could
    /// hold it were never opened: names them.
    static std::optional<std::string> unopenedFilesNote(const std::string& answer,
                                                        const std::vector<std::filesystem::path>& roots,
                                                        const std::vector<CallSeen>& calls);

    /// The answer offers to read or work something out instead of doing it.
    static std::optional<std::string> handBackNote(const std::string& answer,
                                                   const std::vector<CallSeen>& calls);

    /// The answer's last line says it will read or work out a named file, after tool calls this turn.
    static std::optional<std::string> announcedStepNote(const std::string& answer,
                                                        const std::vector<CallSeen>& calls);

    /// A script failed on a missing module and the answer stops there, with the script's inputs unread.
    static std::optional<std::string> failedScriptNote(const std::string& answer, const std::vector<CallSeen>& calls);

    /// A CSV was totalled by file_read and the answer reports a bare 0 for something.
    static std::optional<std::string> zeroForAbsentNote(const std::string& answer, const std::vector<CallSeen>& calls);

    /// The request asks what an order comes to and every figure in the answer is copied from a file.
    static std::optional<std::string> unitPriceOnlyNote(const std::string& request, const std::string& answer,
                                                        const std::vector<CallSeen>& calls);

    /// Where a bare-named new file belongs when the request says "next to" a file in a folder; empty otherwise.
    static std::string besidePath(const std::string& request, const std::string& writtenPath);

    /// The request numbers two or more questions and the answer is too short to cover them.
    static std::optional<std::string> skippedQuestionsNote(const std::string& request, const std::string& answer);

    /// The file asked for was missing, a near-named one was read, and the answer doesn't say so.
    static std::optional<std::string> swappedFileNote(const std::string& answer,
                                                      const std::vector<CallSeen>& calls);

    /// The answer gives totals or rates from a data file no program computed.
    static std::optional<std::string> uncomputedFiguresNote(const std::string& answer,
                                                            const std::vector<CallSeen>& calls);

    /// The answer gives two or more figures as saved, and saved (the text of the files it
    /// says it wrote) holds none of them. Figures the request itself gave don't count.
    static std::optional<std::string> unsavedFiguresNote(const std::string& answer,
                                                         const std::string& request,
                                                         const std::string& saved);

private:
    /// The answer checks. With factsOnly, just what can be read off the disk (a saved file
    /// that isn't there, figures the saved file lacks), worded for the user.
    std::optional<std::string> reviewAnswer(const std::string& answer, bool factsOnly);

    /// OS, working directory and date, appended to the system prompt.
    std::string systemContext() const;

    /// The chat's switches, checked before a tool can ask for approval.
    std::optional<json> switchRefusal(const ToolInfo& tool, const json& args) const;
    bool memoryOff() const { return options_.memoryOff && options_.memoryOff->load(); }

    Options options_;
    mutable std::mutex turnListingMu_;
    std::optional<std::string> turnListing_;  // unset until the first turn starts
    std::string turnTextSize_;                // how much text the working folder holds, in words for the prompt
    std::set<std::string> turnTablesPointedAt_;  // CSVs a command was sent to file_read for, once each
    std::vector<std::string> turnHostileNames_;  // file names holding shell syntax, as the turn began
    std::unique_ptr<SkillLibrary> skills_;
    std::unique_ptr<LemonadeClient> embedClient_;
    std::unique_ptr<DocumentIndex> docs_;
    std::unique_ptr<DocumentIndex> code_;

    void registerCodeTools(std::shared_ptr<const Workspace> ws);
    std::unique_ptr<MemoryStore> memory_;
    std::unique_ptr<LemonadeClient> visionClient_;

    json delegateTasks(const json& args);
    std::shared_ptr<const Workspace> workspace_;  // null with full access
    struct HelperTotals {
        int runs = 0, ok = 0, partial = 0, failed = 0, width = 0;
        UsageStats usage;
        double wallSeconds = 0.0;
    };
    mutable std::mutex helperMu_;  // the totals, and the step log and console while helpers run
    HelperTotals helperTotals_;

    void registerVisionTool(std::shared_ptr<const Workspace> ws);
};

} // namespace gaia
