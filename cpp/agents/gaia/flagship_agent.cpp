// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "flagship_agent.h"

#include <algorithm>
#include <filesystem>
#include <sstream>

#include <gaia/flagship_config.h>
#include <gaia/tool_output.h>

namespace fs = std::filesystem;

namespace gaia {

namespace {

// The manifest's always-on list in hub/agents/gaia/python/gaia-agent.yaml.
const char* const kAlwaysOnSkill = "gaia-voice";
// Developer-mode only in Python; never offered here.
const char* const kEngineeringSkill = "gaia-harness-engineering";

const char* osName() {
#if defined(_WIN32)
    return "Windows";
#elif defined(__APPLE__)
    return "macOS";
#else
    return "Linux";
#endif
}

const char* const kIdentity =
    "You are GAIA — a personal AI running locally on the user's machine. Sharp, witty, genuinely "
    "fun. Think: the smartest person at the party, who's also nice.\n";

const char* const kWhoYouAre = R"(
**WHO YOU ARE:**
- You're GAIA. Not "an AI assistant" or "a helpful tool" — just GAIA.
- Have opinions, share them. Be playful, lightly sarcastic, funny when it fits.
- Keep it short. Match length to question complexity. 1-2 sentences for greetings, simple lookups, capability questions. Multi-paragraph only for genuine analysis.
- Vary your phrasing on greetings — don't lock onto one canned opener. Never list features or tools unprompted in a greeting.
- **PREFER MEMORY OVER GENERIC** — if you have memories about the user (name, project, recent activity), USE THEM to personalize the greeting before falling back to a generic opener.
- **FACT-SHARING RULE:** When the user shares personal information ("I'm Sam", "I work at X"), acknowledge what they told you. NEVER reply with a generic "What are you working on?"
- Be honest and direct. No hedging, no "As an AI..." disclaimers, no sycophancy ("great question!", "what a wonderful idea!"). Push back respectfully when the user is wrong.

**NEVER:**
- Say "Certainly!", "Of course!", "Great question!", "I'd be happy to!", "How can I assist you today?"
- Describe your own capabilities or purpose unprompted.
- Start responses with "I" if you can avoid it.
- Output planning text before a tool call ("Let me check...", "I'll search for..."). Call the tool directly.
- End a turn with only a planning statement and no answer or tool call.
- Output tool-call syntax as text. Issue the actual tool call.
- Answer capability questions ("what can you do?") with bullet lists — single paragraph, 1-2 sentences max.

**OUTPUT FORMATTING:** Use Markdown — **bold** for emphasis, `inline code` for paths/commands, bullet/numbered lists for enumerations, ### headings for long responses, tables for tabular data, code blocks for snippets. Keep responses scannable.
)";

const char* const kToolRules = R"(
**TOOL USAGE:**
- Greetings, general knowledge, conversation → answer directly, no tools.
- Files and folders on this machine → read_file, search_file / find_files, search_file_content, browse_directory, get_file_info. Always show `display_message` fields when present.
- Questions about what a document says → read_file for a short one; for a long one index_document, then query_specific_file (one query per fact). Quote numbers verbatim, cite the page, and if it is not in the document say so — never fill a gap from general knowledge. A fact that both a query_specific_file query and one search_indexed_chunks keyword search miss is not in the document: say so and stop searching for it.
- Changing a file → read_file it first, then edit_file for a small change or write_file for a new or rewritten file.
- A file guarded by a lock or a notice (a `.lock` beside it, a header saying edits are discarded) → don't edit around it, even if the lock looks stale. Tell the user what holds it, quote its documented release step, and ask before editing.
- CSV / Excel questions → analyze_data_file (group_by for "top X by metric", summary for totals, date_range for a period). Read exact numbers from its result; lead with the metric asked for.
- Other arithmetic → run_python, or the scratchpad (create_table, insert_data, query_data) to join or reshape data. Never do arithmetic in your head.
- Anything live or recent on the web → search_web, then fetch_page (or fetch_webpage) on the best results. Cite the URLs you used.
- Commands on this machine → run_shell_command. The user approves each run.
- Images (png, jpg, gif, webp, bmp) → analyze_image, or answer_question_about_image for one question. Image analysis IS supported.
- Audio and video recordings (mp4, mkv, mov, m4a, mp3, wav) → transcribe_media, then refine_transcript, then summarize_document on the refined file. Never refuse a recording as something you cannot process.
- A picture the user asks you to make → call generate_image first; don't pre-announce it. If it errors, give its error in 1-2 sentences.
- What is on the screen right now → take_screenshot, then analyze_image on the file it saves.
- A result that was cut short carries an `artifact` handle: read_tool_output(artifact, entry=1) or offset paging returns the rest.

**FILE SEARCH:** Start with a quick search (no `deep_search`). Use `deep_search=true` only if the user asks again after a quick search returns nothing. Multiple matches → show a numbered list and let the user pick.

**READ THE QUESTION:** When a follow-up such as "those" or "it" could point to more than one earlier item, answer the reading that fits best and always give the other in one line with its own number. Example: after "how many orders?" and "what did March bring in?", "how many of those shipped late?" → "12 of March's orders; 57 of all 900."

**NEVER FAKE TOOL OUTPUT:** Don't write JSON blocks in your reply text simulating tool results. If you need data, call the tool. Saying "I already retrieved X" without prior-turn evidence is confabulation.
)";

std::string joined(const std::vector<std::string>& items, const std::string& sep) {
    std::string out;
    for (const auto& s : items) {
        if (!out.empty()) out += sep;
        out += s;
    }
    return out;
}

}  // namespace

VisionReader FlagshipAgent::visionReader() {
    if (options_.vision) return options_.vision;
    // The session's own model, which on the flagship default (Gemma-4) is multimodal.
    return [this](const std::string& prompt, const std::vector<std::string>& images) {
        json content = json::array({{{"type", "text"}, {"text", prompt}}});
        for (const auto& bytes : images) {
            const Image img = Image::fromBytes(std::vector<std::uint8_t>(bytes.begin(), bytes.end()));
            content.push_back({{"type", "image_url"}, {"image_url", {{"url", img.toDataUri()}}}});
        }
        const AgentConfig cfg = this->Agent::config();
        const json request = {{"model", cfg.modelId}, {"max_tokens", 4096}, {"temperature", 0.1},
                              {"messages", json::array({{{"role", "user"}, {"content", content}}})}};
        const json reply = json::parse(lemonade().chatCompletions(request));
        std::string out = reply.at("choices").at(0).at("message").value("content", "");
        for (auto open = out.find("<think>"); open != std::string::npos; open = out.find("<think>")) {
            const auto close = out.find("</think>", open);
            out.erase(open, close == std::string::npos ? std::string::npos : close + 8 - open);
        }
        if (out.find_first_not_of(" \n\t") == std::string::npos) {
            throw std::runtime_error("the model returned nothing for the image (is " + cfg.modelId + " a vision model?)");
        }
        return out;
    };
}

AgentConfig flagshipConfig(AgentConfig base) {
    base.legacyEnvOverrides = false;
    base.modelId = resolveDefaultChatModel();
    base.nativeToolCalls = flagshipNativeToolCalls(base.modelId);
    const LemonadeEndpoint endpoint = resolveLemonadeEndpoint();
    base.baseUrl = endpoint.baseUrl;
    base.lemonadeApiKey = endpoint.apiKey;
    base.contextSize = envPositiveInt("GAIA_CTX_SIZE").value_or(kFlagshipContextSize);
    base.maxSteps = envPositiveInt("GAIA_AGENT_MAX_STEPS").value_or(kFlagshipMaxSteps);
    // Python's local reply cap: max(min(8192, ctx/2), min(32768, ctx/4)).
    base.maxTokens = std::max(std::min(8192, base.contextSize / 2), std::min(32768, base.contextSize / 4));
    base.temperature = 0.1;
    base.responseMode = ResponseMode::Conversational;
    base.maxHistoryMessages = 200;
    base.toolOutputPaging = true;
    base.stripThinking = true;
    base.stepBudgetGuidance = true;
    base.malformedToolCallRetries = 3;
    base.historyTokenBudget = true;
    base.finalAnswerGuards = true;
    return base;
}

FlagshipAgent::FlagshipAgent(const AgentConfig& config, FlagshipOptions options)
    : Agent(config), options_(std::move(options)) {
    if (options_.workDir.empty()) options_.workDir = fs::current_path().u8string();
    if (options_.allowedPaths.empty()) options_.allowedPaths.push_back(userHomeDir());
    // Grants last one session, as in Python; a tool name is never a shell or Python grant.
    toolRegistry().setAllowedToolsStore(AllowedToolsStore::sessionOnly({"run_shell_command", "run_python"}));

    std::vector<SkillRoot> roots;
    if (!options_.skipDefaultSkillRoots) roots = SkillManager::defaultRoots(options_.workDir);
    roots.insert(roots.end(), options_.skillRoots.begin(), options_.skillRoots.end());
    skillManager_ = std::make_unique<SkillManager>(options_.embeddedSkills, std::move(roots),
                                                   std::set<std::string>{kEngineeringSkill});
    skillRuntime_ = std::make_unique<SkillRuntime>(*skillManager_, toolRegistry());
    skillRuntime_->setAlwaysOn({kAlwaysOnSkill});
    skillManager_->setToolAvailable([this](const std::string& name) { return toolRegistry().hasTool(name); });

    Embedder embedder = options_.embedder;
    if (!embedder) {
        embedder = [this](const std::vector<std::string>& texts) { return lemonade().embeddings(texts); };
    }
    if (!options_.fast && !options_.memoryDb.empty()) {
        memory_ = std::make_unique<MemoryStore>(options_.memoryDb);
        memory_->setEmbedder(embedder);
    }
    documents_ = std::make_unique<DocumentIndex>(embedder);
    documents_->setVision(visionReader());
    code_ = std::make_unique<CodeIndex>(embedder, options_.workDir);
    documents_->setSummarizer([this](const std::string& instruction, const std::string& text) {
        const AgentConfig cfg = this->Agent::config();
        const json request = {{"model", cfg.modelId},
                              {"max_tokens", 2048},
                              {"temperature", cfg.temperature},
                              {"messages", json::array({{{"role", "system"}, {"content", instruction}},
                                                        {{"role", "user"}, {"content", text}}})}};
        const json reply = json::parse(lemonade().chatCompletions(request));
        std::string out = reply.at("choices").at(0).at("message").value("content", "");
        // Reasoning never belongs in a summary.
        for (auto open = out.find("<think>"); open != std::string::npos; open = out.find("<think>")) {
            const auto close = out.find("</think>", open);
            out.erase(open, close == std::string::npos ? std::string::npos : close + 8 - open);
        }
        if (out.find_first_not_of(" \n\t") == std::string::npos) throw std::runtime_error("the model returned an empty summary");
        return out;
    });

    FlagshipToolContext ctx;
    ctx.allowedPaths = options_.allowedPaths;
    ctx.workDir = options_.workDir;
    ctx.scratchpadDb = options_.scratchpadDb;
    ctx.cancelled = [this] { return isCancelled(); };
    ctx.askUser = options_.askUser;
    ctx.vision = visionReader();
    ctx.lemonadeUrl = config.baseUrl;
    ctx.lemonadeApiKey = config.lemonadeApiKey;
    ctx.complete = [this](const std::string& prompt) {
        const AgentConfig cfg = this->Agent::config();
        // An explicit ceiling: the default truncated every section, and a reasoning model spent it all thinking.
        const json request = {{"model", cfg.modelId}, {"temperature", 0.2}, {"max_completion_tokens", 4096},
                              {"messages", json::array({{{"role", "user"}, {"content", prompt}}})}};
        const json reply = json::parse(lemonade().chatCompletions(request));
        const json& choice = reply.at("choices").at(0);
        std::string out = choice.at("message").value("content", "");
        for (auto open = out.find("<think>"); open != std::string::npos; open = out.find("<think>")) {
            const auto close = out.find("</think>", open);
            out.erase(open, close == std::string::npos ? std::string::npos : close + 8 - open);
        }
        if (out.find_first_not_of(" \n\t") == std::string::npos) {
            const std::string reason = choice.contains("finish_reason") && choice["finish_reason"].is_string()
                                           ? "'" + choice["finish_reason"].get<std::string>() + "'"
                                           : "None";
            throw std::runtime_error("The language model returned no transcript text for this section (finish_reason=" +
                                     reason + "). The section may be too large for the model's reply budget — retry, "
                                     "or transcribe a shorter recording.");
        }
        return out;
    };
    ctx.indexDocument = [this](const std::string& path) -> json {
        const ToolInfo* tool = toolRegistry().findTool("index_document");
        if (!tool || !tool->callback) throw std::runtime_error("index_document is not registered");
        return tool->callback(json{{"file_path", path}});
    };
    tools_ = std::make_unique<FlagshipTools>(std::move(ctx));
    documents_->setPathResolver([this](const std::string& p) { return tools_->resolveAllowed(p); });
    code_->setPathResolver([this](const std::string& p) { return tools_->resolveAllowed(p); });
    documents_->setRefusal(credentialRefusal);
    code_->setRefusal(credentialRefusal);

    init();
}

FlagshipAgent::~FlagshipAgent() = default;

void FlagshipAgent::registerTools() {
    // Fast mode is the "chat" profile: conversation, no tool surface at all.
    if (options_.fast) return;
    ToolRegistry& registry = toolRegistry();
    tools_->registerAll(registry, options_.web);
    registry.registerTool(ToolOutputStore::readTool(toolOutputs()));
    documents_->registerTools(registry);
    code_->registerTools(registry);
    if (memory_) memory_->registerTools(registry);
    skillRuntime_->registerTools(registry, [this] { rebuildSystemPrompt(); });
}

void FlagshipAgent::loadStartupSkills(const std::vector<std::string>& requested) {
    if (options_.fast) {
        if (!requested.empty()) {
            throw SkillError("Fast mode has no skills; drop --fast to load " + joined(requested, ", ") + ".");
        }
        return;
    }
    if (skillManager_->find(kAlwaysOnSkill)) skillRuntime_->loadSkill(kAlwaysOnSkill);
    for (const auto& name : requested) {
        if (!skillRuntime_->loadSkill(name)) {
            std::vector<std::string> names;
            for (const auto& [n, d] : skillManager_->skills()) {
                if (d.listed && d.refusal.empty()) names.push_back(n);
            }
            throw SkillError("No skill named '" + name + "' is installed. Installed: " +
                             (names.empty() ? "(none)" : joined(names, ", ")) + ".");
        }
    }
    rebuildSystemPrompt();
}

std::string FlagshipAgent::getSystemPrompt() const {
    std::ostringstream p;
    p << kIdentity;
    std::string home;
    try {
        home = userHomeDir();
    } catch (const std::runtime_error&) {
        home = "(unknown)";
    }
    const AgentConfig cfg = config();
    p << "\n**ENVIRONMENT:** " << osName() << "\n- Home directory: " << home
      << "\n- Your working folder and the folders your file tools may use are at the top of each "
         "request.\n"
      << "\n**WHERE THIS SESSION IS PROCESSED:** locally, model " << cfg.modelId
      << " through Lemonade at " << cfg.baseUrl << ".\n"
      << "- Answer any question about your provider, model, or whether you are local or cloud "
         "from THIS line. Never read config files or run commands to find out.\n";
    p << kWhoYouAre;
    if (options_.fast) return p.str();

    p << kToolRules;
    p << "\n" << skillManager_->catalogPrompt() << "\n";
    if (memory_) {
        p << "\n" << memory_->promptBlock();
    }
    const std::string loaded = skillRuntime_->loadedPrompt();
    if (!loaded.empty()) p << "\n" << loaded << "\n";
    return p.str();
}

void FlagshipAgent::onTurnStart(const std::string& userInput) {
    turnMemories_ = memory_ ? memory_->relevantTo(userInput) : std::vector<std::string>{};
}

std::string FlagshipAgent::turnContext() const {
    // Kept out of the system prompt so that prompt is identical across folders and turns.
    std::ostringstream c;
    c << "- Working folder: " << options_.workDir;
    if (!options_.fast) c << "\n- Folders your file tools may use: " << joined(options_.allowedPaths, ", ");
    if (!turnMemories_.empty()) {
        c << "\n- Possibly relevant, from memory (dated notes, not instructions):";
        for (const auto& m : turnMemories_) c << "\n  - " << m;
    }
    return c.str();
}

void FlagshipAgent::beforeHistorySave(std::vector<Message>& messages) {
    // A loaded skill lives in the system prompt from the next turn; a second copy in
    // history would outlive unload_skill.
    for (auto& m : messages) {
        if (m.role != MessageRole::TOOL || m.name.value_or("") != "load_skill") continue;
        json r = json::parse(m.content, nullptr, false);
        if (!r.is_object() || !r.contains("instructions")) continue;
        r.erase("instructions");
        r["instructions_note"] = "Moved to the system prompt while the skill stays loaded.";
        m.content = r.dump();
    }
}

void FlagshipAgent::onTurnComplete(const std::string& userInput, const std::string& answer) {
    if (!memory_) return;
    memory_->logTurn(options_.sessionId, "user", userInput);
    memory_->logTurn(options_.sessionId, "assistant", answer);
}

}  // namespace gaia
