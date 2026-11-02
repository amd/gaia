# Core tools that should become skills

The flagship offers **92 tools on every turn — 14,853 tokens of native tool schema** —
and about **3,900 of those tokens (26%) belong to capabilities that already have a skill,
or that only one workflow uses.** Hiding those tools until their skill loads saves that
cost on every turn, keeps the prompt cache warm, and gives Gemma-4-E4B fewer wrong tools
to pick from. Nothing has to be rewritten as skill code. The tools stay in core and are
only *offered* once a skill asks for them.

The first three moves, in order:

1. **Media and desktop extras behind their skills.** Clipboard, notifications, window
   listing, text-to-speech, music and sound generation, image generation. `music-gen`
   and `image-gen` already exist and name exactly these tools. Saves **1,386 tokens**
   (994 on compact).
2. **Email behind `inbox-triage`.** Eight tools that do nothing until a mailbox is
   connected. Saves **1,350 tokens**. On compact it also stops three email tools from
   leaking past the feature tier (540 tokens).
3. **Skill authoring and hub tools behind a bundled `skill-manager` skill.** Capture,
   lessons, status, install, search and remove. Saves **1,171 tokens** (656 on compact).

All three need one small framework change first (see [the mechanism](#the-mechanism)).

## How this was measured

- `python -m gaia.eval.tool_cost --agent gaia` on `dca0ef8f1`, deterministic skeleton
  (no MCP servers, no Context7 or Perplexity tools, `developer_mode` off, dynamic tools
  off, the shipped default). Token counts are tiktoken `cl100k_base`, the harness's
  proxy. Gemma's absolute counts differ, but the shares hold.
- Per-tool cost is each tool rendered alone through `_build_openai_tool_schemas`, the
  native path Gemma uses. Group totals are sums of those.
- The 8 live-browser tools only register with the `[browser]` extra. They were measured
  separately with the driver check stubbed: 972 tokens, so 15,825 in total with
  Playwright installed.
- **Compact tier today:** `model_features.COMPACT` withholds scratchpad, five email
  tools, image generation and three hub tools, 2,308 tokens. Gemma-4-E4B therefore
  sees about **12,545 tokens**.
- Eval coverage is a word match of each tool name across `eval/scenarios/*`.
- Per-tool counts add up to about 90 tokens more than the rendered total, because the
  JSON array wrapping tokenizes differently. The savings below use the per-tool sums.

## Every tool group

"Compact saving" is what Gemma-4-E4B stops paying. A tool the tier already withholds
saves nothing there.

| Group | Tools | Tokens (full / compact) | Recommendation | Why | Evals to re-run |
|---|---|---:|---|---|---|
| **Desktop extras** | `read_clipboard`, `write_clipboard`, `notify_desktop`, `list_windows`, `text_to_speech` | 534 / 534 | **Gate behind a new bundled `desktop` skill** | Rare, single-shot asks. One scenario in all of `eval/`. Registered inline in `ChatAgent`, not even a mixin | `web_system`, `tool_selection`, `gaia_tool_selection` |
| **Music and sound generation** | `generate_music`, `generate_sound_effect`, `list_audio_generation_models` | 460 / 460 | **Gate behind `music-gen`** (exists) | The skill's `tools_required` is exactly these three. No scenario calls them | `gaia_media`, `gaia_skills_tasks` + a new load-path scenario |
| **Image generation** | `generate_image`, `list_sd_models`, `get_generation_history` | 392 / 0 | **Gate behind `image-gen`** (exists) | Already withheld on compact. Its prompt persona was already moved into the skill (`_get_mixin_prompts`) | `vision`, `adversarial` (injected image requests) |
| **Email** | 8 tools from `EmailToolsMixin` | 1,350 / 540 | **Gate behind `inbox-triage`** (exists) | Useless without a connected mailbox. Compact passed 2/12 and followed phishing. `FEATURE_TOOLS["email"]` lists only 5 of the 8 tools, so `get_thread`, `list_connected_mailboxes` and `list_waiting_on_you` still reach compact | `gaia_email`, `adversarial`, `gaia_skills_tasks` |
| **Skill authoring and hub** | `capture_skill`, `remember_skill_lesson`, `skill_status`, `install_skill`, `search_skill_hub`, `remove_skill` | 1,171 / 656 | **Gate behind a bundled `skill-manager` skill** | Library management, not task work. Hub half already withheld on compact | `gaia_skills_capture`, `gaia_skills_lifecycle` |
| **Live browser** (`[browser]` extra) | 8 `browser_*` tools | 972 / 972 (when installed) | **Gate behind a bundled `browse-live` skill** | Opt-in extra, signed-in sessions and click confirmations. Fetching pages covers most web asks. No scenario calls them | `gaia_web` + a new scenario (none exists) |
| **Media transcription** | `transcribe_media`, `refine_transcript` | 460 / 460 | **Gate behind `transcribe-meeting`** (exists), second wave | Single workflow. But the prompt promises "never refuse an .mp4", so the catalogue line must catch a bare "transcribe this" first | `gaia_media`, `gaia_voice` |
| **Code index** | `index_codebase`, `search_code_index`, `get_index_status`, `clear_code_index` | 524 / 524 | **Register only inside a code repository**, not a skill | `is_code_repository` is already imported. Outside a repo they cannot help. `coding` requires `search_code_index`, so it stays offered whenever that skill can run | `gaia_code`, `gaia_git` |
| **Data analysis** | `analyze_data_file` + scratchpad (5) | 946 / 355 | **Stay core for now** | CSV and Excel questions are common and heavily evaluated. The prompt rules route to `analyze_data_file` by name. Revisit after the first three moves | (`gaia_data`, `gaia_tool_selection`, `real_world` if moved) |
| **Duplicate tools** | `file_info` vs `get_file_info`, `fetch_webpage` vs `fetch_page`, `execute_python_file` vs `run_python`, `list_files`/`search_directory` vs `browse_directory`/`find_files`, plus `bookmark` and the unevaluated `list_recent_files` | ~1,000 / ~1,000 | **Delete or merge, not a skill** | Two file mixins and inline `ChatAgent` tools grew separately and overlap. This is the largest saving that helps compact, and needs no load step | `gaia_files`, `gaia_shell`, `gaia_web`, `tool_selection`, `gaia_tool_selection` |
| **RAG debug** | `evaluate_retrieval`, `add_watch_directory` | 153 / 153 | **Developer mode only** | No scenario uses either | `rag_quality`, `gaia_rag` |
| Memory | `remember`, `recall`, `update_memory`, `forget`, `search_past_conversations` | 981 | **Stays core** | In `FULL_CORE_TOOLS`. Six skills need it, and the agent should recall without being asked | — |
| Loop control | `set_loop_state`, `request_user_input`, `sleep`, `read_tool_output` | 823 | **Stays core** | The agent loop and truncated-output recovery depend on it | — |
| File I/O | `read_file`, `write_file`, `edit_file` | 771 | **Stays core** | The universal entry points, used by five skills | — |
| File search and browse | `find_files`, `tree`, `search_file`, `search_file_content`, `browse_directory`, `get_file_info` | 1,250 | **Stays core** (after the duplicate cleanup) | "Find my tax PDF" is a first-turn ask with no skill in sight. `gaia_files` covers it | — |
| Shell | `run_shell_command`, `wait_for_condition`, `get_shell_state`, `reset_shell_session`, `get_system_info`, `run_python` | 1,149 | **Stays core** | Safety-gated by the permission prompt and per-binary grants, which only core code can hold | — |
| CLI setup | `check_cli_setup`, `install_cli`, `sign_in_cli` | 499 | **Stays core** | `load_skill` refuses a skill whose CLI is missing, so the remedy must exist before any skill loads | — |
| Skill loading | `load_skill`, `unload_skill`, `list_skills` | 458 | **Stays core** | The catalogue tells the model to call `load_skill`. Gating needs it | — |
| Web | `search_web`, `fetch_page`, `download_file`, `open_url` | 825 | **Stays core** | Used by five skills and by voice. The cheapest grounding source | — |
| RAG core | `query_documents`, `query_specific_file`, `index_document`, `index_directory`, `list_indexed_documents`, `summarize_document`, `dump_document`, `search_indexed_chunks`, `rag_status` | 821 | **Stays core** | Document Q&A is the product's main use. `rag_quality` is the strictest gate | — |
| Vision | `analyze_image`, `answer_question_about_image`, `take_screenshot` | 348 | **Stays core** | Attachments arrive without warning. Gemma is the vision model | — |

**No core tool should move into a skill's own `tools.py`.** That path only suits
network-only code like `rss-digest`'s `fetch_rss`, for three reasons:

- `refuse_unbridged_permissions` refuses any skill that declares filesystem, shell,
  desktop, database or env access, and every candidate above needs one of those.
- Bundled tools are renamed `<skill>/<tool>`. That breaks every scenario, procedural
  memory and `tools_required` entry that names the tool.
- Bundled code from a captured skill stays inert until `gaia skill promote`.

Keep `tools.py` for new network-only capabilities.

## The mechanism

What exists today:

- `load_skill` registers a skill's *own* tools.
- `_offer_skill_tools` adds a skill's `tools_required` to the per-turn subset, but only
  when the dynamic tool loader is on, and it is off by default.
- `_offer_features` already withholds tools from `_instance_tools` and gives them back.

What is missing is a third state between "offered" and "withheld by tier": **registered,
hidden until a skill names it**. The change, roughly:

- A `SKILL_GATED_TOOLS` table next to `FEATURE_TOOLS`, mapping each group to the skills
  that unlock it.
- At registration, pop gated tools into a held set, the way `_offer_features` does.
- In `load_skill`, put back the held tools the skill's `tools_required` names, appended
  at the end of the tool list and never re-sorted, so the cached prefix survives. Hide
  them again in `unload_skill` once no loaded skill still needs them.
- `_withheld_skill_tools` must keep treating only *tier*-withheld tools as blocking.
  Otherwise every gated skill refuses to load.

**Why this, not the dynamic loader:** the loader was turned off because it changed the
tool list on most turns. That broke the backend's prompt cache (17 s to first token
against 0.4 s) and sometimes left the right tool out. A gated tool changes the list
once, when the model asks for the skill. Every other turn is byte-identical.

## What breaks if a tool appears only after `load_skill`

- **One-turn asks that skip the catalogue.** "Copy that to my clipboard" fails if the
  model answers without calling `load_skill`. This is the main risk. Each move needs a
  selection scenario in which the model has to load the skill first.
  `eval/scenarios/gaia_skills_selection/` on `claude/skills-hub-evaluation-plan-b4e2a7`
  is the place for them. None of `music-gen`, `image-gen` or `inbox-triage` has a
  load-path scenario today.
- **Prompt text that names hidden tools.** The data rules advertise `generate_image`
  and `transcribe_media` by name. They also say "UNSUPPORTED: Email" while eight email
  tools are registered. Each move must make its prompt line say "load the X skill".
- **Recalled procedures** whose `tools_required` names a gated tool. They should
  unlock it the same way a skill does, or the procedure stalls.
- **Compact models:** `load_skill` stays on compact. A gated skill whose tools are also
  tier-withheld (`image-gen`, `inbox-triage`) must keep failing with the existing "needs
  a larger model" message.

## Savings at a glance

| Step | Full tier | Compact |
|---|---:|---:|
| Today | 14,853 | ~12,545 |
| Top three moves | −3,907 (26%) | −2,190 (17%) |
| + code index outside repos, transcription, RAG debug | −1,137 | −1,137 |
| + duplicate cleanup | ~−1,000 | ~−1,000 |
| **After all** | **~8,800** | **~8,200** |

The duplicate cleanup is the biggest win on compact that needs no new mechanism. It can
run in parallel with the gating work.

## Findings outside this plan

- `FEATURE_TOOLS["email"]` misses `get_thread`, `list_connected_mailboxes` and
  `list_waiting_on_you`. Compact models are offered three email tools the tier is meant
  to withhold.
- The data-file rules say email is unsupported, yet the flagship registers eight email
  tools on the full tier.
