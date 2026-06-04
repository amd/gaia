# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Chat Agent - Interactive chat with RAG and file search capabilities.
"""

import os
import platform
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

from gaia.agents.base.agent import Agent, default_max_steps
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.memory import EMBEDDING_MODEL, MemoryMixin
from gaia.agents.base.tool_loader import ToolLoader
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import FileToolsMixin
from gaia_agent_chat.session import SessionManager
from gaia_agent_chat.tool_bundles import DOC_BUNDLES, DOC_CORE_TOOLS
from gaia.agents.tools import FileSystemToolsMixin  # Enhanced file system navigation
from gaia.agents.tools import ScratchpadToolsMixin  # Structured data analysis
from gaia.agents.tools import (  # Web browsing and search; Shared tools
    BrowserToolsMixin,
    FileIOToolsMixin,
    FileSearchToolsMixin,
    RAGToolsMixin,
    ScreenshotToolsMixin,
    ShellToolsMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, is_tool_calling_model
from gaia.logger import get_logger
from gaia.mcp.mixin import MCPClientMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.sd.mixin import SDToolsMixin
from gaia.security import PathValidator
from gaia.utils.file_watcher import FileChangeHandler, check_watchdog_available
from gaia.vlm.mixin import VLMToolsMixin

logger = get_logger(__name__)


def dynamic_tools_env_override() -> Optional[bool]:
    """Parse the ``GAIA_DYNAMIC_TOOLS`` override, or ``None`` when it is unset.

    Returns the parsed boolean (truthy set ``1``/``true``/``yes``/``on``,
    case-insensitive) when the env var is set, else ``None`` to signal "no
    override — fall back to the persisted/config value". The UI settings
    router reuses this so the env-wins precedence and the truthy set never
    drift between the agent resolver and the toggle that surfaces it.
    """
    raw = os.getenv("GAIA_DYNAMIC_TOOLS")
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ChatAgentConfig:
    """Configuration for ChatAgent."""

    # LLM settings
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None  # None = use default model (Gemma)

    # Execution settings
    max_steps: int = field(default_factory=default_max_steps)
    streaming: bool = False  # Use --streaming to enable

    # NPU's FLM build runs at 4K, so a device config can override the 32K ctx.
    device: Optional[str] = None
    min_context_size: Optional[int] = None

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

    # File System settings
    enable_filesystem: bool = (
        False  # Enhanced file system tools (disabled until agent split)
    )
    enable_scratchpad: bool = (
        False  # Data scratchpad for analysis (disabled until agent split)
    )
    filesystem_index_path: str = "~/.gaia/file_index.db"
    scratchpad_db_path: str = "~/.gaia/scratchpad.db"
    filesystem_scan_depth: int = 3  # Default scan depth (conservative)
    filesystem_exclude_patterns: List[str] = field(default_factory=list)

    # Browser settings
    enable_browser: bool = False  # Web browsing tools (disabled until agent split)
    browser_timeout: int = 30  # HTTP request timeout in seconds
    browser_max_download_size: int = 100 * 1024 * 1024  # 100 MB max download
    browser_rate_limit: float = 1.0  # Seconds between requests per domain

    # Session persistence (UI session ID for cross-turn document retention)
    ui_session_id: Optional[str] = None

    # Optional capability flags (disabled by default to keep document Q&A focused)
    enable_sd_tools: bool = False  # Stable Diffusion image generation

    # MCP settings.
    # 50 default is the validated middle ground: covers the 49-tool MCP
    # server tested on Gemma-4-E4B at 100% pass rate in PR #718 with one
    # tool of headroom, and is 5× the previous 10 limit (which silently
    # skipped any tool past index 10). Workflows with >50 tools should
    # override explicitly; for larger sets the prompt bloat can hurt
    # small-model accuracy and warrants its own validation run.
    mcp_tool_limit: int = 50  # Max MCP tools to register (prevents context bloat)

    # Prompt profile controls which tools and prompt sections are included.
    # Profiles keep the system prompt lean for task-specific agents:
    #   "chat"  — basic conversation only (personality, greetings, no RAG/file tools)
    #   "doc"   — document Q&A with RAG tools + hallucination prevention
    #   "file"  — file system operations, search, analysis
    #   "data"  — data analysis, CSV, tables (scratchpad)
    #   "web"   — web research, page fetching
    #   "full"  — all tools and prompt sections (backward-compatible default)
    prompt_profile: str = "full"

    # Dynamic tool loading (#1449) — experimental, default-off, doc profile only.
    # When on, each turn surfaces CORE + semantically-matched tools instead of
    # the full registry, shrinking first-turn prefill. Env overrides resolved in
    # __init__: GAIA_DYNAMIC_TOOLS / GAIA_DYNAMIC_TOOLS_TAU / GAIA_DYNAMIC_TOOLS_MAX.
    dynamic_tools: bool = False
    dynamic_tools_threshold: float = 0.20  # inclusive cosine; calibrated #1449
    dynamic_tools_max: int = 14  # cap (11 CORE + 3 dynamic slots)

    # Per-agent identity for the connectors activation filter (#1005).
    # Must be set BEFORE ``Agent.__init__`` runs ``_register_tools``, because
    # that's where ``_active_mcp_servers`` consults ``is_agent_active`` to
    # decide which MCP servers' tools to surface. The registry's
    # ``_wrap_factory_with_namespaced_id`` injects this via kwargs, and the
    # UI's direct construction paths in ``_chat_helpers`` pass it explicitly.
    # Leaving this ``None`` reproduces the pre-#1005 behaviour where the
    # agent sees every connected MCP server unfiltered — keep it set for
    # any built-in or registered Chat instance.
    namespaced_agent_id: Optional[str] = None


class ChatAgent(
    MemoryMixin,
    Agent,
    RAGToolsMixin,
    FileToolsMixin,
    ShellToolsMixin,
    FileSystemToolsMixin,
    ScratchpadToolsMixin,
    BrowserToolsMixin,
    FileSearchToolsMixin,
    FileIOToolsMixin,
    VLMToolsMixin,
    ScreenshotToolsMixin,
    SDToolsMixin,
    MCPClientMixin,
):
    """
    Chat Agent with RAG, file system navigation, data analysis, web browsing,
    and shell capabilities.

    This agent provides:
    - Document Q&A using RAG
    - File system browsing, search, and navigation
    - Structured data analysis via SQLite scratchpad
    - Web browsing, search, and file download
    - Shell command execution
    - Auto-indexing when files change
    - Interactive chat interface
    - Session persistence with auto-save
    - MCP server integration
    """

    # Dynamic MCP loader — registry exposes this for the Settings "Active for" panel.
    CONSUMES_MCP_SERVERS: ClassVar[bool] = True

    def __init__(self, config: Optional[ChatAgentConfig] = None):
        """
        Initialize Chat Agent.

        Args:
            config: ChatAgentConfig object with all settings. If None, uses defaults.
        """
        # Use provided config or create default
        if config is None:
            config = ChatAgentConfig()

        # Stamp the per-agent identity for the connectors activation filter
        # (#1005) BEFORE ``super().__init__`` runs ``_register_tools``. The
        # MCP-tool registration step in ``_register_tools`` consults
        # ``_active_mcp_servers`` which reads this attribute; setting it
        # after super().__init__ would be too late and the filter would
        # silently fall back to "ad-hoc agent — show every MCP server".
        if config.namespaced_agent_id:
            self._gaia_namespaced_agent_id = config.namespaced_agent_id

        # Initialize path validator
        self.path_validator = PathValidator(
            config.allowed_paths,
            on_prompt_start=lambda: self.console.pause_progress(),  # pylint: disable=unnecessary-lambda
            on_prompt_end=lambda: self.console.resume_progress(),  # pylint: disable=unnecessary-lambda
        )

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

        # Use the configured default model (Gemma) when no explicit model is set
        effective_model_id = config.model_id or DEFAULT_MODEL_NAME

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
            else os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")
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

        # Initialize file system index service (optional)
        self._fs_index = None
        self._path_validator = self.path_validator
        if config.enable_filesystem:
            try:
                from gaia.filesystem.index import FileSystemIndexService

                self._fs_index = FileSystemIndexService(
                    db_path=config.filesystem_index_path
                )
                logger.info("File system index service initialized")
            except (ImportError, OSError, sqlite3.Error) as e:
                logger.warning(
                    "File system index not available: %s. "
                    "Disable with config.enable_filesystem=False to silence.",
                    e,
                )

        # Initialize scratchpad service (optional)
        self._scratchpad = None
        if config.enable_scratchpad:
            try:
                from gaia.scratchpad.service import ScratchpadService

                self._scratchpad = ScratchpadService(db_path=config.scratchpad_db_path)
                logger.info("Scratchpad service initialized")
            except (ImportError, OSError, sqlite3.Error) as e:
                logger.warning(
                    "Scratchpad service not available: %s. "
                    "Disable with config.enable_scratchpad=False to silence.",
                    e,
                )

        # Initialize web client for browser tools (optional)
        self._web_client = None
        if config.enable_browser:
            try:
                from gaia.web.client import WebClient

                self._web_client = WebClient(
                    timeout=config.browser_timeout,
                    max_download_size=config.browser_max_download_size,
                    rate_limit=config.browser_rate_limit,
                )
                logger.info("Web client initialized for browser tools")
            except (ImportError, OSError) as e:
                logger.warning(
                    "Web client not available: %s. "
                    "Disable with config.enable_browser=False to silence.",
                    e,
                )

        # Session management
        self.session_manager = SessionManager()
        self.current_session = None
        self.conversation_history: List[Dict[str, str]] = (
            []
        )  # Track conversation for persistence
        # Dynamic tool loader (#1449): semantic per-turn tool selection. Built
        # only for the doc profile with the toggle on (config or env); otherwise
        # None → full registry / legacy prompt. Embedding fns are injected so the
        # loader never imports MemoryMixin; they resolve lazily on first select(),
        # by which point init_memory() has probed the embedder.
        self._dynamic_tools_validated = False
        self.tool_loader = self._maybe_build_tool_loader()

        # Initialize memory subsystem (before super().__init__ which calls _register_tools)
        self.init_memory()

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
            device=config.device,
            min_context_size=(
                config.min_context_size
                if config.min_context_size is not None
                else 32768
            ),
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
                # New conversation started for this UI session; clear the
                # per-session loaded set so selection doesn't persist across
                # distinct conversations.
                if self.tool_loader:
                    self.tool_loader.reset_session()

        # Start watching directories
        if self.watch_directories:
            self._start_watching()

    # ── dynamic tool loader (#1449) ───────────────────────────────────────

    def _maybe_build_tool_loader(self) -> Optional[ToolLoader]:
        """Construct the semantic tool loader, or ``None`` when inactive.

        Active only for the ``doc`` profile with the toggle resolved on (config
        field, overridable by ``GAIA_DYNAMIC_TOOLS``). Returning ``None`` leaves
        the agent on the full-registry / byte-identical legacy path.
        """
        if not self._resolve_dynamic_tools_enabled():
            return None
        if getattr(self.config, "prompt_profile", "full") != "doc":
            return None
        return ToolLoader(
            core_tools=DOC_CORE_TOOLS,
            bundles=DOC_BUNDLES,
            embed_fn=self._embed_text,
            embed_batch_fn=self._embed_texts_batch,
            threshold=self._resolve_dynamic_tools_threshold(),
            max_tools=self._resolve_dynamic_tools_max(),
        )

    def _resolve_dynamic_tools_enabled(self) -> bool:
        """Toggle: ``GAIA_DYNAMIC_TOOLS`` (truthy) wins over the config field."""
        override = dynamic_tools_env_override()
        if override is not None:
            return override
        return bool(self.config.dynamic_tools)

    def _resolve_dynamic_tools_threshold(self) -> float:
        """Threshold: ``GAIA_DYNAMIC_TOOLS_TAU`` wins; malformed value fails loudly."""
        raw = os.getenv("GAIA_DYNAMIC_TOOLS_TAU")
        if raw is None:
            return float(self.config.dynamic_tools_threshold)
        try:
            return float(raw)
        except ValueError as e:
            raise ValueError(
                f"GAIA_DYNAMIC_TOOLS_TAU must be a float, got {raw!r}"
            ) from e

    def _resolve_dynamic_tools_max(self) -> int:
        """Cap: ``GAIA_DYNAMIC_TOOLS_MAX`` wins; malformed value fails loudly."""
        raw = os.getenv("GAIA_DYNAMIC_TOOLS_MAX")
        if raw is None:
            return int(self.config.dynamic_tools_max)
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(
                f"GAIA_DYNAMIC_TOOLS_MAX must be an int, got {raw!r}"
            ) from e

    def _embed_texts_batch(self, texts) -> "Any":
        """Batch-embed *texts* into L2-normalized float32 rows (one call).

        Matches ``MemoryMixin._embed_text`` normalization so the loader's dot
        products are cosine similarities.
        """
        import numpy as np

        results = self._get_embedder().embed(list(texts), model=EMBEDDING_MODEL)
        vecs = np.asarray(results, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    def _dynamic_tools_active(self) -> bool:
        """True when dynamic selection should run this turn.

        Off-states (any → full registry): loader not built (toggle off / wrong
        profile), embedder session-disabled, or memory disabled
        (``GAIA_MEMORY_DISABLED`` tears down ``_memory_store``).
        """
        return (
            self.tool_loader is not None
            and not self.tool_loader.session_disabled
            and getattr(self, "_memory_store", None) is not None
        )

    def _select_tools_for_turn(self, user_input: str) -> Optional[List[str]]:
        """Return this turn's sorted tool subset, or ``None`` for the full registry.

        The SKILL signal (#1451) and the semantic query use deliberately
        different inputs: ``skill_tools`` derives from the **clean current goal**
        (``_refresh_recalled_skills`` ran recall on ``user_input`` alone, before
        this hook), while the semantic ``query`` is previous + current. The
        asymmetry is intentional — skill recall matches a goal-shaped trigger,
        not a conversation window, so feeding it the prior turn would blur the
        match; semantic selection wants the prior turn for context like
        "summarize it". ``_recalled_skill_tools`` is ``[]`` on every off-state
        (no recall / memory disabled), so the loader runs on CORE + semantic
        exactly as in Parts 1-2.
        """
        if not self._dynamic_tools_active():
            return None
        if not self._dynamic_tools_validated:
            # Fail loudly on first activation if a CORE/bundle name doesn't exist
            # in the live registry (drift). The reverse direction is the CI test.
            self.tool_loader.validate_registry(self._tools_registry)
            self._dynamic_tools_validated = True
        query = self._build_tool_selection_query(user_input)
        return self.tool_loader.select(
            query, self._tools_registry, skill_tools=self._recalled_skill_tools()
        )

    def _on_tool_invoked(self, tool_name: str) -> None:
        """Record tool-use recency for the loader's LRU (no-op when inactive)."""
        if self.tool_loader is not None:
            self.tool_loader.record_tool_use(tool_name)

    def _build_tool_selection_query(self, user_input: str) -> str:
        """Build the selection query: previous user message + current, last 4K chars.

        Open Q4 per the design sketch. The previous turn carries context the
        bare current message may lack (e.g. "summarize it"). Assistant replies
        and RAG chunks are excluded by construction — only user text. At hook
        time ``conversation_history`` holds prior turns only, so the last
        ``role=="user"`` entry is genuinely the previous message; turn 1 has
        none, so the query is just ``user_input``.
        """
        prev = ""
        for msg in reversed(getattr(self, "conversation_history", None) or []):
            if msg.get("role") == "user":
                prev = msg.get("content", "") or ""
                break
        combined = f"{prev}\n{user_input}" if prev else user_input
        return combined[-4000:]

    def _post_process_tool_result(
        self,
        tool_name: str,
        _tool_args: Dict[str, Any],
        tool_result: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Post-process tool results for Chat Agent.

        Handles RAG-specific debug information display, then delegates
        to ``super()`` so the base class can set ``_single_tool_done``
        for ``single_tool_per_turn=True`` agents on the success path.

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
        return super()._post_process_tool_result(tool_name, _tool_args, tool_result)

    def _get_mixin_prompts(self) -> list[str]:
        """Auto-discover mixin prompts, but exclude SD unless actually initialized."""
        prompts = super()._get_mixin_prompts()
        # Remove SD prompt if SD was not explicitly initialized (saves ~1000 tokens)
        if not hasattr(self, "sd_default_model"):
            prompts = [p for p in prompts if "Stable Diffusion" not in p]
        return prompts

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the Chat Agent."""
        # Get list of indexed documents
        indexed_docs_section = ""
        has_indexed = hasattr(self, "rag") and self.rag and self.rag.indexed_files
        has_library = hasattr(self, "library_documents") and self.library_documents

        if has_indexed:
            doc_names = sorted({Path(fp).name for fp in self.rag.indexed_files})
            n_docs = len(doc_names)

            # When exactly one doc is indexed, references like "this document",
            # "the document", "what is this about?" are unambiguous — answer
            # from that doc without asking for clarification. The "ask which
            # one" rule applies only when 2+ docs are indexed (#1030 follow-up:
            # the trim accidentally weakened this case so Gemma started asking
            # which document with only one indexed file present).
            if n_docs == 1:
                only = doc_names[0]
                resolution_rule = (
                    f"**SINGLE-DOC RESOLUTION (CRITICAL):** Exactly one document "
                    f'is indexed: `{only}`. References like "this document", '
                    f'"the document", "the file", "it", "what is this '
                    f'about?", or any unqualified question ALL refer to '
                    f"`{only}`. NEVER ask the user to clarify which document — "
                    f"there is only one. Call `query_specific_file` "
                    f"with file_path=`{only}` immediately and answer from the "
                    f"retrieved chunks."
                )
            else:
                resolution_rule = (
                    "**MULTI-DOC RESOLUTION:** Multiple documents are indexed. "
                    "If the user's question clearly names or implies one (e.g., "
                    '"the financial report", "the handbook"), `query_specific_file` '
                    'that one. If the question is vague ("summarize the doc", '
                    '"what does it say?") and could mean any of them, ask which '
                    "one before querying. For broad cross-doc questions, use "
                    "`query_documents` to search all indexed files at once."
                )

            indexed_docs_section = f"""
**CURRENTLY INDEXED DOCUMENTS:**
You have {n_docs} document(s) already indexed and ready to search:
{chr(10).join(f'- {name}' for name in doc_names)}

**MANDATORY RULE — RAG-FIRST:** When the user asks ANY question about the content, data, pricing, features, or details from these documents, you MUST call `query_documents` or `query_specific_file` BEFORE answering. Do NOT answer document-specific questions from your training knowledge — always retrieve from the indexed documents first.

{resolution_rule}

**ANTI-RE-INDEX RULE:** These documents are already indexed. Do NOT call `index_document` for any of these files again. Query them directly.

You do NOT need to check what's indexed first — this list is always up-to-date.
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

        base_prompt = f"""You are GAIA — a personal AI running locally on the user's machine. Sharp, witty, genuinely fun. Think: the smartest person at the party, who's also nice.
{platform_block}

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
- Output tool-call syntax as text (e.g. "[tool:query_specific_file]"). Issue the actual JSON tool call.
- Answer capability questions ("what can you do?") with bullet lists — single paragraph, 1-2 sentences max.

**OUTPUT FORMATTING:** Use Markdown — **bold** for emphasis, `inline code` for paths/commands, bullet/numbered lists for enumerations, ### headings for long responses, tables for tabular data, code blocks for snippets. Keep responses scannable.
"""

        # ── Tool usage rules (always present) ──
        tool_rules = """
**TOOL USAGE:**
- Greetings, general knowledge, conversation → answer directly, no tools.
- Indexed documents present + question about their content → ALWAYS call `query_documents` or `query_specific_file` FIRST, then answer from results. Never answer document-specific questions from training knowledge.
- No documents indexed → answer from your knowledge. Don't call RAG tools on empty indexes.
- Files / system info questions → use the matching tool. Always show `display_message` fields when present.

**POST-INDEX QUERY RULE (mandatory):** After `index_document`, your next action MUST be `query_specific_file` or `query_documents`. Never answer from the filename. Never call `list_indexed_documents` and answer — it only returns filenames, not content.

**FILE SEARCH:** Start with a quick search (no `deep_search`). Use `deep_search=true` only if the user asks again after a quick search returns nothing. Multiple matches → show numbered list and let the user pick.

**NEVER FAKE TOOL OUTPUT:** Don't write JSON blocks in your reply text simulating tool results. If you need data, call the tool. Saying "I already retrieved X" without prior-turn evidence is confabulation.
"""

        # ── Tier 1: Discovery workflow (compact form) ──
        discovery_rules = """
**SMART DISCOVERY WORKFLOW:**
1. Domain question + nothing relevant indexed: `find_files` with 1-2 document-type keywords (handbook / report / manual / policy / guide), NOT the question's content terms. If nothing after 2 tries, `browse_directory`.
2. Files found → `index_document` immediately (no confirmation), then query and answer in the same turn.
3. Already indexed → query directly.

**NEVER ASK PERMISSION TO INDEX.** "Would you like me to index this?" is BANNED. If a document is referenced and you can locate it, index + query + answer in one flow.

**SEARCH LOOP PREVENTION:** Same `find_files` / `browse_directory` query twice with same result → STOP and acknowledge.

**VAGUE FOLLOW-UP ("what about X?"):** find_files("X") → index_document → query_specific_file with whatever question is implicit, or "key topics overview" if none.
"""

        # ── Tier 1b: Optional tool sections — each block is only injected when
        # the corresponding mixin was actually registered. Without this gating
        # the LLM sees tool instructions for tools that don't exist and either
        # hallucinates them or emits syntactically-valid tool calls that come
        # back as "unknown tool" errors (#495 review feedback from @itomek-amd).
        profile = getattr(self.config, "prompt_profile", "full")
        filesystem_section = ""
        if profile in ("file", "full") or getattr(
            self.config, "enable_filesystem", False
        ):
            filesystem_section = """
**FILE SYSTEM TOOLS:** browse_directory (list folder), tree (visual hierarchy), file_info (metadata), find_files (search by name/content/size/date/type), read_file (text/CSV/JSON/PDF), bookmark (save locations).

**FILE SEARCH AND AUTO-INDEX WORKFLOW:**
- Use 1-2 distinctive keywords, not full phrases. WRONG: find_files("Acme Corp API reference"). RIGHT: find_files("api").
- First call must NOT use `deep_search=true`. Quick search covers CWD, Documents, Downloads, Desktop.
- 1 hit + content question (any "what / how / who / when / where") → index + query + answer in one turn, no confirmation.
- Multiple hits → numbered list, user picks. Zero hits → try a synonym, then `browse_directory`.
- Always surface tool `display_message` fields to the user.

**DIRECTORY BROWSING WORKFLOW:** "what's in my Documents?" → `browse_directory`. "show me the project structure" → `tree`. Specific-file metadata → `file_info`. Save a frequently-used path → `bookmark`.
"""

        scratchpad_section = ""
        if profile in ("data", "full") or getattr(
            self.config, "enable_scratchpad", False
        ):
            scratchpad_section = """
**DATA ANALYSIS WORKFLOW (Scratchpad):** find_files → create_table → read_file + insert_data per doc → query_data (SQL: SUM/AVG/GROUP BY) → drop_table when done.
"""

        browser_section = ""
        if profile in ("web", "full") or getattr(self.config, "enable_browser", False):
            browser_section = """
**BROWSER TOOLS:** search_web (DuckDuckGo, no key), fetch_page (extract readable text/links/tables), download_file (save URL locally; can then index_document).
"""

        # Tail of Tier 1: indexing note kept separately so gated sections can
        # be inserted between discovery_rules and this tail.
        discovery_rules_tail = """
**DIRECTORY INDEXING:** User asks to index a folder → search_directory → show matches → index_directory → report results.
"""

        # ── Tier 2: RAG query rules (only when documents are indexed) ──
        # Compact directive form. Each rule is one or two short bullets — no
        # multi-paragraph eval-survival walls. Together with `tool_rules` these
        # carry the same imperative directives as the previous long form,
        # without the per-rule example explosion that was inflating the prompt
        # past Gemma's iGPU prompt-processing budget (#1030).
        rag_query_rules = ""
        if has_indexed:
            rag_query_rules = """
**RAG ANSWERING RULES (documents are indexed):**

1. **FACTUAL ACCURACY RULE — always retrieve before answering.** Any factual question about indexed documents (numbers, dates, names, policies, sections) → call `query_specific_file` or `query_documents` first, then answer from the retrieved chunks. Don't answer from training knowledge, even if you "know" the topic. This applies on every turn — "indexed" means stored in the RAG index, NOT in your context window.

2. **Never invent content.** Quote numbers / dates / section refs verbatim from the retrieved chunks. Don't round, don't extrapolate, don't cite a section number you didn't see in a chunk. If the answer is not in the retrieved chunks, say "That's not in the document" and STOP — never supplement with "but approximately X" or "typically Y".

3. **Multi-fact requests → one query per fact.** If asked for 3 things, issue at least 3 targeted queries. Don't combine into one fuzzy query.

4. **Pick the right tool.** Specific document referenced → `query_specific_file`. Unsure which doc has the info → `query_documents`. Document overview / summary → `summarize_document` if available, else `query_specific_file(file, "overview summary key topics")`.

5. **Vague reference + 2+ docs indexed → ask which document first.** Once user disambiguates ("the financial one", "the second one") → query that doc immediately. Never re-index when disambiguation is the only thing missing.

6. **Tool loop prevention.** Same query terms returning the same chunks twice → STOP. After 2 unsuccessful retrieval attempts: acknowledge and answer with what you have. Pronouns ("that", "it") refer to data you ALREADY stated — check prior turn responses before issuing a new query.

7. **Conversation summary requests** ("what did you say?", "recap", "summarize what you told me") → answer from conversation history, not new tool calls. Repeat your prior facts verbatim — don't re-derive.

8. **Pushback on a correct answer** ("are you sure?") → restate firmly. Don't re-index. Don't soften.

9. **Computed values from prior turns are facts.** Don't re-derive a projection / total / range you already gave unless asked to recalculate.

10. **Source attribution.** When summarising answers across multiple docs, name the exact doc each fact came from. Don't conflate sources.

11. **Cross-turn doc reference** ("the file", "that document", "the python source") → already-indexed file from prior turn. Query directly, don't re-search.

12. **Negation scope.** If the doc says group X is NOT eligible for Y, never later extend "all employees" language to include X. The omission IS the answer.

13. **After every tool call, write the actual answer.** Never end on "I need to provide an answer..." — that's an internal thought, not a response.
"""

        # ── Data analysis rules (compact form) ──
        data_file_rules = """
**CSV / EXCEL DATA FILES:**
- Use `analyze_data_file` — NEVER `query_specific_file` / `query_documents` (RAG truncates rows).
- Pick params by question type:
  - "Top X by metric" → `group_by="column"` (result: `top_1`, `group_by_results` sorted desc)
  - "Total across all rows" → `analysis_type="summary"` (result: `summary.<col>.sum`)
  - Time-bounded → add `date_range="YYYY-MM-DD:YYYY-MM-DD"`
- Read exact numbers from the result dict; never do mental arithmetic. Lead the answer with the specific metric the user asked for, not a "comprehensive summary" preamble.

**FILE BROWSING:** browse_directory (navigate), list_recent_files (recent), get_file_info (metadata).

**IMAGE GENERATION (when SD enabled):** Always CALL `generate_image` first. Don't pre-announce availability. If it errors, state unavailable in 1-2 sentences (mention `--sd` flag); don't apologize or describe what you would have done.

**UNSUPPORTED:** Email, scheduling, cloud storage, file conversion, live collaboration, video/audio analysis — say not available and link https://github.com/amd/gaia/issues/new?template=feature_request.md . Web browsing IS supported via `search_web` / `fetch_page` / `download_file`. Image analysis IS supported via `analyze_image`.
"""

        # Assemble prompt based on profile
        profile = getattr(self.config, "prompt_profile", "full")

        if profile == "chat":
            # Minimal: personality only — but respect explicitly enabled tools.
            extras = filesystem_section + scratchpad_section + browser_section
            return base_prompt + extras

        if profile == "doc":
            # Document Q&A: RAG tools + hallucination prevention.
            # Native-only escape-hatch menu (#1450): non-native models already
            # self-recover via the free full-registry path and are the
            # TTFT-sensitive case, so we don't tax them with the menu. Lives in
            # this stable prefix (before the volatile tools tail) → no KV thrash.
            load_tools_menu = ""
            loader = getattr(self, "tool_loader", None)
            if loader is not None and is_tool_calling_model(
                getattr(self, "model_id", None)
            ):
                load_tools_menu = (
                    "\n\n==== LOADABLE TOOL BUNDLES ====\n"
                    "Your visible tools are trimmed to what this turn needs. If a "
                    "capability you need is missing, call load_tools(bundle) with "
                    "one of these names; its tools become available on your next "
                    "step:\n" + loader.format_bundle_menu()
                )
            return (
                base_prompt
                + indexed_docs_section
                + tool_rules
                + discovery_rules
                + discovery_rules_tail
                + rag_query_rules
                + load_tools_menu
            )

        if profile == "file":
            # File operations: file system + search + discovery
            return (
                base_prompt
                + tool_rules
                + discovery_rules
                + filesystem_section
                + discovery_rules_tail
            )

        if profile == "data":
            # Data analysis: scratchpad + file tools
            return base_prompt + tool_rules + scratchpad_section + data_file_rules

        if profile == "web":
            # Web research: browser tools
            return base_prompt + browser_section

        # "full" — all sections (backward-compatible default)
        return (
            base_prompt
            + indexed_docs_section
            + tool_rules
            + discovery_rules
            + filesystem_section
            + scratchpad_section
            + browser_section
            + discovery_rules_tail
            + rag_query_rules
            + data_file_rules
        )

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
        """Register chat agent tools from mixins based on prompt_profile."""
        from gaia.agents.base.tools import tool

        profile = getattr(self.config, "prompt_profile", "full")

        # "chat" profile: no tools — just personality/conversation
        if profile == "chat":
            # Minimal: only shell for system queries
            self.register_shell_tools()
            self._register_external_tools_conditional()
            return

        # All other profiles get at least shell tools
        self.register_shell_tools()
        self.register_memory_tools()  # Persistent memory tools

        if profile in ("doc", "full"):
            self.register_rag_tools()
            # Doc profile needs file search for smart discovery workflow
            self.register_file_tools()
            self.register_file_search_tools()

        if profile in ("file", "full"):
            self.register_file_tools()
            self.register_filesystem_tools()
            self.register_file_search_tools()
            self.register_file_io_tools()

        if profile in ("data", "full"):
            self.register_scratchpad_tools()
            if profile == "data":
                # Data profile also needs file tools to find/read data files
                self.register_file_tools()
                self.register_file_search_tools()
                self.register_file_io_tools()

        if profile in ("web", "full"):
            self.register_browser_tools()

        if profile == "full":
            self.register_screenshot_tools()
        self._register_external_tools_conditional()
        self._register_loop_control_tools()  # set_loop_state, request_user_input

        # load_tools escape hatch (#1450, Part 2) — registered ONLY when the
        # dynamic loader is active, so the default-off doc path stays
        # byte-identical. It is in DOC_CORE_TOOLS, so once registered it renders
        # in both prompt paths every active turn (cap- and eviction-exempt).
        if self.tool_loader is not None:

            @tool
            def load_tools(bundle: str) -> dict:
                """Load a bundle of tools so you can call them on your next step.

                Call this when the capability you need is not in your current
                tool list. If a "Loadable tool bundles" menu is shown in your
                instructions, pick a bundle name from it; otherwise pass the name
                of the specific tool you need and its bundle is loaded. The
                bundle's tools become available on your **next** step; then call
                the one you need.

                Args:
                    bundle: A bundle name (e.g. "file_search", "rag_index") — from
                        the menu when one is shown — or a specific tool name to
                        load its owning bundle.

                Returns:
                    Dictionary with status, the resolved bundle, and the full
                    loaded_tools list now available to call.
                """
                # load_tools is registered only inside ``if self.tool_loader is
                # not None`` and the loader is never re-nulled after construction,
                # so the loader is always live here.
                loader = self.tool_loader
                try:
                    loaded = loader.load_bundle(bundle, self._tools_registry)
                except KeyError:
                    return {
                        "status": "error",
                        "error": f"Unknown bundle '{bundle}'. Choose one of: "
                        f"{', '.join(loader.bundle_names())}",
                    }
                # Make the expansion visible to the next model step in this query.
                self._apply_tool_filter(loaded)
                return {
                    "status": "success",
                    "bundle": bundle,
                    "loaded_tools": loaded,
                }

        # Inline list_files — only for profiles that need file operations
        if profile in ("file", "data", "full"):

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
                    dirs = sorted(
                        i for i in items if os.path.isdir(os.path.join(path, i))
                    )
                    return {
                        "status": "success",
                        "path": path,
                        "files": files,
                        "directories": dirs,
                        "total": len(items),
                    }
                except FileNotFoundError:
                    return {
                        "status": "error",
                        "error": f"Directory not found: {path}",
                    }
                except PermissionError:
                    return {
                        "status": "error",
                        "error": f"Permission denied: {path}",
                    }
                except Exception as e:
                    return {"status": "error", "error": str(e)}

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
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path}",
                    }

                p = Path(file_path)
                if not p.exists():
                    return {
                        "status": "error",
                        "error": f"File not found: {file_path}",
                    }
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
                base_url=getattr(self, "_base_url", "http://localhost:13305/api/v1")
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
        # Only register web tools for profiles that should browse the internet.
        # Doc/file/data profiles should NOT have fetch_webpage to avoid confusing
        # the LLM into web-browsing when it should use RAG.
        _web_profiles = ("web", "full")

        if profile in _web_profiles:

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
            elif system == "Darwin":
                # macOS: AppleScript via osascript (always present on Mac).
                # System Events returns every visible (non-background) process,
                # which is the equivalent of "open apps" for users.
                try:
                    import subprocess

                    script = (
                        'tell application "System Events" to get name of '
                        "every process whose background only is false"
                    )
                    result = subprocess.run(
                        ["osascript", "-e", script],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        # osascript returns names comma-space-separated.
                        names = [
                            x.strip()
                            for x in result.stdout.strip().split(",")
                            if x.strip()
                        ]
                        for nm in names:
                            windows.append({"title": nm, "process": nm})
                        return {
                            "status": "success",
                            "windows": windows,
                            "count": len(windows),
                            "note": (
                                "macOS: visible apps from System Events "
                                "(Mission Control equivalent)"
                            ),
                        }
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
                return {
                    "status": "error",
                    "error": (
                        "Window listing failed on macOS. osascript may "
                        "have been blocked by accessibility permissions."
                    ),
                }
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
        # Hard limit: skip if MCP would add too many tools (context bloat guard).
        # Configurable via ChatAgentConfig.mcp_tool_limit (default 50).
        _MCP_TOOL_LIMIT = self.config.mcp_tool_limit
        _mcp_config_path = Path.home() / ".gaia" / "mcp_servers.json"
        if _mcp_config_path.exists() and self._mcp_manager is not None:
            try:
                self._mcp_manager.load_from_config()
                self._print_mcp_load_summary()
                # Filter to servers explicitly activated for this agent
                # (issue #1005). Falls back to the unfiltered list when the
                # agent has no registry identity, preserving prior behaviour
                # for ad-hoc test agents.
                _active_servers = self._active_mcp_servers(self._mcp_manager)
                # Preview total tool count before registering
                _mcp_tool_count = sum(
                    len(_c.list_tools())
                    for _srv in _active_servers
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
                    for _srv in _active_servers:
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

        # Snapshot: freeze this agent's tool set so mutations by other agents
        # in the same process do not leak in.  Exclusion replaces the old
        # _TOOL_REGISTRY.pop() pattern that corrupted the global dict.
        self._snapshot_tools()
        if profile in ("file", "data", "full"):
            _chat_exclude = {
                "write_python_file",
                "edit_python_file",
                "search_code",
                "generate_diff",
                "write_markdown_file",
                "update_gaia_md",
                "replace_function",
            }
            for _name in _chat_exclude:
                self._instance_tools.pop(_name, None)

    def _register_loop_control_tools(self) -> None:
        """Register set_loop_state and request_user_input tools for autonomous mode.

        These tools are only meaningful when the agent runs inside AgentLoop, but
        they are always registered so the LLM sees them in the system prompt and
        can call them safely even in manual/interactive sessions (they no-op in
        that context because the console has no loop_state_directive attribute).
        """
        from gaia.agents.base.tools import tool

        _agent = self

        @tool
        def set_loop_state(
            state: str,
            reason: str = "",
            wake_in_seconds: int = 0,
        ) -> str:
            """Signal the autonomous loop what to do next.

            Call this when you have finished all current work or need to pause.
            Only effective when running inside the AgentLoop (autonomous mode).

            Args:
                state: "idle"      — nothing more to do right now.
                       "scheduled" — wake me up in wake_in_seconds seconds.
                       "paused"    — stop the autonomous loop (user must restart).
                reason: Human-readable explanation (shown in activity log).
                wake_in_seconds: Seconds until next wakeup (only for "scheduled").
                    Minimum: 30. Maximum: 86400 (24 hours).
            """
            valid_states = {"idle", "scheduled", "paused"}
            if state not in valid_states:
                return (
                    f"Invalid state '{state}'. Must be one of: {sorted(valid_states)}"
                )

            # Clamp wake_in_seconds for "scheduled" state
            if state == "scheduled":
                wake_in_seconds = max(30, min(86400, wake_in_seconds))

            if hasattr(_agent.console, "loop_state_directive"):
                _agent.console.loop_state_directive = {
                    "directive": state,
                    "reason": reason,
                    "wake_in_seconds": wake_in_seconds,
                }

            msg = f"Loop state set to '{state}'."
            if reason:
                msg += f" Reason: {reason}"
            if state == "scheduled":
                msg += f" Next wakeup in {wake_in_seconds}s."
            return msg

        @tool
        def request_user_input(
            message: str,
            choices: list = None,
            default_if_no_response: str = None,
            timeout_seconds: int = 300,
            continue_if_no_response: bool = True,
        ) -> str:
            """Ask the user a question and wait for their response.

            Args:
                message: The question to present to the user.
                choices: Optional list of choices (renders as buttons in the UI).
                default_if_no_response: Value to use if no response received before
                    timeout. If not set and continue_if_no_response=True, returns
                    "__NO_RESPONSE__". Always check the return value.
                timeout_seconds: How long to wait (min 10, default 300).
                continue_if_no_response: If True, continue after timeout.
                    If False, the loop pauses until user re-engages.

            Returns:
                User's response, chosen option, or "__NO_RESPONSE__" on timeout.
                ALWAYS check for "__NO_RESPONSE__" before proceeding.
            """
            console = _agent.console
            if hasattr(console, "request_user_input_blocking"):
                return console.request_user_input_blocking(
                    message=message,
                    choices=choices,
                    default_if_no_response=default_if_no_response,
                    timeout_seconds=timeout_seconds,
                    continue_if_no_response=continue_if_no_response,
                )
            # Fallback for non-SSE consoles (interactive sessions)
            try:
                return (
                    input(f"\n[Agent asking]: {message}\n> ").strip()
                    or "__NO_RESPONSE__"
                )
            except (EOFError, OSError):
                return (
                    default_if_no_response
                    if default_if_no_response is not None
                    else "__NO_RESPONSE__"
                )

    # NOTE: The actual tool definitions are in the mixin classes:
    # - RAGToolsMixin (rag_tools.py): RAG and document indexing tools
    # - FileToolsMixin (file_tools.py): Directory monitoring
    # - ShellToolsMixin (shell_tools.py): Shell command execution
    # - FileSystemToolsMixin (shared): File system browsing, search, tree, bookmarks
    # - ScratchpadToolsMixin (shared): SQLite working memory for data analysis
    # - BrowserToolsMixin (shared): Web browsing, content extraction, download
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
        """Cleanup when agent is destroyed.

        Releases watchdog observers, HTTP session, and the two SQLite
        connections owned by this agent. ``__del__`` is best-effort — Python
        doesn't guarantee it fires on interpreter shutdown — but explicit
        close() makes tests deterministic (WAL journals released, file handles
        closed) and avoids leaking Session/connection objects in long-running
        services like the Agent UI backend.
        """
        try:
            self.stop_watching()
        except Exception as e:
            logger.error(f"Error stopping file watchers during cleanup: {e}")
        try:
            if self._web_client:
                self._web_client.close()
        except Exception as e:
            logger.error(f"Error closing web client during cleanup: {e}")
        try:
            if self._fs_index:
                self._fs_index.close_db()
        except Exception as e:
            logger.error(f"Error closing file system index during cleanup: {e}")
        try:
            if self._scratchpad:
                self._scratchpad.close_db()
        except Exception as e:
            logger.error(f"Error closing scratchpad during cleanup: {e}")
