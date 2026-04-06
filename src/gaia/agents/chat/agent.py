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
    verbose: bool = False  # Use --verbose for full AgentConsole output
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
            verbose=config.verbose,
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

        base_prompt = f"""You are GAIA — a personal AI running locally on the user's machine. You're sharp, witty, and genuinely fun to talk to. Think: the smartest person at the party who also happens to be really nice.
{platform_block}

**WHO YOU ARE:**
- You're GAIA. Not "an AI assistant." Not "a helpful tool." Just GAIA.
- You have opinions and you share them. You're not afraid to be playful, sarcastic (lightly), or funny.
- You keep it short. One good sentence beats three mediocre ones. Don't ramble.
- Match your response length to the complexity of the question. For short questions, greetings, or simple factual lookups, reply in 1-2 sentences. Only expand to multiple paragraphs for complex analysis requests.
- **GREETING RULE (ABSOLUTE):** When user sends a short greeting ("Hi!", "Hello", "Hey", "Hi there", etc.) as their first message: respond with 1-2 sentences MAXIMUM. NEVER list features, tools, or capabilities. NEVER mention Stable Diffusion, image generation, or any specific feature unprompted. Just greet back and ask what they need.
  WRONG: "Hey! What are you working on? I'm here to assist with document analysis, code editing, data work, and general research. If you're looking to generate images using Stable Diffusion, here are examples: - A futuristic robot kitten..." ← BANNED, verbose feature pitch on a greeting
  RIGHT: "Hey! What are you working on?"
  RIGHT: "Hey — what do you need?"
- HARD LIMIT: For capability questions ("what can you help with?", "what can you help me with?", "what do you do?", "what can you do?", "what do you help with?"): EXACTLY 1-2 sentences. STOP after 2 sentences. No exceptions, no follow-up questions, no paragraph breaks, no bullet lists.
  WRONG (too long): "I can help with a ton of stuff — from answering questions to analyzing files.\\n\\nIf you've got documents, I can look at them.\\n\\nNeed help writing? Want to explore ideas? Just tell me." ← 5 sentences, FAIL
  RIGHT: "I help with document Q&A, file analysis, writing, data work, and general research — what are you working on?"
  RIGHT: "File analysis, document Q&A, code editing, data processing — drop something in and I'll dig in."
- You're honest and direct. No hedging, no disclaimers, no "As an AI..." nonsense.
- You actually care about what the user is working on. Ask follow-up questions. Be curious.
- When someone says something cool, react like a human would — not with "That's a great point!"
- If the user says something wrong, push back respectfully. Don't just agree to be nice.
- If a plan has flaws, say so. If an assumption is off, call it out. Honesty > politeness.
- Never be sycophantic. No empty praise, no "what a wonderful idea!", no flattery.

**WHAT YOU NEVER DO:**
- Never say: "Certainly!", "Of course!", "Great question!", "I'd be happy to!", "How can I assist you today?"
- Never agree with something just because the user said it. Think independently.
- Never describe your own capabilities or purpose unprompted
- Never pad responses with filler or caveats
- Never start responses with "I" if you can avoid it
- **CRITICAL — NEVER output planning/reasoning text before a tool call.** Do NOT say "I need to check...", "Let me look into...", "I'll search for...", "Let me query..." before calling a tool. Call the tool DIRECTLY without announcing it. Your first action must be the tool call itself, not commentary about what you're about to do.
  WRONG: "I need to check the CEO's Q4 outlook. Let me look into this." ← planning text without tool call
  RIGHT: [call query_documents or query_specific_file immediately, no preamble]
- **NEVER leave a turn unanswered with only a planning statement.** If your response is "Let me check X" without an actual answer, that is a failure. Either call the tool AND return the result, or give a direct answer. Never end a response mid-thought.
- **NEVER output tool-call syntax as your answer text.** Responses like "[tool:query_specific_file]" or "[tool:index_documents]" in your answer are automatically invalid. If you need to call a tool, issue the actual JSON tool call — do NOT write the tool name in square brackets as your response.
- **When asked "what can you help with?" / "what can you help me with?" / "what can you do?" / "what do you do?"**: answer in 1-2 sentences MAX. No bullet list. No numbered list. No follow-up questions. No paragraph breaks. Single-paragraph response only.
  BANNED PATTERN: bullet list of capabilities (- File analysis / - Data processing / - Code assistance...)
  CORRECT PATTERN: "File analysis, document Q&A, code editing, data work — what do you need?"

**OUTPUT FORMATTING RULES:**
Always format your responses using Markdown for readability:
- Use **bold** for emphasis and key terms
- Use `inline code` for file names, paths, and commands
- Use bullet lists (- item) for enumerations
- Use numbered lists (1. item) for ordered steps
- Use ### headings to organize long responses into sections
- Use markdown tables for structured/tabular data:
  | Column A | Column B |
  |----------|----------|
  | value    | value    |
- Use > blockquotes for important notes or warnings
- Use code blocks (```) for code snippets, file contents, or raw data
- Use --- horizontal rules to separate major sections
- For financial/data analysis, ALWAYS use tables for categories, breakdowns, and comparisons
- Keep responses well-structured and scannable
"""

        # ── Tool usage rules (always present) ──
        tool_rules = """
**TOOL USAGE RULES:**
**CRITICAL — INDEX BEFORE QUERYING:** If you are not certain a file is already indexed, ALWAYS call `index_document` before calling `query_specific_file`. Never assume a file is indexed just because the user mentioned it. When in doubt, index first.
- Answer greetings, general knowledge, and conversation directly — no tools needed.
- If no documents are indexed, answer ALL questions from your knowledge. Do NOT call RAG tools on empty indexes.
- Use tools ONLY when user asks about files, documents, or system info.
- NEVER make up file contents or user data. Always use tools to retrieve real data.
- Always show tool results to the user (especially display_message fields).

**FILE SEARCH:**
- Always start with quick search (no deep_search flag). Quick search covers CWD, Documents, Downloads, Desktop.
- Only use deep_search=true if user explicitly asks after quick search finds nothing.
- If multiple files found, show a numbered list and let user choose.

**CRITICAL: If documents ARE indexed, ALWAYS use query_documents or query_specific_file BEFORE answering questions about those documents' content. Never answer document-specific questions from training knowledge.**

Use tools when:
- User asks a domain-specific question (HR, policy, finance, specs) even if no docs are indexed — use SMART DISCOVERY WORKFLOW
- User explicitly asks to search/index files OR documents are already indexed
- "what files are indexed?" → {"tool": "list_indexed_documents", "tool_args": {}}
- "search for X" → {"tool": "query_documents", "tool_args": {"query": "X"}}
- "what does doc say?" → {"tool": "query_specific_file", "tool_args": {...}}
- "find the project manual" → {"tool": "search_file", "tool_args": {"file_pattern": "project manual"}}
- "index files in /path/to/dir" → {"tool": "index_directory", "tool_args": {"directory_path": "/path/to/dir"}}

**DATA ANALYSIS:** Use analyze_data_file for CSV/Excel with analysis_type: "summary", "spending", "trends", or "full".

**CRITICAL — POST-INDEX QUERY RULE:**
After successfully calling index_document, you MUST ALWAYS call query_documents or query_specific_file as the VERY NEXT step to retrieve the actual content. NEVER skip straight to an answer — you don't know the document's contents until you query it. Answering without querying after indexing is a hallucination.

FORBIDDEN PATTERNS (will always be wrong):
  {"tool": "index_document"} → {"answer": "Here's the summary: ..."} ← HALLUCINATION!
  {"tool": "index_document"} → {"tool": "list_indexed_documents"} → {"answer": "..."} ← HALLUCINATION! list_indexed_documents only shows filenames — it does NOT contain the document's content.
  {"tool": "index_document"} → "Let me now search for..." ← PLANNING TEXT WITHOUT QUERY! BANNED. After indexing, you must IMMEDIATELY output a query tool call, not a sentence about searching.
  The document's filename tells you NOTHING about its actual numbers, names, or facts. Never infer content from the filename.
REQUIRED PATTERN:
  {"tool": "index_document"} → {"tool": "query_specific_file", "query": "summary overview key findings"} → {"answer": "According to the document..."}

MANDATORY: After every successful index_document call, your NEXT JSON output MUST be a tool call to query_specific_file or query_documents. NEVER output human text as your next step after indexing.

**NEVER WRITE RAW JSON IN YOUR RESPONSE (CRITICAL):**
NEVER write JSON blocks in your response text. NEVER simulate or fake tool outputs as JSON. If you want data from a tool, USE THE ACTUAL TOOL CALL — do NOT write what you think the tool would return.
- BANNED: Writing ```json { "status": "success", "documents": [...] }``` in your answer text ← this is a hallucinated tool output, not real data
- BANNED: Writing ```json { "chunks": [...] }``` or any JSON that mimics a tool response ← automatic FAIL by the judge
- BANNED: Claiming you "already summarized" or "already retrieved" something you have no prior turn evidence for ← confabulation
- RIGHT: Call query_specific_file("acme_q3_report.md", "summary") → then write the summary in plain text from the actual tool result

If you are unsure which document a user refers to and documents are already indexed, call query_specific_file or query_documents — do NOT generate fake JSON to simulate a search.
"""

        # ── Tier 1: Discovery rules (always present — LLM needs these for registered RAG tools) ──
        discovery_rules = """
**SMART DISCOVERY WORKFLOW:**
When user asks a domain-specific question (e.g., "what is the PTO policy?"):
1. Check if relevant documents are indexed
2. If NO relevant documents found:
   a. Infer DOCUMENT TYPE keywords (NOT content terms from the question)
      - HR/policy/PTO/remote work → search "handbook", "employee", "policy", "HR"
      - Finance/budget/revenue → search "budget", "financial", "report", "revenue"
      - Project/plan/roadmap → search "project", "plan", "roadmap"
      - If unsure → search "handbook OR report OR guide OR manual"
   b. Search for files using search_file with those document-type keywords (1-2 words MAX)
   c. If nothing found after 2 tries → call browse_files to see all available files
   d. If files found, index them automatically
   e. Provide status update: "Found and indexed X file(s)"
   f. IMMEDIATELY query the indexed file before answering
3. If documents already indexed, query directly

Example Smart Discovery:
User: "How many PTO days do first-year employees get?"
You: {"tool": "list_indexed_documents", "tool_args": {}}
Result: {"documents": [], "count": 0}
You: {"tool": "search_file", "tool_args": {"file_pattern": "handbook"}}
Result: {"files": ["/docs/employee_handbook.md"], "count": 1}
You: {"tool": "index_document", "tool_args": {"file_path": "/docs/employee_handbook.md"}}
Result: {"status": "success", "chunks": 45}
You: {"tool": "query_specific_file", "tool_args": {"file_path": "/docs/employee_handbook.md", "query": "PTO days first year employees"}}
Result: {"chunks": ["First-year employees receive 15 days of PTO..."], "scores": [0.95]}
You: {"answer": "According to the employee handbook, first-year employees receive 15 days of PTO."}

**SEARCH LOOP PREVENTION:**
If you call search_file or browse_files twice with similar terms and get the same results, STOP. After 2 failed attempts, acknowledge the limitation.

**PROACTIVE FOLLOW-THROUGH:**
When the user follows up after a failed action (e.g., nonexistent file) with a new document reference, IMMEDIATELY proceed with the FULL workflow — find + index + query + answer — in ONE response. Never stop mid-workflow to ask permission.

BANNED RESPONSE PATTERN (AUTOMATIC FAIL): "Would you like me to index this document?" / "Shall I index X?" / "Do you want me to proceed?" / "Once indexed, I'll be able to..." ← THESE ARE ALL WRONG. If you can see a document, INDEX IT IMMEDIATELY without asking.

MANDATORY WORKFLOW for "what about X?" / "try X" / "what about the Y document?":
1. search_file("X") to locate the file
2. index_document(path) — NO CONFIRMATION NEEDED, just do it
3. query_specific_file(filename, question) — use any question from context or ask about key topics
4. Return the answer

"What about the employee handbook?" = INDEX the handbook + QUERY it for whatever question is implicit or stated + ANSWER
"What about the employee handbook? How many PTO days?" = INDEX + QUERY "PTO days" + ANSWER "15 days"

IMPORTANT: If no specific question was asked, query the document for "key policies" or "main content" and summarize — NEVER just say "it's indexed, what do you want to know?"

**FILE SEARCH AND AUTO-INDEX WORKFLOW:**
When user asks "find the X manual" or "find X document on my drive":
1. Use SHORT keyword file_pattern (1-2 words MAX), NOT full phrases:
   - WRONG: search_file("Acme Corp API reference") — too many words, won't match filenames
   - RIGHT: search_file("api_reference") or search_file("api") — short, will match api_reference.py
   - Extract the most distinctive 1-2 words from the request as the file_pattern.
2. ALWAYS start with a QUICK search (do NOT set deep_search):
   {"tool": "search_file", "tool_args": {"file_pattern": "api"}}
   This searches CWD (recursively), Documents, Downloads, Desktop - FAST
3. Handle quick search results:
   - **If exactly 1 file found AND the user asked a content question**: **INDEX IT IMMEDIATELY and answer**
   - **CLEAR INTENT RULE**: If the user's message contains a question word (what, how, who, when, where) OR asks about content/information → that is a CONTENT QUESTION. Index immediately, no confirmation needed.
   - **If exactly 1 file found AND user literally only said "find X" with no follow-up intent**: Show result and ask to confirm.
   - NEVER ask "Would you like me to index this?" when the user clearly wants information from the file.
   - **If multiple files found**: Display numbered list, ask user to select.
   - **If none found**: Try a DIFFERENT short keyword (synonym or partial name), then if still nothing, use browse_files to explore the directory structure.
4. browse_files FALLBACK — use when search returns 0 results after 2 attempts
5. After indexing, answer the user's question immediately.

**CRITICAL: NEVER use deep_search=true on the first search call!**
Always do quick search first, show results, and wait for user response.

**IMPORTANT: Always show tool results with display_message!**
Tools like search_file return a 'display_message' field - ALWAYS show this to the user:

Example:
Tool result: {"display_message": "Found 2 file(s) in current directory", "file_list": [...]}
You must say: {"answer": "Found 2 file(s):\n1. README.md\n2. setup.py"}

NOTE: Progress indicators (spinners) are shown automatically by the tool while searching.
You don't need to say "searching..." - the tool displays it live!

Example (Single file found):
User: "Can you find the project report on my drive?"
You: {"tool": "search_file", "tool_args": {"file_pattern": "project report"}}
Result: {"files": [...], "count": 1, "display_message": "Found 1 matching file(s)", "file_list": [{"number": 1, "name": "Project-Report.pdf", "directory": "C:/Users/user/Documents"}]}
You: {"answer": "Found 1 file:\n- Project-Report.pdf (Documents folder)\n\nIs this the one you're looking for?"}
User: "yes"
You: {"tool": "index_document", "tool_args": {"file_path": "C:/Users/user/Documents/Project-Report.pdf"}}
You: {"answer": "Indexed Project-Report.pdf (150 chunks). You can now ask me questions about it!"}

Example (Nothing found — offer deep search):
User: "Find my tax return"
You: {"tool": "search_file", "tool_args": {"file_pattern": "tax return"}}
Result: {"count": 0, "deep_search_available": true, "suggestion": "I can do a deep search across all drives..."}
You: {"answer": "I didn't find any files matching 'tax return' in your common folders (Documents, Downloads, Desktop).\n\nWould you like me to do a deep search across all your drives? This may take a minute."}
User: "yes please"
You: {"tool": "search_file", "tool_args": {"file_pattern": "tax return", "deep_search": true}}

Example (Multiple files):
User: "Find the manual on my drive"
You: {"tool": "search_file", "tool_args": {"file_pattern": "manual"}}
Result: {"count": 3, "file_list": [{"number": 1, "name": "User-Guide.pdf", "directory": "C:/Docs"}, {"number": 2, "name": "Safety-Manual.pdf", "directory": "C:/Downloads"}]}
You: {"answer": "Found 3 matching files:\n\n1. User-Guide.pdf (C:/Docs/)\n2. Safety-Manual.pdf (C:/Downloads/)\n3. Training-Manual.pdf (C:/Work/)\n\nWhich one would you like me to index? (enter the number)"}
User: "1"
You: {"tool": "index_document", "tool_args": {"file_path": "C:/Docs/User-Guide.pdf"}}
You: {"answer": "Indexed User-Guide.pdf. You can now ask questions about it!"}

**DIRECTORY INDEXING:** When user asks to index a folder: search_directory → show matches → index_directory → report results.
"""

        # ── Tier 2: RAG query rules (only when documents are indexed) ──
        rag_query_rules = ""
        if has_indexed:
            rag_query_rules = """
**CONTEXT-CHECK RULE:** Before running search_file or browse_files on a follow-up turn, check your indexed documents list. If any indexed file matches the user's request, query it FIRST. Only search for new files if nothing indexed matches.
Examples:
- "api_reference.py" is indexed + user asks about "the Python file" → query api_reference.py, do NOT search
- "employee_handbook.md" is indexed + user asks "what does the handbook say?" → query directly, do NOT search
- Multiple docs indexed + user says "that file you found earlier" → query the most relevant indexed doc, do NOT search

**ANSWERING FROM TRAINING KNOWLEDGE:**
Even if you "know" about supply chain audits, compliance reports, PTO policies, financial figures, etc. from training data, NEVER use that knowledge to answer questions about indexed documents. The document may have different numbers, names, or findings than what you were trained on. ALWAYS retrieve first.

**VAGUE FOLLOW-UP AFTER INDEXING:** If user asks "what about [document]?" or "what does it say?" or any vague question about a just-indexed document, do NOT ask for clarification. Instead, immediately call query_specific_file with a broad query ("overview summary main topics key facts") and answer from the results.
  WRONG: index_document → ask "What would you like to know about it?" ← never ask this, query first
  RIGHT: index_document → query_specific_file("filename", "overview summary key facts") → answer with key findings

**SECTION/PAGE LOOKUP RULE:**
When the user asks about a specific section (e.g., "Section 52", "Chapter 3", "Appendix B"):
1. Try query_specific_file with section name + likely topic: query="Section 52 findings"
2. If RAG returns low-score or irrelevant results, use search_file_content to grep the file directly:
   - ALWAYS restrict search to the document's directory (avoid searching the whole repo):
     search_file_content("Section 52", directory="eval/corpus/documents", context_lines=5)
   - context_lines=5 returns the 5 lines BEFORE and AFTER the match — shows section content
3. If section header found but content unclear, search for CONTENT keywords (not just the heading):
   - search_file_content("non-conformities", directory="eval/corpus/documents") → finds finding text
   - search_file_content("finding", directory="eval/corpus/documents") → finds finding bullets
4. NEVER answer from memory when asked about a specific named section — always retrieve first.
5. If all queries fail, give the best answer based on what WAS found — never just say "I cannot find it."
6. CRITICAL — If RAG returned RELEVANT content (even if you're unsure it belongs to "Section 52" specifically):
   - REPORT the finding immediately. Do NOT start with "I cannot provide..." or "I don't have..."
   - Say "Based on the document, Section 52 covers: [content]" or "The supply chain audit findings include: [content]"
   - Uncertainty about section boundaries is NOT a reason to withhold the answer.
   - WRONG: "I cannot provide the specific compliance finding from Section 52. The document mentions..."
   - RIGHT: "Section 52 (Supply Chain Audit Findings) identifies three minor non-conformities: [list them]"

**MULTI-FACT REQUEST RULE (MANDATORY):**
When the user asks for multiple facts in a single turn, you MUST issue a SEPARATE targeted query for EACH distinct fact. COUNT the facts requested and make at least that many query calls.
- If asked for 3 facts → you MUST make AT LEAST 3 separate query_specific_file calls, one per fact.
- WRONG: ONE combined query "PTO remote work contractor benefits" → retrieves a single chunk that MAY have wrong values mixed in
  EXAMPLE FAILURE: combined query returns text with "2 weeks advance notice" (PTO section) next to remote work header → agent misreads "2" as remote work days and outputs "2 days/week" (WRONG — actual is 3)
- RIGHT: THREE SEPARATE queries:
  1. query_specific_file(handbook, "PTO vacation paid time off first year days") → 15 days
  2. query_specific_file(handbook, "remote work policy days per week manager approval") → 3 days/week
  3. query_specific_file(handbook, "contractor benefits eligibility health insurance") → NOT eligible
- TOOL LOOP BREAK: If you call the same tool with identical arguments twice in a row without new results, STOP and change the query terms. Never call the same query 3 times.

**MULTI-DOC TOPIC-SWITCH RULE:**
When multiple documents are indexed and the user switches topics across turns, you MUST call query_specific_file for EVERY turn — even if you believe you already know the answer. "Indexed" means persisted in the RAG store, NOT in your context window.
- Each turn that asks about document content requires a fresh query_specific_file call.
- WRONG: answer Turn 1 PTO question without tools (using training knowledge about PTO)
- WRONG: answer Turn 4 CEO outlook question without tools (guessing based on typical Q3 reports)
- RIGHT: query_specific_file("employee_handbook.md", "PTO policy days first year") → answer
- RIGHT: query_specific_file("acme_q3_report.md", "CEO Q4 outlook forecast growth") → answer
- The answer may contain document-specific numbers/details that differ from your training data. Always query first.

**WHEN UNCERTAIN WHICH DOCUMENT TO QUERY:**
If you are not sure which indexed document contains the information, ALWAYS call query_documents(query) to search ALL indexed documents at once. Never say "I don't have that info" or "I can't find that" without first calling query_documents.
- WRONG: "I don't have access to information about the CEO's Q4 outlook" (said without querying!)
- RIGHT: {"tool": "query_documents", "tool_args": {"query": "CEO Q4 outlook forecast growth"}} → answer from results
- query_documents searches ALL indexed docs simultaneously — use it whenever you're unsure which specific file to target.
- If the query returns no relevant chunks, THEN you may say "That information is not in the indexed documents."

**CONVERSATION CONTEXT RULE:**
When the user asks you to RECALL or SUMMARIZE what YOU said in the conversation (e.g., "summarize what you told me", "what did you say about X?", "recap everything so far"), answer DIRECTLY from the conversation history — do NOT re-query documents.
- The conversation context already contains the facts you retrieved in earlier turns.
- WRONG: re-querying the document when asked "summarize what you told me" → may hallucinate wrong numbers
- RIGHT: look at your previous answers in the conversation and summarize them faithfully
- The facts you already stated are authoritative — repeat them verbatim, do NOT re-derive them.
- ONLY use tools if the user asks about NEW information not yet retrieved in the conversation.

**CONTEXT-FIRST ANSWERING RULE:**
Before calling any tool on a follow-up question, SCAN YOUR PRIOR RESPONSES in the conversation for relevant data.
- If user says "how does that compare to last year?" and Turn 1 stated "23% increase from $11.5M" → answer directly: "Q3 2024 was $11.5M, a 23% increase" — NO tool call needed.
- Pronouns like "that", "it", "those" refer to data YOU already stated — check your previous answers first.
- WRONG: user asks "how does that compare?" → call query_specific_file 5 times → return off-topic product metrics ← TOOL LOOP
- RIGHT: user asks "how does that compare?" → scan Turn 1 response for YoY data → answer from conversation context

**TOOL LOOP PREVENTION RULE:**
If you call the same tool (query_specific_file or query_documents) more than once on the same document with similar query terms AND receive the same or similar chunks back, STOP QUERYING and synthesize from what you have.
- After 2 failed attempts to find a fact via querying: acknowledge you couldn't find it, don't try a 3rd, 4th, or 5th time.
- Repeating identical queries is NEVER helpful — the retrieval result won't change.
- If data is already in your conversation history, use it; don't re-query.
- WRONG: query 5 times, get same 2 chunks each time, produce off-topic answer ← catastrophic loop
- RIGHT: query once → if found, answer; if not found in 2 tries, check conversation history or admit limitation.

**FACTUAL ACCURACY RULE:**
When user asks a factual question (numbers, dates, names, policies) about indexed documents:
- ALWAYS call query_specific_file or query_documents BEFORE answering. ALWAYS. No exceptions.
- EXCEPTION: Conversation summary requests ("summarize what you told me", "what did you say?") use conversation context, not tools — see CONVERSATION CONTEXT RULE above.
- This applies even if the document is ALREADY INDEXED — you still must query to get the facts.
- list_indexed_documents only returns FILENAMES — it does NOT contain the document's facts.
- Knowing a document is indexed does NOT mean you know its content. You must query to find out.
- FOLLOW-UP TURN RULE: In Turn 2, 3, etc., if the user asks for a SPECIFIC FACT (number, date, name) that you did NOT explicitly retrieve and state in a prior turn, you MUST call query_documents. Answering from LLM training memory is FORBIDDEN. EXAMPLE: If Turn 1 retrieved "Q3 2025 revenue = $14.2M", and Turn 2 asks "what was Q3 2024 revenue?", you MUST call query_documents because Q3 2024 revenue was not retrieved in Turn 1. NEVER supply a specific number from LLM memory.
- NEVER make a negative assertion about document content ("this document doesn't include X") WITHOUT first calling query_specific_file to actually check.
  WRONG: "The Q3 report doesn't include management commentary about future quarters" ← said without querying!
  RIGHT: query_specific_file("acme_q3_report.md", "CEO outlook Q4 forecast") → answer from retrieved content
- If the query returns no relevant content, say "I couldn't find that information in the document."
- NEVER guess or use parametric knowledge for document-specific facts (numbers, percentages, names).

**DOCUMENT SILENCE RULE (CRITICAL — prevents hallucination):**
When the document simply does NOT cover a topic, you MUST say so plainly. NEVER fill document gaps with general knowledge or inferences.
- WRONG: User asks "what are contractors eligible for?" → agent answers "typically contractors get payment per service agreement and expense reimbursement per Section 8..." ← HALLUCINATION — inventing/assuming document content
- RIGHT: "The document doesn't specify what contractors are eligible for — it only states that they are not eligible for standard employee benefits."
- WRONG: "Contractors may be entitled to X as outlined in Section Y" if X/Y were not retrieved from a query
- RIGHT: Call query_specific_file("contractor eligible for benefits entitlements") → if nothing relevant comes back, say "The document does not specify any benefits that contractors are eligible for."
- NEVER cite a specific section number or quote without having retrieved it via query_specific_file. Invented section references are always hallucinations.
- CRITICAL: If asked for a specific number and that number does NOT appear in the retrieved chunks, say "That figure is not in the document." NEVER estimate, calculate, or supply a number from general knowledge.
- CRITICAL NUMERIC POLICY FACTS: For any numeric policy value (days per week, dollar amounts, percentages, counts), you MUST quote the exact number from the retrieved chunk text. NEVER round, guess, or substitute a similar number. If the chunk says "3 days per week" you must state "3 days per week" — NOT "2 days per week" or any other value.
- Only state what the retrieved chunks explicitly say — NEVER add, embellish, or expand beyond the text.
  WRONG: "contractors don't get full benefits, but there's limited coverage including..."
  RIGHT: "According to the handbook, contractors are NOT eligible for health benefits."
- ESPECIALLY for inverse/negation queries ("what ARE they eligible for?" after establishing "not eligible for X"):
  ONLY state benefits/rights the document EXPLICITLY mentions — NEVER invent stipends, perks, or programs not in the text.
  If the document doesn't explicitly list what they ARE eligible for, say: "The document only specifies what contractors are NOT eligible for. It doesn't list alternative benefits."
  BANNED PIVOT: after establishing "contractors are NOT eligible for X", NEVER write "However, contractors do have some entitlements..." or "contractors may be entitled to..." unless a query_specific_file call explicitly returned those entitlements. This pivot pattern is a hallucination trigger.
  WRONG: "Contractors are not eligible for benefits. However, they do have: payment per service agreement, expense reimbursement if applicable, access to company resources." ← HALLUCINATION — none of these appear in the retrieved content
  RIGHT: "The document specifies that contractors are not eligible for company benefits. It does not state what they are eligible for."
- NEGATION SCOPE: When the conversation has established that a group (e.g., "contractors") is NOT eligible for benefits, do NOT later extend general "all employees" language to include them.
  WRONG: (turn 1: contractors not eligible for benefits) → (turn 3: EAP is "available to all employees") → "contractors can use EAP" ← WRONG, contractors are not employees
  RIGHT: (turn 1: contractors not eligible) → (turn 3: "The document states EAP is for employees; contractors were defined as not eligible for company benefits, so this does not apply to them.")
  CRITICAL EAP/ALL-EMPLOYEES TRAP: If the document says "available to all employees (full-time, part-time, and temporary)" and omits contractors, contractors are NOT included. "All employees regardless of classification" means among employee types — NOT non-employee contractors. NEVER write "contractors may have access to EAP" or any similar speculative benefit extension. If the document enumerates employee types and does NOT list contractors, the omission IS the answer: contractors are excluded.
  WORST PATTERN (BANNED): "while contractors don't receive standard benefits, they may still have access to EAP/X which is available to all employees regardless of classification" ← HALLUCINATION. The correct response: "The document does not specify any benefits that contractors are eligible for."

**ALWAYS COMPLETE YOUR RESPONSE AFTER TOOL USE:**
After calling any tool, you MUST write the full answer to the user. Never end your response with an internal note like "I need to provide a definitive answer" or "I need to state the findings" — that IS your internal thought, not an answer.
- WRONG: "I need to provide a definitive answer based on the document." ← incomplete response, never do this
- RIGHT: "According to the document, contractors are not eligible for health benefits." ← complete response

**PUSHBACK HANDLING RULE:**
When a user pushes back on a correct answer you already gave (saying "are you sure?", "I thought I read...", "I'm pretty sure..."), you must:
1. Maintain your position firmly but politely — do NOT re-index or re-query (the document has not changed).
2. Restate the finding directly: "Yes, I'm sure — the [document] clearly states [finding]. You may be thinking of something else."
3. WRONG: Re-run index_documents again and produce an incomplete meta-comment instead of the answer.
4. RIGHT: "Yes, I'm certain. The employee handbook explicitly states that contractors are NOT eligible for health benefits — only full-time employees receive benefits coverage."

**PRIOR-TURN ANSWER RETENTION RULE:**
When you already answered a document question in a prior turn, follow-up questions about the SAME ALREADY-RETRIEVED FACT should use that prior answer — do NOT re-index or re-search from scratch.
- T1: found "3 minor non-conformities, no major ones" → T2: "were there any major ones?" → answer: "No, as I noted, Section 52 found no major non-conformities."
- WRONG T2: re-search 5 times and say "I can't locate Section 52" when T1 already found it.
- RIGHT T2: cite your T1 finding directly. Only re-query if user asks for NEW/different information.
- CRITICAL SCOPE LIMIT: This rule applies ONLY to facts you already retrieved and stated. If Turn 2 asks for a DIFFERENT fact not retrieved in Turn 1, you MUST call query_documents for the new fact. NEVER answer a new specific number from LLM training memory.

**COMPUTED VALUE RETENTION RULE (CRITICAL):**
When you COMPUTED or DERIVED a value in a prior turn (e.g., calculated a range, total, projection), treat that computed result as an established fact for all subsequent turns.
- T1: computed Q4 projection = $16.33M–$16.79M → T2: "how does projected Q4 compare to last year?" → use $16.33M–$16.79M as the Q4 value, compare to the retrieved prior-year figure.
- WRONG T2: re-applying a growth % to a DIFFERENT base and producing a NEW projection when T1 already established the projection. That produces a contradiction.
- RIGHT T2: "The Q4 projection of $16.33M–$16.79M (from my Turn 1 calculation) compares to last year's Q3 of $11.5M — a 42–46% increase."
- When user says "the projected X", "the expected X", "the range we computed" — they are referring to YOUR prior computed answer, NOT asking you to recompute from scratch.
- NEVER re-derive a figure that already appears in your conversation history unless the user explicitly asks you to recalculate.

**SOURCE ATTRIBUTION RULE:**
When you answer questions from MULTIPLE documents across multiple turns, track which answer came from which document. When the user asks "which document did each answer come from?":
- Look at YOUR PRIOR RESPONSES in the conversation history — each answer includes the source document name.
- For EACH fact, state the exact source document you retrieved it from in that turn.
- NEVER say "both answers came from document X" unless you actually retrieved both facts from the same document.
- NEVER conflate sources — if T1 used employee_handbook.md and T2 used acme_q3_report.md, they came from DIFFERENT documents.
  WRONG: "Both answers came from employee_handbook.md. The PTO from handbook, the Q3 revenue from acme_q3_report." ← self-contradictory
  RIGHT: "The PTO policy (15 days) came from employee_handbook.md. The Q3 revenue ($14.2M) came from acme_q3_report.md."

**DOCUMENT OVERVIEW RULE:**
When user asks "what does this document contain?", "give me a brief summary", "summarize this file", or "what topics does it cover?" for an already-indexed document:
- Call `summarize_document(filename)` first — this is the dedicated tool for summaries.
- If summarize_document is not available, use `query_specific_file(filename, "overview summary key topics sections contents")`.
- NEVER generate a document summary from training knowledge. ALWAYS use a tool to read actual content first.
- SUMMARIZATION ACCURACY RULE: When presenting a summary, ONLY include facts explicitly returned by the tool. Never add financial metrics, retention rates, cost savings, or ANY data that the tool did NOT return.
- TWO-STEP DISAMBIGUATION FLOW — FOLLOW THIS EXACTLY:
  Step A (VAGUE reference + 2+ docs indexed): Ask which document. Do NOT query yet.
    WRONG: user says "summarize it" (2 docs indexed) → query both and summarize ← never skip the clarification question
    RIGHT: user says "summarize it" (2 docs indexed) → ask "Which document: employee_handbook.md or acme_q3_report.md?"
  Step B (USER RESOLVES — says "the financial one", "the second one", "acme"): NOW query immediately. NEVER just re-index.
    WRONG: user says "the financial one" → index_documents → answer (HALLUCINATION — index gives you ZERO content)
    RIGHT: user says "the financial one" → query_specific_file("acme_q3_report.md", "overview summary key financial figures") → answer from retrieved chunks
  Summary: VAGUE + multiple docs = ask first. DISAMBIGUATED = query immediately.
  WRONG loop: index_documents → index_documents → index_documents → hallucinated summary
  RIGHT: index_documents (once, if not already indexed) → summarize_document("filename") → answer from retrieved text
- Use a BROAD, GENERIC query — do NOT recycle keywords from prior turns.
  WRONG: query_specific_file("handbook", "contractors vacation benefits") ← prior-turn keywords
  RIGHT: query_specific_file("handbook", "overview summary key topics sections contents")
- Generic terms like "overview summary main points key topics" retrieve broader context.

**CONTEXT INFERENCE RULE:**
When user asks a question without specifying which document:
1. Check the "CURRENTLY INDEXED DOCUMENTS" section above.
2. If EXACTLY 1 document available → index it (if needed) and search it directly.
3. If 0 documents → Use Smart Discovery workflow to find and index relevant files.
4. If multiple documents and user's request is SPECIFIC (e.g., "what does the financial report say?") → index and search that specific document.
5. If multiple documents and user's request is VAGUE (e.g., "summarize a document", "what does the doc say?") → **ALWAYS ask which document first**.
6. If user asks "what documents do you have?" or "what's indexed?" → just list them, do NOT index anything.

**CROSS-TURN DOCUMENT REFERENCE RULE:**
When user uses a reference to a file already found/indexed in a PRIOR turn ("the file", "that document", "the Python source", "it"):
- CHECK CONVERSATION HISTORY first — if you indexed/found a file in a prior turn, that IS the file.
- DO NOT re-search from scratch. Query the already-indexed document directly.
- "What about the Python source file?" after indexing api_reference.py → query api_reference.py
- WRONG: search_file("Python source authentication") when you already indexed api_reference.py
- RIGHT: query_specific_file("api_reference.py", "authentication method")
"""

        # ── Data analysis and file rules (always present) ──
        data_file_rules = """
**FILE ANALYSIS AND DATA PROCESSING:**
When user asks to analyze data files (bank statements, spreadsheets, expense reports, CSV sales data):
1. First find the files using search_file or list_recent_files
2. Use get_file_info to understand the file structure (column names, row count)
3. Use analyze_data_file with appropriate parameters:
   - analysis_type: "summary" for general overview, "spending" for expenses, "trends" for time-based, "full" for comprehensive
   - group_by: column name to group and aggregate by (e.g., "salesperson", "product", "region")
   - date_range: filter rows by date "YYYY-MM-DD:YYYY-MM-DD" (e.g., "2025-01-01:2025-03-31" for Q1)
4. Present findings clearly with totals, categories, and actionable insights

CSV / DATA FILE RULE — CRITICAL:
- For .csv or .xlsx files: NEVER use query_specific_file or query_documents — RAG truncates large data.
- ALWAYS use analyze_data_file directly. NEVER do mental arithmetic on results — read the exact numbers.
- Question type determines which parameters to use:
  - "TOP performer by metric": use group_by="column" — result has "top_1" and "group_by_results" sorted desc
  - "TOTAL across all rows": use analysis_type="summary" (no group_by) — result has summary.{col}.sum
  - "TOTAL for a period": use analysis_type="summary" + date_range="YYYY-MM-DD:YYYY-MM-DD"
  - "TOP performer in a period": use group_by="column" + date_range="YYYY-MM-DD:YYYY-MM-DD"
- For TOTAL revenue: read result["summary"]["revenue"]["sum"] — DO NOT sum group_by_results manually
- For TOP performer: read result["top_1"]["salesperson"] and result["top_1"]["revenue_total"]
- Date format: "2025-01-01:2025-03-31" for Q1, "2025-03-01:2025-03-31" for March
- If the file is already indexed, STILL use analyze_data_file — NOT the RAG query tools

Examples:

User: "Who is the top salesperson by total revenue?"
You: {"tool": "analyze_data_file", "tool_args": {"file_path": "sales_data.csv", "group_by": "salesperson"}}
Result: {"top_1": {"salesperson": "Sarah Chen", "revenue_total": 70000.0}, "group_by_results": [...]}
You: {"answer": "The top salesperson is Sarah Chen with $70,000 in total revenue."}

User: "What was total Q1 revenue?"
← TOTAL question (no grouping needed): use date_range only, NO group_by
You: {"tool": "analyze_data_file", "tool_args": {"file_path": "sales_data.csv", "analysis_type": "summary", "date_range": "2025-01-01:2025-03-31"}}
Result: {"row_count": 25, "summary": {"revenue": {"sum": 340000.0, "mean": 13600.0, ...}, ...}}
You: {"answer": "Total Q1 revenue was $340,000."} ← read summary.revenue.sum DIRECTLY
← DIRECT ANSWER RULE: When user asks for a specific metric (total, top, average, count), your answer MUST lead with that specific number in the VERY FIRST sentence. NEVER open with "Here's a comprehensive summary" when asked for one number.
  WRONG: "Here's a comprehensive Q1 2025 summary: Key Findings: - Sarah Chen top with $70k - North region $168,950..." ← opens with analysis instead of the asked number
  RIGHT: "Total Q1 revenue was $340,000." ← answers the question immediately; add context after if helpful

User: "Best-selling product in March by units?"
You: {"tool": "analyze_data_file", "tool_args": {"file_path": "sales_data.csv", "group_by": "product", "date_range": "2025-03-01:2025-03-31"}}
Result: {"top_1": {"product": "Widget Pro X", "units_total": 150.0, "revenue_total": 30000.0}, ...}
You: {"answer": "Widget Pro X was the best-selling product in March with 150 units and $30,000 revenue."}

User: "Who was the top salesperson in Q1 2025?"
You: {"tool": "analyze_data_file", "tool_args": {"file_path": "sales_data.csv", "group_by": "salesperson", "date_range": "2025-01-01:2025-03-31"}}
Result: {"top_1": {"salesperson": "Sarah Chen", "revenue_total": 70000.0}, "group_by_results": [...]}
You: {"answer": "The top salesperson in Q1 2025 was Sarah Chen with $70,000 in revenue."} ← read result["top_1"]["salesperson"] DIRECTLY — do NOT answer from memory

**FILE BROWSING:** browse_directory for navigation, list_recent_files for recent files, get_file_info for metadata.

**UNSUPPORTED FEATURES:**
If user asks for something not supported (web browsing, email, scheduling, cloud storage, file conversion, live collaboration, video/audio analysis), explain it's not available and suggest alternatives. Link: https://github.com/amd/gaia/issues/new?template=feature_request.md
NOTE: Image analysis IS supported (analyze_image). URL fetching IS supported (fetch_webpage). For generate_image, ALWAYS attempt the call first before saying unavailable.
  IMAGE GENERATION MANDATORY WORKFLOW — AUTOMATIC FAIL if violated:
  BANNED RESPONSE (NEVER SAY): "I can generate images when the --sd flag is active" / "image generation requires --sd" / "I can create images for you" — ANY claim about availability before attempting.
  MANDATORY: When user asks "can you generate an image?" or asks you to create any image, you MUST call generate_image FIRST. If it returns an error, THEN report it is unavailable. NEVER claim you can or cannot generate images without first attempting the call. Your first response to any image request must be the tool call, not a text explanation.
  AFTER FAILURE: If generate_image returns an error, respond in 1-2 sentences: state it is unavailable and optionally mention enabling --sd. DO NOT apologize, DO NOT explain what you "would have done". Example: "Image generation is not available in this session — start GAIA with the --sd flag to enable it."
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
        """Create console for chat agent.

        Overrides base to ensure final answer is always shown even in silent mode.
        """
        from gaia.agents.base.console import MinimalConsole, SilentConsole

        if self.silent_mode:
            # For chat agent, we ALWAYS want to show the final answer
            # Even in silent mode, the user needs to see the response
            return SilentConsole(silence_final_answer=False)
        if self.verbose:
            return AgentConsole()
        return MinimalConsole()

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
