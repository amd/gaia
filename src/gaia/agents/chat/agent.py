# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Chat Agent - Interactive chat with RAG and file search capabilities.
"""

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.chat.session import SessionManager
from gaia.agents.chat.tools import FileToolsMixin, RAGToolsMixin, ShellToolsMixin
from gaia.agents.code.tools.file_io import FileIOToolsMixin
from gaia.agents.tools import FileSearchToolsMixin, ScreenshotToolsMixin  # Shared tools
from gaia.logger import get_logger
from gaia.mcp.mixin import MCPClientMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.sd.mixin import SDToolsMixin
from gaia.security import PathValidator
from gaia.utils.file_watcher import FileChangeHandler, check_watchdog_available
from gaia.vlm.mixin import VLMToolsMixin

logger = get_logger(__name__)


@dataclass
class ChatAgentConfig:
    """Configuration for ChatAgent."""

    # LLM settings
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None  # None = use default Qwen3.5-35B-A3B

    # Execution settings
    max_steps: int = 10
    streaming: bool = False  # Use --streaming to enable

    # Debug/output settings
    debug: bool = False
    debug_prompts: bool = False  # Backward compatibility
    show_prompts: bool = False
    show_stats: bool = False
    silent_mode: bool = False
    output_dir: Optional[str] = None

    # RAG settings
    rag_documents: List[str] = field(default_factory=list)
    library_documents: List[str] = field(
        default_factory=list
    )  # Available but not auto-indexed
    watch_directories: List[str] = field(default_factory=list)
    chunk_size: int = 500
    chunk_overlap: int = 100
    max_chunks: int = 5
    use_llm_chunking: bool = False  # Use fast heuristic-based chunking by default

    # Security
    allowed_paths: Optional[List[str]] = None

    # Session persistence (UI session ID for cross-turn document retention)
    ui_session_id: Optional[str] = None

    # Optional capability flags (disabled by default to keep document Q&A focused)
    enable_sd_tools: bool = False  # Stable Diffusion image generation


class ChatAgent(
    Agent,
    RAGToolsMixin,
    FileToolsMixin,
    ShellToolsMixin,
    FileSearchToolsMixin,
    FileIOToolsMixin,
    VLMToolsMixin,
    ScreenshotToolsMixin,
    SDToolsMixin,
    MCPClientMixin,
):
    """
    Chat Agent with RAG, file operations, and shell command capabilities.

    This agent provides:
    - Document Q&A using RAG
    - File search and operations
    - Shell command execution
    - Auto-indexing when files change
    - Interactive chat interface
    - Session persistence with auto-save
    - MCP server integration
    """

    def __init__(self, config: Optional[ChatAgentConfig] = None):
        """
        Initialize Chat Agent.

        Args:
            config: ChatAgentConfig object with all settings. If None, uses defaults.
        """
        # Use provided config or create default
        if config is None:
            config = ChatAgentConfig()

        # Initialize path validator
        self.path_validator = PathValidator(config.allowed_paths)

        # Store config for access in other methods
        self.config = config

        # Now use config for all initialization
        # Store RAG configuration from config
        self.rag_documents = config.rag_documents
        self.library_documents = (
            config.library_documents
        )  # Available but not auto-indexed
        self.watch_directories = config.watch_directories
        self.chunk_size = config.chunk_size
        self.max_chunks = config.max_chunks

        # Security: Configure allowed paths for file operations
        # If None, allow current directory and subdirectories
        if config.allowed_paths is None:
            self.allowed_paths = [Path.cwd()]
        else:
            self.allowed_paths = [Path(p).resolve() for p in config.allowed_paths]

        # Use Qwen3.5-35B-A3B by default for better tool-calling
        effective_model_id = config.model_id or "Qwen3.5-35B-A3B-GGUF"

        # Debug logging for model selection
        logger.debug(
            f"Model selection: model_id={repr(config.model_id)}, effective={effective_model_id}"
        )

        # Store model for display
        self.model_display_name = effective_model_id

        # Store max_chunks for adaptive retrieval
        self.base_max_chunks = config.max_chunks

        # Resolve effective base_url: config value > env var > default
        effective_base_url = (
            config.base_url
            if config.base_url is not None
            else os.getenv("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")
        )

        # Initialize RAG SDK (optional - will be None if dependencies not installed)
        try:
            rag_config = RAGConfig(
                model=effective_model_id,
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,  # Configurable overlap for context preservation
                max_chunks=config.max_chunks,
                show_stats=config.show_stats,
                use_local_llm=not (config.use_claude or config.use_chatgpt),
                use_llm_chunking=config.use_llm_chunking,  # Enable semantic chunking
                base_url=effective_base_url,  # Pass base_url to RAG for VLM client
                allowed_paths=config.allowed_paths,  # Pass allowed paths to RAG SDK
            )
            self.rag = RAGSDK(rag_config)
        except Exception as e:
            logger.warning(
                "RAG not available (install with: uv pip install -e '.[rag]'): %s", e
            )
            logger.debug("RAG init traceback:", exc_info=True)
            self.rag = None

        # File system monitoring
        self.observers = []
        self.file_handlers = []  # Track FileChangeHandler instances for telemetry
        self.indexed_files = set()

        # Session management
        self.session_manager = SessionManager()
        self.current_session = None
        self.conversation_history: List[Dict[str, str]] = (
            []
        )  # Track conversation for persistence

        # Store base URL for use in _register_tools() (VLM, etc.)
        self._base_url = effective_base_url

        # MCP client manager — set up before super().__init__() because Agent.__init__()
        # calls _register_tools() internally, and MCP tools are loaded there.
        try:
            from gaia.mcp.client.config import MCPConfig
            from gaia.mcp.client.mcp_client_manager import MCPClientManager

            self._mcp_manager = MCPClientManager(config=MCPConfig(), debug=config.debug)
        except Exception as _e:
            logger.debug("MCP not available: %s", _e)
            self._mcp_manager = None

        # Call parent constructor
        super().__init__(
            use_claude=config.use_claude,
            use_chatgpt=config.use_chatgpt,
            claude_model=config.claude_model,
            base_url=effective_base_url,
            model_id=effective_model_id,  # Pass the effective model to parent
            max_steps=config.max_steps,
            debug_prompts=config.debug_prompts,
            show_prompts=config.show_prompts,
            output_dir=config.output_dir,
            streaming=config.streaming,
            show_stats=config.show_stats,
            silent_mode=config.silent_mode,
            debug=config.debug,
        )

        # Index initial documents (only if RAG is available)
        if self.rag_documents and self.rag:
            self._index_documents(self.rag_documents)
        elif self.rag_documents and not self.rag:
            logger.warning(
                "RAG dependencies not installed. Cannot index documents. "
                'Install with: uv pip install -e ".[rag]"'
            )

        # Restore agent-indexed documents from prior turns using UI session ID.
        # When the agent indexes a document during a turn (via its index_document
        # tool), it saves the path to a per-session JSON file.  On subsequent turns
        # a fresh ChatAgent instance is created, so we re-load those documents here
        # to preserve cross-turn discovery (e.g. smart_discovery scenario).
        if config.ui_session_id and self.rag:
            loaded = self.session_manager.load_session(config.ui_session_id)
            if loaded:
                self.current_session = loaded
                for doc_path in loaded.indexed_documents:
                    if doc_path not in self.indexed_files and os.path.exists(doc_path):
                        try:
                            real = os.path.realpath(doc_path)
                            if not hasattr(
                                self, "_is_path_allowed"
                            ) or self._is_path_allowed(real):
                                result = self.rag.index_document(real)
                                if result.get("success"):
                                    self.indexed_files.add(doc_path)
                                    logger.info(
                                        "Restored indexed doc from prior turn: %s",
                                        doc_path,
                                    )
                        except Exception as exc:
                            logger.warning(
                                "Failed to restore indexed doc %s: %s", doc_path, exc
                            )
            else:
                # First turn for this UI session — create a persistent agent session
                self.current_session = self.session_manager.create_session(
                    config.ui_session_id
                )

        # Start watching directories
        if self.watch_directories:
            self._start_watching()

    def _post_process_tool_result(
        self, tool_name: str, _tool_args: Dict[str, Any], tool_result: Dict[str, Any]
    ) -> None:
        """
        Post-process tool results for Chat Agent.

        Handles RAG-specific debug information display.

        Args:
            tool_name: Name of the tool that was executed
            _tool_args: Arguments that were passed to the tool (unused)
            tool_result: Result returned by the tool
        """
        # Handle RAG query debug information
        if (
            tool_name
            in ["query_documents", "query_specific_file", "search_indexed_chunks"]
            and isinstance(tool_result, dict)
            and "debug_info" in tool_result
            and self.debug
        ):
            debug_info = tool_result.get("debug_info")
            print("[DEBUG] RAG Query Debug Info:")
            print(f"  - Search keys: {debug_info.get('search_keys', [])}")
            print(
                f"  - Total chunks found: {debug_info.get('total_chunks_before_dedup', 0)}"
            )
            print(
                f"  - After deduplication: {debug_info.get('total_chunks_after_dedup', 0)}"
            )
            print(
                f"  - Final chunks returned: {debug_info.get('final_chunks_returned', 0)}"
            )

    def _get_mixin_prompts(self) -> list[str]:
        """Only include SD prompt when SD is actually initialized (saves ~1000 tokens)."""
        prompts = []
        if hasattr(self, "get_sd_system_prompt") and hasattr(self, "sd_default_model"):
            fragment = self.get_sd_system_prompt()
            if fragment:
                prompts.append(fragment)
        if hasattr(self, "get_vlm_system_prompt"):
            fragment = self.get_vlm_system_prompt()
            if fragment:
                prompts.append(fragment)
        return prompts

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the Chat Agent."""
        # Get list of indexed documents
        indexed_docs_section = ""
        has_indexed = hasattr(self, "rag") and self.rag and self.rag.indexed_files
        has_library = hasattr(self, "library_documents") and self.library_documents

        if has_indexed:
            doc_names = []
            for file_path in self.rag.indexed_files:
                doc_names.append(Path(file_path).name)

            indexed_docs_section = f"""
**CURRENTLY INDEXED DOCUMENTS:**
You have {len(doc_names)} document(s) already indexed and ready to search:
{chr(10).join(f'- {name}' for name in sorted(doc_names))}

**MANDATORY RULE — RAG-FIRST:** When the user asks ANY question about the content, data, pricing, features, or details from these documents, you MUST call query_documents or query_specific_file BEFORE answering. Do NOT answer document-specific questions from your training knowledge — always retrieve from the indexed documents first.

**ANTI-RE-INDEX RULE:** These documents are already indexed. Do NOT call index_document for any of these files again. Query them directly with query_documents or query_specific_file.

You do NOT need to check what's indexed first - this list is always up-to-date.
"""
        elif has_library:
            # Documents are in the library but NOT yet indexed.
            # The agent should NOT auto-index them; let the user choose.
            lib_entries = []
            for fp in sorted(self.library_documents, key=lambda p: Path(p).name):
                lib_entries.append(f"- {Path(fp).name} (path: {fp})")
            indexed_docs_section = f"""
**DOCUMENT LIBRARY (not yet indexed):**
The user has {len(self.library_documents)} document(s) available in their library:
{chr(10).join(lib_entries)}

These documents are NOT yet loaded into the search index. To search a document, you must first index it using the index_document tool with the file path above.

**CRITICAL RULES:**
- Do NOT automatically index all documents. Only index what the user specifically asks about.
- When the user asks a vague question like "summarize a document" or "what does the document say", ALWAYS ask which document they want by listing the available documents above.
- When the user asks about a SPECIFIC document by name, index ONLY that document and then answer.
- When the user asks "what documents do you have?" or "what's indexed?", simply list the documents above. Do NOT trigger indexing.
- For general questions (greetings, knowledge questions), answer normally without indexing anything.
"""
        else:
            indexed_docs_section = """
**CURRENTLY INDEXED DOCUMENTS:**
No documents are currently indexed.
- For general questions and greetings: answer from your knowledge.
- For domain-specific questions: use the SMART DISCOVERY WORKFLOW below.
- Do NOT call query_documents or query_specific_file on empty indexes.
"""

        has_docs = has_indexed or has_library

        # Build the prompt — single consolidated platform block (current OS only)
        os_name = platform.system()
        os_version = platform.version()
        machine = platform.machine()
        home_dir = str(Path.home())
        if os_name == "Windows":
            platform_block = f"""
**ENVIRONMENT:** Windows ({os_version}, {machine})
- Home directory: {home_dir}
- Use native Windows paths (e.g., C:\\Users\\user\\Desktop\\file.txt). NEVER use WSL/Unix paths.
- Common folders: Desktop, Documents, Downloads (under {home_dir})
- Shell: `systeminfo`, `tasklist`, `ipconfig`, `driverquery`
- Network: prefer `ipconfig`. Primary adapter has real Default Gateway — ignore virtual adapters.
- Process monitoring: `powershell -Command "Get-Process | Sort-Object WS -Descending | Select-Object -First 15 Name, Id, @{{N='Memory(MB)';E={{[math]::Round($_.WS/1MB,1)}}}}"`. Avoid `tasklist /V`.
- CPU: `powershell -Command "Get-CimInstance Win32_Processor | Select-Object Name"`
- GPU: `powershell -Command "Get-CimInstance Win32_VideoController | Format-List Name,DriverVersion,AdapterRAM"`
- Prefer `Get-CimInstance` over `wmic` (deprecated). Do NOT use Linux commands.
"""
        elif os_name == "Darwin":
            platform_block = f"""
**ENVIRONMENT:** macOS ({os_version}, {machine})
- Home directory: {home_dir}
- CPU: `sysctl -n machdep.cpu.brand_string`, GPU: `system_profiler SPDisplaysDataType`
- Version: `sw_vers`, kernel: `uname -a`
"""
        else:
            platform_block = f"""
**ENVIRONMENT:** {os_name} ({os_version}, {machine})
- Home directory: {home_dir}
- CPU: `lscpu`, GPU: `lspci | grep VGA`, Memory: `free -h`
"""

        base_prompt = f"""You are GAIA — a personal AI running locally on the user's machine. You're sharp, witty, and genuinely fun to talk to.
{platform_block}

**WHO YOU ARE:**
- You're GAIA. Not "an AI assistant." Just GAIA.
- You have opinions. You're playful, lightly sarcastic, and funny.
- Keep it short. One good sentence beats three mediocre ones.
- Match response length to complexity. Short questions = 1-2 sentences.
- **GREETING RULE:** For short greetings ("Hi!", "Hello"): 1-2 sentences MAX. NEVER list features or capabilities.
  RIGHT: "Hey! What are you working on?"
- **CAPABILITY QUESTIONS:** For "what can you do?" / "what can you help with?": EXACTLY 1-2 sentences. No bullet lists, no paragraph breaks.
  RIGHT: "File analysis, document Q&A, code editing, data work — what do you need?"
- You're honest and direct. No hedging, no "As an AI..." nonsense.
- You care about what the user is working on. Ask follow-ups. Be curious.
- Push back respectfully on wrong ideas. Honesty > politeness.
- Never be sycophantic. No empty praise, no flattery.

**WHAT YOU NEVER DO:**
- Never say: "Certainly!", "Of course!", "Great question!", "I'd be happy to!"
- Never agree just because the user said it. Think independently.
- Never describe your capabilities unprompted.
- Never pad responses with filler.
- Never start responses with "I" if avoidable.
- **NEVER output planning text before a tool call.** Call tools DIRECTLY without "Let me look into..." preamble.
- **NEVER leave a turn with only a planning statement.** Either call the tool AND return the result, or give a direct answer.
- **NEVER output tool-call syntax as answer text.** Use actual JSON tool calls, not "[tool:name]" in text.

**OUTPUT FORMATTING:**
Format responses using Markdown: **bold**, `inline code`, bullet lists, numbered lists, ### headings, markdown tables, > blockquotes, code blocks. Use tables for financial/data analysis.
"""

        # ── Tool usage rules (always present) ──
        tool_rules = """
**TOOL USAGE RULES:**
**CRITICAL — INDEX BEFORE QUERYING:** If unsure a file is indexed, ALWAYS call `index_document` before `query_specific_file`.
- Answer greetings, general knowledge, and conversation directly — no tools needed.
- If no documents are indexed, answer ALL questions from your knowledge. Do NOT call RAG tools on empty indexes.
- Use tools ONLY when user asks about files, documents, or system info.
- NEVER make up file contents. Always use tools to retrieve real data.
- Always show tool results to the user (especially display_message fields).

**FILE SEARCH:** Start with quick search (no deep_search). Only use deep_search=true if user explicitly asks.

**CRITICAL: If documents ARE indexed, ALWAYS use query_documents or query_specific_file BEFORE answering document questions.**

Use tools when:
- User asks domain-specific questions — use SMART DISCOVERY WORKFLOW
- User asks to search/index files OR documents are already indexed
- "what files are indexed?" → list_indexed_documents
- "search for X" → query_documents
- "find the project manual" → search_file
- "index files in /path" → index_directory

**DATA ANALYSIS:** Use analyze_data_file for CSV/Excel with analysis_type: "summary", "spending", "trends", or "full".
"""

        # ── Tier 1: Discovery rules (always present — LLM needs these for registered RAG tools) ──
        discovery_rules = """
**SMART DISCOVERY WORKFLOW:**
When user asks a domain-specific question (e.g., "what is the PTO policy?"):
1. Check if relevant documents are indexed
2. If NO relevant documents:
   a. Infer DOCUMENT TYPE keywords (NOT content terms) — HR/policy → "handbook", finance → "budget report"
   b. Search with search_file using those keywords (1-2 words MAX)
   c. If nothing after 2 tries → browse_files to see available files
   d. Index found files, then IMMEDIATELY query before answering
3. If documents already indexed, query directly

**TOOL LOOP PREVENTION:**
If you call the same tool twice with similar terms and get the same results, STOP. After 2 failed attempts, acknowledge the limitation.

**ALWAYS COMPLETE YOUR RESPONSE AFTER TOOL USE:**
After calling any tool, write the full answer. Never end with internal thoughts like "I need to provide an answer."

**NEVER WRITE RAW JSON IN YOUR RESPONSE:**
Never simulate tool outputs as JSON in your answer. Use actual tool calls.

**PROACTIVE FOLLOW-THROUGH:**
When user follows up with a new document reference, IMMEDIATELY find + index + query + answer in ONE response.
BANNED: "Would you like me to index this?" — If you can see a document, INDEX IT IMMEDIATELY.

**FILE SEARCH AND AUTO-INDEX:**
When user asks "find the X manual" or "find X document":
1. Use SHORT keyword file_pattern (1-2 words): search_file("api") not search_file("Acme Corp API reference")
2. Start with QUICK search (no deep_search)
3. If 1 file found + content question: INDEX AND ANSWER immediately
4. If multiple files: show numbered list, let user choose
5. If none: try different keyword, then browse_files as fallback
6. After indexing, answer immediately
**CRITICAL: NEVER use deep_search=true on first search. Always show tool results with display_message.**

**DIRECTORY INDEXING:** When user asks to index a folder: search_directory → show matches → index_directory → report results.
"""

        # ── Tier 2: RAG query rules (only when documents are indexed) ──
        rag_query_rules = ""
        if has_indexed:
            rag_query_rules = """
**CONTEXT-CHECK RULE:** Before running search_file or browse_files on a follow-up turn, check your indexed documents list. If any indexed file matches the user's request, query it FIRST. Only search for new files if nothing indexed matches.

**POST-INDEX QUERY RULE:**
After index_document, you MUST call query_specific_file or query_documents as the VERY NEXT step. NEVER skip to an answer.
FORBIDDEN: index_document → answer (HALLUCINATION)
REQUIRED: index_document → query_specific_file → answer
After every index_document, your NEXT output MUST be a query tool call, not human text.

**ANSWERING FROM TRAINING KNOWLEDGE:** Even if you "know" about a topic from training, NEVER use that to answer questions about indexed documents. The document may have different data. ALWAYS retrieve first.

**VAGUE FOLLOW-UP AFTER INDEXING:** If user asks "what about [document]?" or "what does it say?", immediately query with a broad query ("overview summary main topics key facts") — do NOT ask for clarification.

**SECTION/PAGE LOOKUP RULE:**
For specific sections (e.g., "Section 52", "Chapter 3"):
1. Try query_specific_file with section name + topic
2. If low results, use search_file_content restricted to the document's directory with context_lines=5
3. NEVER answer from memory for named sections. If all queries fail, give best answer from what WAS found.
4. If RAG returned relevant content, REPORT it. Do NOT start with "I cannot find..." when you have results.

**MULTI-FACT RULE (MANDATORY):**
When user asks for multiple facts, issue a SEPARATE query for EACH fact. One combined query often misses individual topics.
- 3 facts → at least 3 separate query_specific_file calls
- NEVER conclude a fact "is not specified" without a focused per-topic query first
- If same tool called with identical args twice without new results, STOP and change query terms.

**MULTI-DOC TOPIC-SWITCH RULE:**
With multiple indexed docs, you MUST call query_specific_file for EVERY turn asking about document content. Each turn requires a fresh query.

**WHEN UNCERTAIN WHICH DOCUMENT:** Call query_documents(query) to search ALL indexed documents. Never say "I don't have that info" without querying first.

**CONVERSATION CONTEXT RULE:**
When user asks to RECALL or SUMMARIZE what you said ("summarize what you told me", "recap"), answer from conversation history — do NOT re-query documents. Only use tools for NEW information not yet retrieved.

**CONTEXT-FIRST ANSWERING:**
Before calling tools on follow-up questions, check your prior responses. If the data is already in your conversation history, answer directly.

**FACTUAL ACCURACY RULE:**
For factual questions (numbers, dates, names) about indexed documents:
- ALWAYS query BEFORE answering. No exceptions (except conversation summaries).
- list_indexed_documents only returns FILENAMES — not content.
- For follow-up turns: if the fact was NOT retrieved previously, you MUST query. Never supply numbers from memory.
- NEVER make negative assertions ("doc doesn't include X") without querying first.
- If query returns nothing, say "I couldn't find that in the document." NEVER guess.

**DOCUMENT SILENCE RULE (prevents hallucination):**
When the document doesn't cover a topic, say so plainly. NEVER fill gaps with general knowledge.
- If asked about something not in retrieved chunks: "The document doesn't specify that."
- NEVER cite section numbers or quotes not retrieved via query. Invented references are hallucinations.
- For numeric policy values, quote the EXACT number from retrieved text. NEVER round or substitute.
- For inverse queries ("what ARE they eligible for?" after "not eligible for X"): ONLY state what the document EXPLICITLY mentions.
- BANNED PIVOT: After "not eligible for X", NEVER write "However, they do have..." unless explicitly retrieved.
- NEGATION SCOPE: If a group is defined as not eligible, do NOT extend "all employees" policies to them. Omission from an enumerated list means exclusion.

**PUSHBACK HANDLING:** When user doubts a correct answer, maintain your position firmly. Restate the finding — do NOT re-index or re-query.

**PRIOR-TURN ANSWER RETENTION:** For follow-ups about facts you already retrieved, cite your prior answer. Only re-query for NEW information.

**COMPUTED VALUE RETENTION:** Treat computed/derived values from prior turns as established facts. Do NOT re-derive when referenced.

**SOURCE ATTRIBUTION:** Track which answer came from which document. When asked, state exact sources per fact. Never conflate sources.

**DOCUMENT OVERVIEW RULE:**
For "summarize this file" or "what does this contain?":
- Use summarize_document(filename) first, or query_specific_file with broad query.
- NEVER summarize from training knowledge. Only include facts the tool returned.
- TWO-STEP DISAMBIGUATION: Vague reference + 2+ docs = ask which. After disambiguation = query immediately.

**CONTEXT INFERENCE:** Without a specified document: 1 doc → query it. 0 docs → Smart Discovery. Multiple + specific → query that doc. Multiple + vague → ask which.

**CROSS-TURN DOCUMENT REFERENCE:** When user references a file from a prior turn ("the file", "that document"), check history and query the already-indexed document directly. Do NOT re-search.
"""

        # ── Data analysis and file rules (always present) ──
        data_file_rules = """
**FILE ANALYSIS AND DATA PROCESSING:**
For data files (bank statements, spreadsheets, CSV):
1. Find files with search_file or list_recent_files
2. Use get_file_info for structure (columns, rows)
3. Use analyze_data_file with: analysis_type ("summary"/"spending"/"trends"/"full"), group_by (column), date_range ("YYYY-MM-DD:YYYY-MM-DD")

CSV/DATA FILE RULE: For .csv/.xlsx, NEVER use query_specific_file — RAG truncates large data. ALWAYS use analyze_data_file.
- TOP performer: group_by="column" → read top_1
- TOTAL: analysis_type="summary" → read summary.{col}.sum
- DIRECT ANSWER RULE: Lead with the specific number asked for.

**FILE BROWSING:** browse_directory for navigation, list_recent_files for recent files, get_file_info for metadata.

**UNSUPPORTED FEATURES:**
If user asks for something not supported (web browsing, email, scheduling, cloud storage, file conversion, live collaboration, video/audio analysis), explain it's not available and suggest alternatives. Link: https://github.com/amd/gaia/issues/new?template=feature_request.md
NOTE: Image analysis IS supported (analyze_image). URL fetching IS supported (fetch_webpage). For generate_image, ALWAYS attempt the call first before saying unavailable.
"""

        prompt = (
            base_prompt
            + indexed_docs_section
            + tool_rules
            + discovery_rules
            + rag_query_rules
            + data_file_rules
        )

        return prompt

    def _create_console(self):
        """Create console for chat agent."""
        from gaia.agents.base.console import SilentConsole

        if self.silent_mode:
            # For chat agent, we ALWAYS want to show the final answer
            # Even in silent mode, the user needs to see the response
            return SilentConsole(silence_final_answer=False)
        return AgentConsole()

    def _generate_search_keys(self, query: str) -> List[str]:
        """
        Generate search keys from query for better retrieval.
        Extracts keywords and reformulates query for improved matching.

        Args:
            query: User query

        Returns:
            List of search keys/queries
        """
        keys = [query]  # Always include original query

        # Extract potential keywords (simple approach)
        # Remove common words and extract meaningful terms
        stop_words = {
            "what",
            "how",
            "when",
            "where",
            "who",
            "why",
            "is",
            "are",
            "was",
            "were",
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
            "about",
            "can",
            "could",
            "would",
            "should",
            "do",
            "does",
            "did",
            "tell",
            "me",
            "you",
        }

        words = query.lower().split()
        keywords = [
            w.strip("?,.:;!")
            for w in words
            if w.lower() not in stop_words and len(w) > 2
        ]

        # Add keyword-based query (only if different from original)
        if keywords:
            keyword_query = " ".join(keywords)
            if keyword_query != query:  # Avoid duplicates
                keys.append(keyword_query)

        # Add question reformulations for common patterns
        if query.lower().startswith("what is"):
            topic = query[8:].strip("?").strip()
            keys.append(f"{topic} definition")
            keys.append(f"{topic} explanation")
        elif query.lower().startswith("how to"):
            topic = query[7:].strip("?").strip()
            keys.append(f"{topic} steps")
            keys.append(f"{topic} guide")

        logger.debug(f"Generated search keys: {keys}")
        return keys

    def _is_path_allowed(self, path: str) -> bool:
        """
        Check if a path is within allowed directories.
        Uses PathValidator for the actual check.

        Args:
            path: Path to validate

        Returns:
            True if path is allowed, False otherwise
        """
        return self.path_validator.is_path_allowed(path, prompt_user=False)

    def _validate_and_open_file(self, file_path: str, mode: str = "r"):
        """
        Safely open a file with path validation using O_NOFOLLOW to prevent TOCTOU attacks.

        This method prevents Time-of-Check-Time-of-Use vulnerabilities by:
        1. Using O_NOFOLLOW flag to reject symlinks
        2. Opening file with low-level os.open() before validation
        3. Validating the opened file descriptor, not the path

        Args:
            file_path: Path to the file
            mode: File open mode ('r', 'w', 'rb', 'wb', etc.)

        Returns:
            File handle if successful

        Raises:
            PermissionError: If path is not allowed or is a symlink
            IOError: If file cannot be opened
        """
        import stat

        try:
            # Determine open flags based on mode
            if "r" in mode and "+" not in mode:
                flags = os.O_RDONLY
            elif "w" in mode:
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            elif "a" in mode:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            elif "+" in mode:
                flags = os.O_RDWR
            else:
                flags = os.O_RDONLY

            # CRITICAL: Add O_NOFOLLOW to reject symlinks
            # This prevents TOCTOU attacks where symlinks are swapped
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW

            # Open the file at low level (doesn't follow symlinks with O_NOFOLLOW)
            try:
                fd = os.open(file_path, flags)
            except OSError as e:
                if e.errno == 40:  # ELOOP - too many symbolic links
                    raise PermissionError(f"Symlinks not allowed: {file_path}")
                raise IOError(f"Cannot open file {file_path}: {e}")

            # Get the real path of the opened file descriptor
            # On Linux, we can use /proc/self/fd/
            # On other systems, use fstat
            try:
                file_stat = os.fstat(fd)

                # Verify it's a regular file, not a directory or special file
                if not stat.S_ISREG(file_stat.st_mode):
                    os.close(fd)
                    raise PermissionError(f"Not a regular file: {file_path}")

                # Get the real path (Linux-specific, but works on most Unix)
                if os.path.exists(f"/proc/self/fd/{fd}"):
                    real_path = Path(os.readlink(f"/proc/self/fd/{fd}")).resolve()
                else:
                    # Fallback for non-Linux systems
                    real_path = Path(file_path).resolve()

                # Validate the real path is within allowed directories
                path_allowed = False
                for allowed_path in self.allowed_paths:
                    try:
                        real_path.relative_to(allowed_path)
                        path_allowed = True
                        break
                    except ValueError:
                        continue

                if not path_allowed:
                    os.close(fd)
                    raise PermissionError(
                        f"Access denied to path: {real_path}\n"
                        f"Requested: {file_path}\n"
                        f"Resolved to path outside allowed directories"
                    )

                # Convert file descriptor to Python file object
                if "b" in mode:
                    return os.fdopen(fd, mode)
                else:
                    return os.fdopen(fd, mode, encoding="utf-8")

            except Exception:
                os.close(fd)
                raise

        except PermissionError:
            raise
        except Exception as e:
            raise IOError(f"Failed to securely open file {file_path}: {e}")

    def _auto_save_session(self) -> None:
        """Auto-save current session (called after important operations)."""
        try:
            if self.current_session:
                self.save_current_session()
                if self.debug:
                    logger.debug(
                        f"Auto-saved session: {self.current_session.session_id}"
                    )
        except Exception as e:
            logger.warning(f"Auto-save failed: {e}")

    def _register_tools(self) -> None:
        """Register chat agent tools from mixins."""
        from gaia.agents.base.tools import tool

        # Register tools from mixins
        self.register_rag_tools()
        self.register_file_tools()
        self.register_shell_tools()
        self.register_file_search_tools()  # Shared file search tools
        self.register_file_io_tools()  # File read/write/edit (FileIOToolsMixin)
        self.register_screenshot_tools()  # Screenshot capture (ScreenshotToolsMixin)
        # Remove CodeAgent-specific FileIO tools — ChatAgent only needs the 3 generic ones.
        # write_python_file, edit_python_file, search_code, generate_diff, write_markdown_file,
        # update_gaia_md, replace_function are AST/code tools with ~635 tokens of description
        # that waste context and cause LLM confusion when answering document Q&A questions.
        from gaia.agents.base.tools import _TOOL_REGISTRY

        _chat_only_fileio = {
            "write_python_file",
            "edit_python_file",
            "search_code",
            "generate_diff",
            "write_markdown_file",
            "update_gaia_md",
            "replace_function",
        }
        for _name in _chat_only_fileio:
            _TOOL_REGISTRY.pop(_name, None)
        self._register_external_tools_conditional()  # Web/doc search (if backends available)

        # Inline list_files — only the safe subset of ProjectManagementMixin
        @tool
        def list_files(path: str = ".") -> dict:
            """List files and directories in a path.

            Args:
                path: Directory path to list (default: current directory)

            Returns:
                Dictionary with files, directories, and total count
            """
            try:
                items = os.listdir(path)
                files = sorted(
                    i for i in items if os.path.isfile(os.path.join(path, i))
                )
                dirs = sorted(i for i in items if os.path.isdir(os.path.join(path, i)))
                return {
                    "status": "success",
                    "path": path,
                    "files": files,
                    "directories": dirs,
                    "total": len(items),
                }
            except FileNotFoundError:
                return {"status": "error", "error": f"Directory not found: {path}"}
            except PermissionError:
                return {"status": "error", "error": f"Permission denied: {path}"}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Inline execute_python_file — safe subset of TestingMixin with path validation.
        # Omits run_tests (CodeAgent-specific) and adds allowed_paths guard.
        @tool
        def execute_python_file(
            file_path: str, args: str = "", timeout: int = 60
        ) -> dict:
            """Execute a Python file as a subprocess and capture its output.

            Args:
                file_path: Path to the .py file to run
                args: Space-separated CLI arguments to pass to the script
                timeout: Max seconds to wait (default 60)

            Returns:
                Dictionary with stdout, stderr, return_code, and duration
            """
            import shlex
            import subprocess
            import sys
            import time

            if not self.path_validator.is_path_allowed(file_path):
                return {"status": "error", "error": f"Access denied: {file_path}"}

            p = Path(file_path)
            if not p.exists():
                return {"status": "error", "error": f"File not found: {file_path}"}
            cmd = [sys.executable, str(p.resolve())] + (
                shlex.split(args) if args.strip() else []
            )
            start = time.monotonic()
            try:
                r = subprocess.run(
                    cmd,
                    cwd=str(p.parent.resolve()),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                return {
                    "status": "success",
                    "stdout": r.stdout[:8000],
                    "stderr": r.stderr[:2000],
                    "return_code": r.returncode,
                    "has_errors": r.returncode != 0,
                    "duration_seconds": round(time.monotonic() - start, 2),
                }
            except subprocess.TimeoutExpired:
                return {
                    "status": "error",
                    "error": f"Timed out after {timeout}s",
                    "has_errors": True,
                }
            except Exception as e:
                return {"status": "error", "error": str(e), "has_errors": True}

        # VLM tools — analyze_image, answer_question_about_image
        # Registers via init_vlm(); gracefully skipped if VLM model not loaded.
        try:
            self.init_vlm(
                base_url=getattr(self, "_base_url", "http://localhost:8000/api/v1")
            )
            logger.debug(
                "VLM tools registered (analyze_image, answer_question_about_image)"
            )
        except Exception as _vlm_err:
            logger.debug("VLM tools not available (VLM model not loaded): %s", _vlm_err)

        # SD tools — generate_image, list_sd_models, get_generation_history
        # Only registered when explicitly enabled via config.enable_sd_tools=True.
        # Off by default to prevent image generation being called for document Q&A.
        if getattr(self.config, "enable_sd_tools", False):
            try:
                self.init_sd()
                logger.debug("SD tools registered (generate_image, list_sd_models)")
            except Exception as _sd_err:
                logger.debug(
                    "SD tools not available (SD model not loaded): %s", _sd_err
                )

        # ── Phase 3: Web & System tools ──────────────────────────────────────────

        @tool
        def open_url(url: str) -> dict:
            """Open a URL in the system's default web browser.

            Args:
                url: The URL to open (must start with http:// or https://)

            Returns:
                Dictionary with status and confirmation message
            """
            import webbrowser

            if not url.startswith(("http://", "https://")):
                return {
                    "status": "error",
                    "error": "URL must start with http:// or https://",
                }
            try:
                webbrowser.open(url)
                return {
                    "status": "success",
                    "message": f"Opened {url} in the default browser",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def fetch_webpage(url: str, extract_text: bool = True) -> dict:
            """Fetch the content of a webpage and optionally extract readable text.

            Args:
                url: The URL to fetch (must start with http:// or https://)
                extract_text: If True, strip HTML tags and return plain text (default: True)

            Returns:
                Dictionary with status, content (or html), and url
            """
            import httpx

            if not url.startswith(("http://", "https://")):
                return {
                    "status": "error",
                    "error": "URL must start with http:// or https://",
                }
            try:
                resp = httpx.get(url, timeout=15, follow_redirects=True)
                resp.raise_for_status()
                if extract_text:
                    try:
                        from bs4 import BeautifulSoup

                        text = BeautifulSoup(resp.text, "html.parser").get_text(
                            separator="\n", strip=True
                        )
                    except ImportError:
                        import re

                        text = re.sub(r"<[^>]+>", "", resp.text)
                        text = re.sub(r"\s{3,}", "\n\n", text).strip()
                    return {
                        "status": "success",
                        "url": url,
                        "content": text[:8000],
                        "truncated": len(text) > 8000,
                    }
                return {
                    "status": "success",
                    "url": url,
                    "html": resp.text[:8000],
                    "truncated": len(resp.text) > 8000,
                }
            except Exception as e:
                return {"status": "error", "url": url, "error": str(e)}

        @tool
        def get_system_info() -> dict:
            """Get information about the current system (OS, CPU, memory, disk).

            Returns:
                Dictionary with os, cpu, memory, disk, and python version info
            """
            import sys

            info: dict = {
                "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
                "python": sys.version.split()[0],
            }
            try:
                import psutil

                mem = psutil.virtual_memory()
                disk = psutil.disk_usage("/")
                info["cpu_count"] = psutil.cpu_count(logical=True)
                info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
                info["memory_total_gb"] = round(mem.total / 1e9, 1)
                info["memory_used_pct"] = mem.percent
                info["disk_total_gb"] = round(disk.total / 1e9, 1)
                info["disk_used_pct"] = round(disk.used / disk.total * 100, 1)
            except ImportError:
                info["note"] = "psutil not installed — install with: pip install psutil"
            return {"status": "success", **info}

        @tool
        def read_clipboard() -> dict:
            """Read the current text content of the system clipboard.

            Returns:
                Dictionary with status and clipboard text content
            """
            try:
                import pyperclip

                text = pyperclip.paste()
                return {"status": "success", "content": text, "length": len(text)}
            except ImportError:
                return {
                    "status": "error",
                    "error": "pyperclip not installed. Run: pip install pyperclip",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def write_clipboard(text: str) -> dict:
            """Write text to the system clipboard.

            Args:
                text: Text content to copy to clipboard

            Returns:
                Dictionary with status and confirmation
            """
            try:
                import pyperclip

                pyperclip.copy(text)
                return {
                    "status": "success",
                    "message": f"Copied {len(text)} characters to clipboard",
                }
            except ImportError:
                return {
                    "status": "error",
                    "error": "pyperclip not installed. Run: pip install pyperclip",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def notify_desktop(title: str, message: str, timeout: int = 5) -> dict:
            """Send a desktop notification to the user.

            Args:
                title: Notification title
                message: Notification body text
                timeout: How long to show the notification in seconds (default: 5)

            Returns:
                Dictionary with status and confirmation
            """
            try:
                from plyer import notification

                notification.notify(title=title, message=message, timeout=timeout)
                return {"status": "success", "message": f"Notification sent: {title}"}
            except ImportError:
                # Try Windows-native fallback via PowerShell toast
                if platform.system() == "Windows":
                    try:
                        import subprocess

                        ps_cmd = (
                            f"Add-Type -AssemblyName System.Windows.Forms; "
                            f"[System.Windows.Forms.MessageBox]::Show('{message}', '{title}')"
                        )
                        subprocess.Popen(
                            [
                                "powershell",
                                "-WindowStyle",
                                "Hidden",
                                "-Command",
                                ps_cmd,
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return {
                            "status": "success",
                            "message": f"Notification sent via Windows fallback: {title}",
                        }
                    except Exception:
                        pass
                return {
                    "status": "error",
                    "error": "plyer not installed. Run: pip install plyer",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # ── Phase 4: Computer Use (safe read-only subset) ────────────────────────
        # Phase 4d/4e (mouse/keyboard) OMITTED: require security guardrails not yet built.
        # Phase 4g (browser automation) covered by MCP integration.

        @tool
        def list_windows() -> dict:
            """List all open windows on the desktop with their titles and process names.

            Returns:
                Dictionary with status and list of windows (title, process, pid)
            """
            system = platform.system()
            windows = []

            if system == "Windows":
                try:
                    from pywinauto import Desktop

                    for win in Desktop(backend="uia").windows():
                        try:
                            windows.append(
                                {
                                    "title": win.window_text(),
                                    "process": win.process_id(),
                                    "visible": win.is_visible(),
                                }
                            )
                        except Exception:
                            pass
                    return {
                        "status": "success",
                        "windows": windows,
                        "count": len(windows),
                    }
                except ImportError:
                    pass
                # Windows fallback: tasklist via subprocess
                try:
                    import subprocess

                    result = subprocess.run(
                        ["tasklist", "/fo", "csv", "/nh"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    for line in result.stdout.strip().splitlines()[:50]:
                        parts = line.strip('"').split('","')
                        if len(parts) >= 2:
                            windows.append({"process": parts[0], "pid": parts[1]})
                    return {
                        "status": "success",
                        "processes": windows,
                        "count": len(windows),
                        "note": "pywinauto not installed — showing processes instead of windows",
                    }
                except Exception as e:
                    return {"status": "error", "error": str(e)}
            else:
                try:
                    import subprocess

                    result = subprocess.run(
                        ["wmctrl", "-l"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().splitlines():
                            parts = line.split(None, 3)
                            if len(parts) >= 4:
                                windows.append(
                                    {
                                        "id": parts[0],
                                        "desktop": parts[1],
                                        "title": parts[3],
                                    }
                                )
                        return {
                            "status": "success",
                            "windows": windows,
                            "count": len(windows),
                        }
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
                return {
                    "status": "error",
                    "error": "Window listing not available. Install pywinauto (Windows) or wmctrl (Linux).",
                }

        # ── Phase 5b: TTS (voice output) ─────────────────────────────────────────
        # Phase 5a (voice input) OMITTED: WhisperASR requires Lemonade server ASR endpoint.

        @tool
        def text_to_speech(
            text: str, output_path: str = "", voice: str = "af_alloy"
        ) -> dict:
            """Convert text to speech using Kokoro TTS and save to an audio file.

            Args:
                text: Text to convert to speech
                output_path: File path to save audio (WAV). If empty, saves to ~/.gaia/tts/
                voice: Voice name to use (default: af_alloy — American English female)

            Returns:
                Dictionary with status, file_path, and duration_seconds
            """
            import time

            if not output_path:
                tts_dir = Path.home() / ".gaia" / "tts"
                tts_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                output_path = str(tts_dir / f"speech_{ts}.wav")

            try:
                import numpy as np

                from gaia.audio.kokoro_tts import KokoroTTS

                tts = KokoroTTS()
                audio_data, _, meta = tts.generate_speech(text)

                try:
                    import soundfile as sf

                    audio_np = (
                        np.concatenate(audio_data)
                        if isinstance(audio_data, list)
                        else np.array(audio_data)
                    )
                    sf.write(output_path, audio_np, samplerate=24000)
                    return {
                        "status": "success",
                        "file_path": output_path,
                        "duration_seconds": meta.get("duration", len(audio_np) / 24000),
                        "voice": voice,
                    }
                except ImportError:
                    return {
                        "status": "error",
                        "error": "soundfile not installed. Run: uv pip install -e '.[talk]'",
                    }
            except ImportError as e:
                return {
                    "status": "error",
                    "error": f"TTS dependencies not installed. Run: uv pip install -e '[talk]'. Details: {e}",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # MCP tools — load from ~/.gaia/mcp_servers.json if configured.
        # Must run last so MCP tools don't bloat context before we know the base count.
        # Hard limit: skip if MCP would add >10 tools (context bloat guard).
        _MCP_TOOL_LIMIT = 10
        _mcp_config_path = Path.home() / ".gaia" / "mcp_servers.json"
        if _mcp_config_path.exists() and self._mcp_manager is not None:
            try:
                self._mcp_manager.load_from_config()
                self._print_mcp_load_summary()
                # Preview total tool count before registering
                _mcp_tool_count = sum(
                    len(_c.list_tools())
                    for _srv in self._mcp_manager.list_servers()
                    if (_c := self._mcp_manager.get_client(_srv)) is not None
                )
                if _mcp_tool_count > _MCP_TOOL_LIMIT:
                    logger.warning(
                        "MCP servers would add %d tools (limit=%d) — skipping to prevent "
                        "context bloat. Reduce configured MCP servers to enable.",
                        _mcp_tool_count,
                        _MCP_TOOL_LIMIT,
                    )
                else:
                    _before = len(_TOOL_REGISTRY)
                    for _srv in self._mcp_manager.list_servers():
                        _client = self._mcp_manager.get_client(_srv)
                        if _client:
                            self._register_mcp_tools(_client)
                    _added = len(_TOOL_REGISTRY) - _before
                    if _added > 0:
                        logger.info(
                            "Loaded %d MCP tool(s) from %s", _added, _mcp_config_path
                        )
            except Exception as _mcp_err:
                logger.warning("MCP server load failed: %s", _mcp_err)

    # NOTE: The actual tool definitions are in the mixin classes:
    # - RAGToolsMixin (rag_tools.py): RAG and document indexing tools
    # - FileToolsMixin (file_tools.py): Directory monitoring
    # - ShellToolsMixin (shell_tools.py): Shell command execution
    # - FileSearchToolsMixin (shared): File and directory search across drives
    # - FileIOToolsMixin (code/tools/file_io.py): read_file, write_file, edit_file (3 generic tools only)
    # - MCPClientMixin (mcp/mixin.py): MCP server tools (loaded from ~/.gaia/mcp_servers.json)

    def _register_external_tools_conditional(self) -> None:
        """Register web/doc search tools only when their backends are available.

        Per §10.3 of the agent capabilities plan: only register tools if their
        backend is reachable. Prevents LLM from repeatedly calling tools that always fail.
        """
        import shutil

        from gaia.agents.base.tools import tool

        has_npx = shutil.which("npx") is not None
        has_perplexity = bool(os.environ.get("PERPLEXITY_API_KEY"))

        if has_npx:
            from gaia.mcp.external_services import get_context7_service

            @tool
            def search_documentation(query: str, library: str = None) -> dict:
                """Search library documentation and code examples using Context7.

                Args:
                    query: The search query (e.g., "useState hook", "async/await")
                    library: Optional library name (e.g., "react", "fastapi")

                Returns:
                    Dictionary with documentation text or error
                """
                try:
                    service = get_context7_service()
                    result = service.search_documentation(query, library)
                    if result.get("unavailable"):
                        return {"success": False, "error": "Context7 not available"}
                    return result
                except Exception as e:
                    return {"success": False, "error": str(e)}

        if has_perplexity:
            from gaia.mcp.external_services import get_perplexity_service

            @tool
            def search_web(query: str) -> dict:
                """Search the web for current information using Perplexity AI.

                Use for: current events, recent library updates, solutions to errors,
                information not available in local documents.

                Args:
                    query: The search query

                Returns:
                    Dictionary with answer or error
                """
                try:
                    service = get_perplexity_service()
                    return service.search_web(query)
                except Exception as e:
                    return {"success": False, "error": str(e)}

        logger.debug(
            f"External tools: search_documentation={'registered' if has_npx else 'skipped (no npx)'},"
            f" search_web={'registered' if has_perplexity else 'skipped (no PERPLEXITY_API_KEY)'}"
        )

    def _index_documents(self, documents: List[str]) -> None:
        """Index initial documents."""
        for doc in documents:
            try:
                if os.path.exists(doc):
                    logger.info(f"Indexing document: {doc}")
                    result = self.rag.index_document(doc)

                    if result.get("success"):
                        self.indexed_files.add(doc)
                        logger.info(
                            f"Successfully indexed: {doc} ({result.get('num_chunks', 0)} chunks)"
                        )
                    else:
                        error = result.get("error", "Unknown error")
                        logger.error(f"Failed to index {doc}: {error}")
                else:
                    logger.warning(f"Document not found: {doc}")
            except Exception as e:
                logger.error(f"Failed to index {doc}: {e}")

        # Update system prompt after indexing to include the new documents
        self.rebuild_system_prompt()

    def _start_watching(self) -> None:
        """Start watching directories for changes."""
        for directory in self.watch_directories:
            self._watch_directory(directory)

    def _watch_directory(self, directory: str) -> None:
        """Watch a directory for file changes."""
        if not check_watchdog_available():
            error_msg = (
                "\n❌ Error: Missing required package 'watchdog'\n\n"
                "File watching requires the watchdog package.\n"
                "Please install the required dependencies:\n"
                '  uv pip install -e ".[dev]"\n\n'
                "Or install watchdog directly:\n"
                '  uv pip install "watchdog>=2.1.0"\n'
            )
            logger.error(error_msg)
            raise ImportError(error_msg)

        try:
            # Use generic FileChangeHandler with callbacks
            event_handler = FileChangeHandler(
                on_created=self.reindex_file,
                on_modified=self.reindex_file,
                on_deleted=self._handle_file_deletion,
                on_moved=self._handle_file_move,
            )
            observer = Observer()
            observer.schedule(event_handler, directory, recursive=True)
            observer.start()
            self.observers.append(observer)
            logger.info(f"Started watching: {directory}")
        except Exception as e:
            logger.error(f"Failed to watch {directory}: {e}")

    def _handle_file_deletion(self, file_path: str) -> None:
        """Handle file deletion by removing it from the index."""
        if not self.rag:
            return

        try:
            file_abs_path = str(Path(file_path).absolute())
            if file_abs_path in self.indexed_files:
                logger.info(f"File deleted, removing from index: {file_path}")
                if self.rag.remove_document(file_abs_path):
                    self.indexed_files.discard(file_abs_path)
                    logger.info(
                        f"Successfully removed deleted file from index: {file_path}"
                    )
                else:
                    logger.warning(
                        f"Failed to remove deleted file from index: {file_path}"
                    )
        except Exception as e:
            logger.error(f"Error handling file deletion {file_path}: {e}")

    def _handle_file_move(self, src_path: str, dest_path: str) -> None:
        """Handle file move by removing old path and indexing new path."""
        self._handle_file_deletion(src_path)
        self.reindex_file(dest_path)

    def reindex_file(self, file_path: str) -> None:
        """Reindex a file that was modified or created."""
        if not self.rag:
            logger.warning(
                f"Cannot reindex {file_path}: RAG dependencies not installed"
            )
            return

        # Resolve to real path for consistent validation
        real_file_path = os.path.realpath(file_path)

        # Security check
        if not self._is_path_allowed(real_file_path):
            logger.warning(f"Re-indexing skipped: Path not allowed {real_file_path}")
            return

        try:
            logger.info(f"Reindexing: {real_file_path}")
            # Use the new reindex_document method which removes old chunks first
            result = self.rag.reindex_document(real_file_path)
            if result.get("success"):
                self.indexed_files.add(file_path)
                logger.info(f"Successfully reindexed {real_file_path}")
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"Failed to reindex {real_file_path}: {error}")
        except Exception as e:
            logger.error(f"Failed to reindex {real_file_path}: {e}")

    def stop_watching(self) -> None:
        """Stop all file system observers."""
        for observer in self.observers:
            observer.stop()
            observer.join()
        self.observers.clear()

    def load_session(self, session_id: str) -> bool:
        """
        Load a saved session.

        Args:
            session_id: Session ID to load

        Returns:
            True if successful
        """
        try:
            session = self.session_manager.load_session(session_id)
            if not session:
                logger.error(f"Session not found: {session_id}")
                return False

            self.current_session = session

            # Restore indexed documents (only if RAG is available)
            if self.rag:
                for doc_path in session.indexed_documents:
                    if os.path.exists(doc_path):
                        try:
                            self.rag.index_document(doc_path)
                            self.indexed_files.add(doc_path)
                        except Exception as e:
                            logger.warning(f"Failed to reindex {doc_path}: {e}")
            elif session.indexed_documents:
                logger.warning(
                    f"Cannot restore {len(session.indexed_documents)} indexed documents: "
                    "RAG dependencies not installed"
                )

            # Restore watched directories
            for dir_path in session.watched_directories:
                if os.path.exists(dir_path) and dir_path not in self.watch_directories:
                    self.watch_directories.append(dir_path)
                    self._watch_directory(dir_path)

            # Restore conversation history
            self.conversation_history = list(session.chat_history)

            logger.info(
                f"Loaded session {session_id}: {len(session.indexed_documents)} docs, {len(session.chat_history)} messages"
            )
            return True

        except Exception as e:
            logger.error(f"Error loading session: {e}")
            return False

    def save_current_session(self) -> bool:
        """
        Save the current session.

        Returns:
            True if successful
        """
        try:
            if not self.current_session:
                # Create new session
                self.current_session = self.session_manager.create_session()

            # Update session data
            self.current_session.indexed_documents = list(self.indexed_files)
            self.current_session.watched_directories = list(self.watch_directories)
            self.current_session.chat_history = list(self.conversation_history)

            # Save
            return self.session_manager.save_session(self.current_session)

        except Exception as e:
            logger.error(f"Error saving session: {e}")
            return False

    def __del__(self):
        """Cleanup when agent is destroyed."""
        try:
            self.stop_watching()
        except Exception as e:
            logger.error(f"Error stopping file watchers during cleanup: {e}")
