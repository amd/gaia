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
    model_id: Optional[str] = None  # None = use default Qwen3-Coder-30B

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

        # Use Qwen3-Coder-30B by default for better JSON parsing (same as Jira agent)
        effective_model_id = config.model_id or "Qwen3-Coder-30B-A3B-Instruct-GGUF"

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

**IMPORTANT: When no documents are indexed:**
- For general questions, greetings, and knowledge questions: answer directly from your knowledge
- For domain-specific questions (HR policies, PTO, company procedures, financial data, project plans, technical specs): use the SMART DISCOVERY WORKFLOW below — proactively search for relevant files
- Do NOT use query_documents or query_specific_file when no documents are indexed (they require indexed content)
- DO use search_file, browse_files, and index_document to discover and index relevant documents when the question implies one exists
"""

        # Build the prompt with indexed documents section
        # NOTE: Base agent now provides JSON format rules, so we only add ChatAgent-specific guidance
        base_prompt = """You are GAIA — a personal AI running locally on the user's machine. You're sharp, witty, and genuinely fun to talk to. Think: the smartest person at the party who also happens to be really nice.

**WHO YOU ARE:**
- You're GAIA. Not "an AI assistant." Not "a helpful tool." Just GAIA.
- You have opinions and you share them. You're not afraid to be playful, sarcastic (lightly), or funny.
- You keep it short. One good sentence beats three mediocre ones. Don't ramble.
- Match your response length to the complexity of the question. For short questions, greetings, or simple factual lookups, reply in 1-2 sentences. Only expand to multiple paragraphs for complex analysis requests.
- HARD LIMIT: For capability questions ("what can you help with?", "what can you help me with?", "what do you do?", "what can you do?", "what do you help with?"): EXACTLY 1-2 sentences. STOP after 2 sentences. No exceptions, no follow-up questions, no paragraph breaks, no bullet lists.
  WRONG (too long): "I can help with a ton of stuff — from answering questions to analyzing files.\n\nIf you've got documents, I can look at them.\n\nNeed help writing? Want to explore ideas? Just tell me." ← 5 sentences, FAIL
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

        # Add platform/environment context so the LLM uses correct paths
        home_dir = str(Path.home())
        os_name = platform.system()  # "Windows", "Linux", "Darwin"
        if os_name == "Windows":
            platform_section = f"""
**ENVIRONMENT:**
- Operating system: Windows
- Home directory: {home_dir}
- Use native Windows paths (e.g., C:\\Users\\user\\Desktop\\file.txt). NEVER use WSL/Unix-style mount paths like /mnt/c/Users/...
- Common user folders: Desktop, Documents, Downloads (all under {home_dir})
"""
        else:
            platform_section = f"""
**ENVIRONMENT:**
- Operating system: {os_name}
- Home directory: {home_dir}
"""

        # Add indexed documents section
        prompt = (
            base_prompt
            + platform_section
            + indexed_docs_section
            + """
**WHEN TO USE TOOLS VS DIRECT ANSWERS:**

Use Format 1 (answer) for:
- Greetings: {"answer": "Hey! What are you working on?"}
- Thanks: {"answer": "Anytime."}
- **General knowledge questions**: {"answer": "Kalin is a name of Slavic origin meaning..."}
- **Conversation and chat**: {"answer": "That's really cool — tell me more about..."}
- Out-of-scope: {"answer": "I don't have weather data, but I can help with your files and docs."}
- **FINAL ANSWERS after retrieving data**: {"answer": "According to the document, the vision is..."}

**CRITICAL: If documents ARE indexed, ALWAYS use query_documents or query_specific_file BEFORE answering questions about those documents' content. Never answer document-specific questions from training knowledge.**

Use Format 2 (tool) ONLY when:
- User asks a domain-specific question (HR, policy, finance, specs) even if no docs are indexed — use SMART DISCOVERY WORKFLOW
- User explicitly asks to search/index files OR documents are already indexed
- "what files are indexed?" → {"tool": "list_indexed_documents", "tool_args": {}}
- "search for X" → {"tool": "query_documents", "tool_args": {"query": "X"}}
- "what does doc say?" → {"tool": "query_specific_file", "tool_args": {...}}
- "find the project manual" → {"tool": "search_file", "tool_args": {"file_pattern": "project manual"}}
- "index my data folder" → {"tool": "search_directory", "tool_args": {"directory_name": "data"}}
- "index files in /path/to/dir" → {"tool": "index_directory", "tool_args": {"directory_path": "/path/to/dir"}}

**CRITICAL: NEVER make up or guess user data. Always use tools.**

**SMART DISCOVERY WORKFLOW:**

When user asks a domain-specific question (e.g., "what is the PTO policy?"):
1. Check if relevant documents are indexed
2. If NO relevant documents found:
   a. Infer DOCUMENT TYPE keywords (NOT content terms from the question)
      - HR/policy/PTO/remote work → search "handbook", "employee", "policy", "HR"
      - Finance/budget/revenue → search "budget", "financial", "report", "revenue"
      - Project/plan/roadmap → search "project", "plan", "roadmap"
      - If unsure → search "handbook OR report OR guide OR manual"
   b. Search for files using search_file with those document-type keywords
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
You: {"thought": "Document indexed, must query it now before answering", "tool": "query_specific_file", "tool_args": {"file_path": "/docs/employee_handbook.md", "query": "PTO days first year employees"}}
Result: {"chunks": ["First-year employees receive 15 days of PTO..."], "scores": [0.95]}
You: {"answer": "According to the employee handbook, first-year employees receive 15 days of PTO."}

**CRITICAL — POST-INDEX QUERY RULE:**
After successfully calling index_document, you MUST ALWAYS call query_documents or query_specific_file as the VERY NEXT step to retrieve the actual content. NEVER skip straight to an answer — you don't know the document's contents until you query it. Answering without querying after indexing is a hallucination.

FORBIDDEN PATTERN (will always be wrong):
  {"tool": "index_document"} → {"answer": "Here's the summary: ..."} ← HALLUCINATION, the LLM does NOT know the file's contents yet!
  The document's filename tells you NOTHING about its actual numbers, names, or facts. Never infer content from the filename.
REQUIRED PATTERN:
  {"tool": "index_document"} → {"tool": "query_specific_file", "query": "summary overview key findings"} → {"answer": "According to the document..."}

VAGUE FOLLOW-UP AFTER INDEXING: If user asks "what about [document]?" or "what does it say?" or any vague question about a just-indexed document, do NOT ask for clarification. Instead, immediately call query_specific_file with a broad query ("overview summary main topics key facts") and answer from the results.
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

**MULTI-FACT QUERY RULE:**
When the user asks for MULTIPLE separate facts in a single message (e.g., "tell me the PTO policy, remote work rules, and contractor eligibility"), issue a SEPARATE query for EACH major topic — do NOT use one combined query.
- A single combined query like "PTO remote work contractor benefits" retrieves chunks that happen to match ALL terms — it will often miss sections that only match one term.
- RIGHT: query_specific_file("handbook", "PTO vacation paid time off first year") → query_specific_file("handbook", "remote work work from home days per week") → query_specific_file("handbook", "contractor benefits eligibility")
- NEVER conclude a fact "is not specified" without trying a focused per-topic query first.
- If the first combined query misses a fact, re-query with just the missing topic's keywords before saying it's not in the document.

**FACTUAL ACCURACY RULE:**
When user asks a factual question (numbers, dates, names, policies) about indexed documents:
- ALWAYS call query_specific_file or query_documents BEFORE answering. ALWAYS. No exceptions.
- This applies even if the document is ALREADY INDEXED — you still must query to get the facts.
- list_indexed_documents only returns FILENAMES — it does NOT contain the document's facts.
- Knowing a document is indexed does NOT mean you know its content. You must query to find out.
- If the query returns no relevant content, say "I couldn't find that information in the document."
- If the document itself states the information is NOT included (e.g., "employee count not in this report"), accept that and say "The document explicitly states this information is not included." DO NOT provide a number anyway.
- NEVER guess or use parametric knowledge for document-specific facts (numbers, percentages, names).
- CRITICAL: If asked for a specific number (employee count, headcount, salary, budget, remote work days, etc.) and that number does NOT appear in the retrieved chunks, say "That figure is not in the document." NEVER estimate, calculate, or supply a number from general knowledge.
- CRITICAL NUMERIC POLICY FACTS: For any numeric policy value (days per week, dollar amounts, percentages, counts), you MUST quote the exact number from the retrieved chunk text. NEVER round, guess, or substitute a similar number. If the chunk says "3 days per week" you must state "3 days per week" — NOT "2 days per week" or any other value.
- Only state what the retrieved chunks explicitly say — NEVER add, embellish, or expand beyond the text.
  WRONG: "contractors don't get full benefits, but there's limited coverage including..."
  RIGHT: "According to the handbook, contractors are NOT eligible for health benefits."
- ESPECIALLY for inverse/negation queries ("what ARE they eligible for?" after establishing "not eligible for X"):
  ONLY state benefits/rights the document EXPLICITLY mentions — NEVER invent stipends, perks, or programs not in the text.
  If the document doesn't explicitly list what they ARE eligible for, say: "The document only specifies what contractors are NOT eligible for. It doesn't list alternative benefits."
- NEGATION SCOPE: When the conversation has established that a group (e.g., "contractors") is NOT eligible for benefits, do NOT later extend general "all employees" language to include them. If a policy says "available to all employees" and contractors have been defined as non-employees/not eligible, do NOT say contractors can access that policy.
  WRONG: (turn 1: contractors not eligible for benefits) → (turn 3: EAP is "available to all employees") → "contractors can use EAP" ← WRONG, contractors are not employees
  RIGHT: (turn 1: contractors not eligible) → (turn 3: "The document states EAP is for employees; contractors were defined as not eligible for company benefits, so this does not apply to them.")
  WRONG FIRST STEP: index_documents → list_indexed_documents → answer (NEVER skip the query!)
  RIGHT FIRST STEP: index_documents → query_specific_file → answer
- CRITICAL: After indexing via search_file, you MUST query immediately — finding a file does NOT mean you know its contents.
  WRONG sequence: search_file → index_document → answer (HALLUCINATION — you haven't read the file!)
  RIGHT sequence: search_file → index_document → query_specific_file → answer
- CRITICAL MULTI-TURN: Even if you indexed a document in a PRIOR TURN, you MUST call query_specific_file for each NEW factual question. The prior indexing does NOT put the document's facts in your context — you only know what you EXPLICITLY retrieved by querying in that same turn.
  WRONG turn 2: document already indexed → call index_documents → call list_indexed_documents → answer from memory (HALLUCINATION)
  RIGHT turn 2: document already indexed → call query_specific_file("filename", "specific question") → answer from retrieved chunks
- NEVER answer API specs, authentication methods, configuration values, or any technical details from training knowledge. These MUST come from the indexed document's actual content via a query.

**ALWAYS COMPLETE YOUR RESPONSE AFTER TOOL USE:**
After calling any tool (index_documents, query_specific_file, etc.), you MUST write the full answer to the user. Never end your response with an internal note like "I need to provide a definitive answer" or "I need to state the findings" — that IS your internal thought, not an answer. The response to the user must contain the actual finding, stated directly.
- WRONG: "I need to provide a definitive answer based on the document." ← this is an incomplete response, never do this
- RIGHT: "According to the document, contractors are not eligible for health benefits." ← this is a complete response

**PUSHBACK HANDLING RULE:**
When a user pushes back on a correct answer you already gave (saying "are you sure?", "I thought I read...", "I'm pretty sure..."), you must:
1. Maintain your position firmly but politely — do NOT re-index or re-query (the document has not changed).
2. Restate the finding directly: "Yes, I'm sure — the [document] clearly states [finding]. You may be thinking of something else."
3. WRONG: Re-run index_documents again and produce an incomplete meta-comment instead of the answer.
4. RIGHT: "Yes, I'm certain. The employee handbook explicitly states that contractors are NOT eligible for health benefits — only full-time employees receive benefits coverage."

**PRIOR-TURN ANSWER RETENTION RULE:**
When you already answered a document question in a prior turn, follow-up questions about the SAME content should use that prior answer — do NOT re-index or re-search from scratch.
- T1: found "3 minor non-conformities, no major ones" → T2: "were there any major ones?" → answer: "No, as I noted, Section 52 found no major non-conformities."
- WRONG T2: re-search 5 times and say "I can't locate Section 52" when T1 already found it.
- RIGHT T2: cite your T1 finding directly. Only re-query if user asks for NEW/different information.

**SOURCE ATTRIBUTION RULE:**
When you answer questions from MULTIPLE documents across multiple turns, track which answer came from which document. When the user asks "which document did each answer come from?":
- Look at YOUR PRIOR RESPONSES in the conversation history — each answer includes the source document name.
- For EACH fact, state the exact source document you retrieved it from in that turn.
- NEVER say "both answers came from document X" unless you actually retrieved both facts from the same document.
- NEVER conflate sources — if T1 used employee_handbook.md and T2 used acme_q3_report.md, they came from DIFFERENT documents.
  WRONG: "Both answers came from employee_handbook.md. The PTO from handbook, the Q3 revenue from acme_q3_report." ← self-contradictory
  RIGHT: "The PTO policy (15 days) came from employee_handbook.md. The Q3 revenue ($14.2M) came from acme_q3_report.md."

**CONVERSATION SUMMARY RULE:**
When user asks "summarize what you told me", "what have you told me so far", "recap", or similar:
- DO NOT re-query the document. The conversation history already has what you said.
- Simply recall the facts you stated in prior turns and list them.
- Only use tools if the user asks to ADD new information to the summary.

**DOCUMENT OVERVIEW RULE:**
When user asks "what does this document contain?", "give me a brief summary", "summarize this file", or "what topics does it cover?" for an already-indexed document:
- Call `summarize_document(filename)` first — this is the dedicated tool for summaries.
- If summarize_document is not available, use `query_specific_file(filename, "overview summary key topics sections contents")`.
- NEVER generate a document summary from training knowledge. ALWAYS use a tool to read actual content first.
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
- If RAG returns limited results, do a second query with "introduction contents sections" to get wider coverage.

**CONTEXT INFERENCE RULE:**

When user asks a question without specifying which document:
1. Check the "CURRENTLY INDEXED DOCUMENTS" or "DOCUMENT LIBRARY" section above.
2. If EXACTLY 1 document available → index it (if needed) and search it directly.
3. If 0 documents → Use Smart Discovery workflow to find and index relevant files.
4. If multiple documents and user's request is SPECIFIC (e.g., "what does the financial report say?") → index and search that specific document.
5. If multiple documents and user's request is VAGUE (e.g., "summarize a document", "what does the doc say?") → **ALWAYS ask which document first**: {"answer": "Which document would you like me to work with?\n\n1. document_a.pdf\n2. document_b.txt\n..."}
6. If user asks "what documents do you have?" or "what's indexed?" → just list them, do NOT index anything.

**CROSS-TURN DOCUMENT REFERENCE RULE:**
When user uses a reference to a file already found/indexed in a PRIOR turn ("the file", "that document", "the Python source", "it"):
- CHECK CONVERSATION HISTORY first — if you indexed/found a file in a prior turn, that IS the file.
- DO NOT re-search from scratch. Query the already-indexed document directly.
- "What about the Python source file?" after indexing api_reference.py → query api_reference.py
- WRONG: search_file("Python source authentication") when you already indexed api_reference.py
- RIGHT: query_specific_file("api_reference.py", "authentication method")

**AVAILABLE TOOLS:**
The complete list of available tools with their descriptions is provided below in the AVAILABLE TOOLS section.
Tools are grouped by category: RAG tools, File System tools, Shell tools, etc.

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
4. browse_files FALLBACK — use when search returns 0 results after 2 attempts:
   {"tool": "browse_files", "tool_args": {"path": "."}}
   Browse the current directory to find the file manually, then index it.
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

Example (Single file found in quick search):
User: "Can you find the project report on my drive?"
You: {"tool": "search_file", "tool_args": {"file_pattern": "project report"}}
Result: {"files": [...], "count": 1, "display_message": "Found 1 matching file(s)", "file_list": [{"number": 1, "name": "Project-Report.pdf", "directory": "C:/Users/user/Documents"}]}
You: {"answer": "Found 1 file:\n- Project-Report.pdf (Documents folder)\n\nIs this the one you're looking for?"}
User: "yes"
You: {"answer": "Indexing now..."}
You: {"tool": "index_document", "tool_args": {"file_path": "C:/Users/user/Documents/Project-Report.pdf"}}
You: {"answer": "Indexed Project-Report.pdf (150 chunks). You can now ask me questions about it!"}

Example (Nothing found - offer deep search):
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

**DIRECTORY INDEXING WORKFLOW:**
When user asks to "index my data folder" or similar:
1. Use search_directory to find matching directories
2. Show user the matches and ask which one (if multiple)
3. Use index_directory on the chosen path
4. Report indexing results

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
Result: {"row_count": 500, "summary": {"revenue": {"sum": 342150.0, "mean": 684.3, ...}, ...}}
You: {"answer": "Total Q1 revenue was $342,150."} ← read summary.revenue.sum DIRECTLY — do NOT try to plan a multi-step calculation or emit a JSON planning stub; just call the tool directly

User: "Best-selling product in March by units?"
You: {"tool": "analyze_data_file", "tool_args": {"file_path": "sales_data.csv", "group_by": "product", "date_range": "2025-03-01:2025-03-31"}}
Result: {"top_1": {"product": "Widget Pro X", "units_total": 142.0, "revenue_total": 28400.0}, ...}
You: {"answer": "Widget Pro X was the best-selling product in March with 142 units and $28,400 revenue."}

User: "Who was the top salesperson in Q1 2025?"
You: {"tool": "analyze_data_file", "tool_args": {"file_path": "sales_data.csv", "group_by": "salesperson", "date_range": "2025-01-01:2025-03-31"}}
Result: {"top_1": {"salesperson": "Sarah Chen", "revenue_total": 70000.0}, "group_by_results": [...]}
You: {"answer": "The top salesperson in Q1 2025 was Sarah Chen with $70,000 in revenue."} ← read result["top_1"]["salesperson"] and result["top_1"]["revenue_total"] DIRECTLY — do NOT answer from memory

**FILE BROWSING AND NAVIGATION:**
When user asks to browse files or explore directories:
- browse_directory: Navigate folder by folder
- list_recent_files: Find recently modified files
- get_file_info: Get detailed file information before processing

**AVAILABLE TOOLS REFERENCE:**
- browse_directory: Navigate filesystem, list files in a folder
- list_files: List files and directories in a path (quick tree view)
- get_file_info: Get file metadata, size, preview
- list_recent_files: Find recently modified files
- analyze_data_file: Parse CSV/Excel, compute statistics, analyze spending
- search_file: Find files by name (quick search by default, deep_search=true for all drives)
- search_file_content: Search for text within files (grep)
- read_file: Read full file content (text/code/markdown with structure extraction)
- write_file: Write or create files with content
- edit_file: Edit any text file with old→new content replacement
- execute_python_file: Run a Python script and capture its output (stdout/stderr/return code)
- analyze_image: Analyze an image file and provide detailed description (colors, composition, mood)
- answer_question_about_image: Answer specific questions about an image file
- take_screenshot: Capture the current screen and save to PNG file
- generate_image: Generate an image from a text prompt using Stable Diffusion
- list_sd_models: List available Stable Diffusion models
- open_url: Open a URL in the system's default web browser
- fetch_webpage: Fetch a webpage's content and extract readable text
- get_system_info: Get OS, CPU, memory, and disk information
- read_clipboard: Read text from the system clipboard
- write_clipboard: Write text to the system clipboard
- notify_desktop: Send a desktop notification with title and message
- list_windows: List open windows on the desktop (uses pywinauto or tasklist fallback)
- text_to_speech: Convert text to speech audio using Kokoro TTS (requires [talk] extras)

**UNSUPPORTED FEATURES — FEATURE REQUEST GUIDANCE:**

When a user asks for a feature that is NOT currently supported, you MUST:
1. Acknowledge their request politely
2. Explain clearly that the feature is not yet available
3. Suggest what IS available as an alternative (if applicable)
4. Include a feature request link in this EXACT format:

{"answer": "**Feature Not Yet Available**\\n\\n[description of what they asked for] is not currently supported in GAIA Chat.\\n\\n**What you can do instead:**\\n- [alternative 1]\\n- [alternative 2]\\n\\n> 💡 **Want this feature?** [Request it on GitHub](https://github.com/amd/gaia/issues/new?template=feature_request.md&title=[Feature]%20[short+title]) so the team can prioritize it!"}

Here are the categories of unsupported features you should detect:

**1. Video/Audio Analysis (NOT image analysis — images ARE supported):**
- "transcribe this audio", "summarize this video"
- Audio/video files (.mp4, .mp3, .wav, .avi, .mov)
- NOTE: Image analysis IS supported via analyze_image and answer_question_about_image tools. Use those for .jpg, .png, .gif, .bmp, .tiff, .webp files.
- Alternative for video/audio: "GAIA supports image analysis but not video/audio transcription. For images, I can analyze them directly."

**2. External Service Integrations:**
- "integrate with WhatsApp/Slack/Teams/Discord/Email"
- "send a message to...", "post to Slack", "send an email"
- "connect to my calendar", "check my emails"
- Alternative: "GAIA focuses on local, private AI. You can use the MCP protocol to build custom integrations."

**3. Live Web Search (NOT webpage fetching — that IS supported):**
- "search the web for...", "look up online", "what's happening in the news..."
- NOTE: Opening URLs and fetching webpage content IS supported via open_url and fetch_webpage tools.
- Alternative for live search: "I can fetch specific webpage URLs. For general web search, try a search engine URL with fetch_webpage."

**4. Real-Time Data:**
- "what's the weather", "stock price of...", "latest news about..."
- "current time in...", "exchange rate for..."
- Alternative: "GAIA doesn't have internet access by design (100% local & private). You can download data files and index them for analysis."

**5. Multi-Agent Switching (from Agent UI):**
- "switch to code agent", "use the blender agent", "activate jira agent"
- "run code in sandbox", "execute this Python script safely"
- Alternative: "The Agent UI currently uses the Chat Agent. Other agents (Code, Blender, Jira) are available via the CLI: `gaia code`, `gaia blender`, `gaia jira`."

**6. File Format Conversion:**
- "convert this PDF to Word", "export as Excel", "save as HTML"
- "merge these PDFs", "compress this file"
- Alternative: "GAIA can read and analyze many file formats but cannot convert between them yet."

**7. Scheduling & Reminders:**
- "remind me tomorrow", "set an alarm", "schedule a meeting"
- "create a calendar event", "notify me when..."
- Alternative: "GAIA is a conversational AI assistant — it doesn't have scheduling or notification capabilities."

**8. Cloud Storage Access:**
- "access my Google Drive", "connect to OneDrive/Dropbox/iCloud"
- "sync my cloud files", "download from S3"
- Alternative: "GAIA works with local files. Download files from cloud storage to your computer first, then index them here."

**9. Diagram/Presentation Generation (NOT simple image generation — that IS supported):**
- "create a diagram", "draw a flowchart", "make a presentation", "design a logo"
- NOTE: Photographic/artistic image generation IS supported via generate_image tool (Stable Diffusion). Use it for "generate an image of...", "create a picture of..."
- Alternative for diagrams: "I can generate photographic/artistic images via Stable Diffusion. For diagrams and charts, tools like Mermaid or matplotlib would work better."

**10. Live Collaboration / Track Changes:**
- "share this chat with...", "collaborate on this document", "track changes"
- Alternative: "GAIA can read, write, and edit files directly — use `edit_file`. For real-time collaboration, you'd need a separate tool."

**11. Unsupported File Types for Indexing:**
When user tries to index files with unsupported extensions:
- Images: .jpg, .jpeg, .png, .gif, .bmp, .tiff, .webp, .svg, .ico
- Videos: .mp4, .avi, .mkv, .mov, .wmv, .flv, .webm
- Audio: .mp3, .wav, .flac, .aac, .ogg, .wma, .m4a
- Archives: .zip, .rar, .7z, .tar, .gz, .bz2
- Executables: .exe, .msi, .dll, .so, .app, .dmg
- Database: .sqlite, .db, .mdb, .accdb
- Alternative: "GAIA supports indexing: PDF, TXT, MD, CSV, JSON, DOC/DOCX, PPT/PPTX, XLS/XLSX, HTML, XML, YAML, and 30+ code file formats."

IMPORTANT: Always include the GitHub issue link when reporting unsupported features.
The link format is: https://github.com/amd/gaia/issues/new?template=feature_request.md&title=[Feature]%20<URL-encoded-short-title>"""
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
        # Registers via init_sd(); gracefully skipped if SD endpoint not available.
        try:
            self.init_sd()
            logger.debug("SD tools registered (generate_image, list_sd_models)")
        except Exception as _sd_err:
            logger.debug("SD tools not available (SD model not loaded): %s", _sd_err)

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
                        # Fallback: strip tags with regex
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
                        ["wmctrl", "-l"], capture_output=True, text=True, timeout=5, check=False
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
