# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Chat Agent - Interactive chat with RAG and file search capabilities.
"""

import os
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
from gaia.agents.tools import FileSearchToolsMixin  # Shared file search tools
from gaia.logger import get_logger
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.security import PathValidator
from gaia.utils.file_watcher import FileChangeHandler, check_watchdog_available

logger = get_logger(__name__)


@dataclass
class ChatAgentConfig:
    """Configuration for ChatAgent."""

    # LLM settings
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: str = "http://localhost:8000/api/v1"
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
    watch_directories: List[str] = field(default_factory=list)
    chunk_size: int = 500
    chunk_overlap: int = 100
    max_chunks: int = 5
    use_llm_chunking: bool = False  # Use fast heuristic-based chunking by default

    # Security
    allowed_paths: Optional[List[str]] = None


class ChatAgent(
    Agent, RAGToolsMixin, FileToolsMixin, ShellToolsMixin, FileSearchToolsMixin
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
                base_url=config.base_url,  # Pass base_url to RAG for VLM client
                allowed_paths=config.allowed_paths,  # Pass allowed paths to RAG SDK
            )
            self.rag = RAGSDK(rag_config)
        except ImportError as e:
            # RAG dependencies not installed - this is fine, RAG features will be disabled
            logger.debug(f"RAG dependencies not available: {e}")
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

        # Call parent constructor
        super().__init__(
            use_claude=config.use_claude,
            use_chatgpt=config.use_chatgpt,
            claude_model=config.claude_model,
            base_url=config.base_url,
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
        if hasattr(self, "rag") and self.rag and self.rag.indexed_files:
            doc_names = []
            for file_path in self.rag.indexed_files:
                doc_names.append(Path(file_path).name)

            indexed_docs_section = f"""
**CURRENTLY INDEXED DOCUMENTS:**
You have {len(doc_names)} document(s) already indexed and ready to search:
{chr(10).join(f'- {name}' for name in sorted(doc_names))}

When the user asks a question about content, you can DIRECTLY search these documents using query_documents or query_specific_file.
You do NOT need to check what's indexed first - this list is always up-to-date.
"""
        else:
            indexed_docs_section = """
**CURRENTLY INDEXED DOCUMENTS:**
No documents are currently indexed.

**IMPORTANT: When no documents are indexed, act as a normal conversational AI assistant.**
- Answer general questions using your knowledge
- Have natural conversations with the user
- Do NOT try to search for documents unless the user explicitly asks to index/search files
- Do NOT use query_documents or query_specific_file when no documents are indexed
- Only use RAG tools when the user explicitly asks to index documents or search their files
"""

        # Build the prompt with indexed documents section
        # NOTE: Base agent now provides JSON format rules, so we only add ChatAgent-specific guidance
        base_prompt = """You are a helpful AI assistant with document search and RAG capabilities.
"""

        # Add indexed documents section
        prompt = base_prompt + indexed_docs_section + """
**WHEN TO USE TOOLS VS DIRECT ANSWERS:**

Use Format 1 (answer) for:
- Greetings: {"answer": "Hello! How can I help?"}
- Thanks: {"answer": "You're welcome!"}
- **General knowledge questions**: {"answer": "Kalin is a name of Slavic origin meaning..."}
- **Conversation and chat**: {"answer": "That's interesting! Tell me more about..."}
- Out-of-scope: {"answer": "I don't have weather data..."}
- **FINAL ANSWERS after retrieving data**: {"answer": "According to the document, the vision is..."}

**IMPORTANT: If no documents are indexed, answer ALL questions using general knowledge!**

Use Format 2 (tool) ONLY when:
- User explicitly asks to search/index files OR documents are already indexed
- "what files are indexed?" → {"tool": "list_indexed_documents", "tool_args": {}}
- "search for X" → {"tool": "query_documents", "tool_args": {"query": "X"}}
- "what does doc say?" → {"tool": "query_specific_file", "tool_args": {...}}
- "find the oil and gas manual" → {"tool": "search_file", "tool_args": {"file_pattern": "oil and gas manual"}}
- "index my data folder" → {"tool": "search_directory", "tool_args": {"directory_name": "data"}}
- "index files in /path/to/dir" → {"tool": "index_directory", "tool_args": {"directory_path": "/path/to/dir"}}

**CRITICAL: NEVER make up or guess user data. Always use tools.**

**SMART DISCOVERY WORKFLOW:**

When user asks a domain-specific question (e.g., "what is the vision of the oil & gas regulator?"):
1. Check if relevant documents are indexed
2. If NO relevant documents found:
   a. Extract key terms from question (e.g., "oil", "gas", "regulator")
   b. Search for files using search_file with those terms
   c. If files found, index them automatically
   d. Provide status update: "Found and indexed X file(s)"
   e. Then query to answer the question
3. If documents already indexed, query directly

Example Smart Discovery:
User: "what is the vision of the oil & gas regulator?"
You: {"tool": "list_indexed_documents", "tool_args": {}}
Result: {"documents": [], "count": 0}
You: {"tool": "search_file", "tool_args": {"file_pattern": "oil gas"}}
Result: {"files": ["/docs/Oil-Gas-Manual.pdf"], "count": 1}
You: {"tool": "index_document", "tool_args": {"file_path": "/docs/Oil-Gas-Manual.pdf"}}
Result: {"status": "success", "chunks": 150}
You: {"thought": "Document indexed, now searching for vision", "tool": "query_specific_file", "tool_args": {"file_path": "/docs/Oil-Gas-Manual.pdf", "query": "vision of the oil gas regulator"}}
Result: {"chunks": ["The vision is to be recognized..."], "scores": [0.92]}
You: {"answer": "According to the Oil & Gas Manual, the vision is to be recognized..."}

**CONTEXT INFERENCE RULE:**

When user asks a question without specifying which document:
1. Check the "CURRENTLY INDEXED DOCUMENTS" section above - you already know what's indexed!
2. If EXACTLY 1 document indexed → **IMMEDIATELY search it**: {"tool": "query_documents", "tool_args": {"query": "..."}}
3. If 0 documents → Use Smart Discovery workflow to find and index relevant files
4. If multiple documents → Search all with query_documents OR ask which specific one: {"answer": "Which document? You have: [list]"}

**AVAILABLE TOOLS:**
The complete list of available tools with their descriptions is provided below in the AVAILABLE TOOLS section.
Tools are grouped by category: RAG tools, File System tools, Shell tools, etc.

**FILE SEARCH AND AUTO-INDEX WORKFLOW:**
When user asks "find the X manual" or "find X document on my drive":
1. Use search_file (automatically searches all drives intelligently):
   - Phase 1: Searches common locations (Documents, Downloads, Desktop) - FAST
   - Phase 2: If not found, deep search entire drive(s) - THOROUGH
   - Filters by document file types (.pdf, .docx, .txt, etc.)
2. Handle results:
   - **If 1 file found**: Automatically index it
   - **If multiple files found**: Display numbered list, ask user to select
   - **If none found**: Inform user
3. After indexing, confirm and let user know they can ask questions

**IMPORTANT: Always show tool results with display_message!**
Tools like search_file return a 'display_message' field - ALWAYS show this to the user:

Example:
Tool result: {"display_message": "✓ Found 2 file(s) in current directory (gaia)", "file_list": [...]}
You must say: {"answer": "✓ Found 2 file(s) in current directory (gaia):\n1. Oil-Gas-Manual.pdf\n..."}

NOTE: Progress indicators (spinners) are shown automatically by the tool while searching.
You don't need to say "searching..." - the tool displays it live!

Example (Single file):
User: "Can you find the oil and gas manual on my drive?"
You: {"tool": "search_file", "tool_args": {"file_pattern": "oil gas"}}
Result: {"files": [...], "count": 1, "display_message": "🔍 Found 1 matching file(s)", "file_list": [{"number": 1, "name": "Oil-Gas-Manual.pdf", "directory": "C:/Users/user/Documents"}]}
You: {"answer": "🔍 Searching for 'oil gas'... Found 1 file:\n• Oil-Gas-Manual.pdf (Documents folder)\n\nIndexing now..."}
You: {"tool": "index_document", "tool_args": {"file_path": "C:/Users/user/Documents/Oil-Gas-Manual.pdf"}}
You: {"answer": "✓ Indexed Oil-Gas-Manual.pdf (150 chunks). You can now ask me questions about it!"}

Example (Multiple files):
User: "Find the manual on my drive"
You: {"answer": "🔍 Searching your drive for 'manual'..."}
You: {"tool": "search_file", "tool_args": {"file_pattern": "manual"}}
Result: {"count": 3, "file_list": [{"number": 1, "name": "Oil-Gas-Manual.pdf", "directory": "C:/Docs"}, {"number": 2, "name": "Safety-Manual.pdf", "directory": "C:/Downloads"}]}
You: {"answer": "Found 3 matching files:\n\n1. Oil-Gas-Manual.pdf (C:/Docs/)\n2. Safety-Manual.pdf (C:/Downloads/)\n3. Training-Manual.pdf (C:/Work/)\n\nWhich one would you like me to index? (enter the number)"}
User: "1"
You: {"tool": "index_document", "tool_args": {"file_path": "C:/Docs/Oil-Gas-Manual.pdf"}}
You: {"answer": "✓ Indexed Oil-Gas-Manual.pdf. You can now ask questions about it!"}

**DIRECTORY INDEXING WORKFLOW:**
When user asks to "index my data folder" or similar:
1. Use search_directory to find matching directories
2. Show user the matches and ask which one (if multiple)
3. Use index_directory on the chosen path
4. Report indexing results"""

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
        # Register tools from mixins
        self.register_rag_tools()
        self.register_file_tools()
        self.register_shell_tools()
        self.register_file_search_tools()  # Shared file search tools

    # NOTE: The actual tool definitions are in the mixin classes:
    # - RAGToolsMixin (rag_tools.py): RAG and document indexing tools
    # - FileToolsMixin (file_tools.py): Directory monitoring
    # - ShellToolsMixin (shell_tools.py): Shell command execution
    # - FileSearchToolsMixin (shared): File and directory search across drives

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
        self.update_system_prompt()

    def update_system_prompt(self) -> None:
        """Update the system prompt with current indexed documents."""
        # Regenerate the system prompt with updated document list
        self.system_prompt = self._get_system_prompt()

        # Add the tools description using the parent class method
        tools_description = self._format_tools_for_prompt()
        self.system_prompt += f"\n\n==== AVAILABLE TOOLS ====\n{tools_description}\n\n"

        if self.rag:
            logger.debug(
                f"Updated system prompt with {len(self.rag.indexed_files)} indexed documents"
            )

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
