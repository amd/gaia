# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for Chat Agent with RAG capabilities.
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# ChatAgent ships as the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402


class TestChatAgent:
    """Test suite for Chat Agent."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def sample_pdf(self, temp_dir):
        """Create a sample PDF for testing (placeholder)."""
        # Note: In real tests, you'd create an actual PDF
        # For now, return a path that the test can check
        pdf_path = Path(temp_dir) / "test.pdf"
        return str(pdf_path)

    @pytest.fixture
    def agent(self, temp_dir):
        """Create Chat Agent instance."""
        # Use absolute paths for configuration
        # On macOS, temp_dir might be in /var/... but resolved to /private/var/...
        # We MUST use the resolved path for ChatAgent configuration because it uses
        # realpath() internally for validation.
        resolved_temp_dir = str(Path(temp_dir).resolve())
        config = ChatAgentConfig(
            silent_mode=True,
            debug=False,
            max_steps=5,
            allowed_paths=[resolved_temp_dir, str(Path.cwd().resolve())],
        )
        agent = ChatAgent(config)
        yield agent
        # Cleanup
        agent.stop_watching()

    @pytest.fixture
    def mock_llm_response(self):
        """Create a mock LLM response for testing without real LLM server."""
        mock_response = Mock()
        mock_response.text = "Mocked response for testing"
        mock_response.stats = {"tokens": 50}
        mock_response.tool_calls = []
        return mock_response

    def test_agent_initialization(self, agent):
        """Test agent initializes correctly."""
        assert agent is not None
        assert agent.rag is not None
        assert len(agent.indexed_files) == 0

    def test_list_indexed_documents(self, agent, mock_llm_response):
        """Test listing indexed documents."""
        with patch.object(agent.chat, "send_messages", return_value=mock_llm_response):
            result = agent.process_query("List all indexed documents")
            assert result["status"] in ["success", "incomplete"]
            assert "result" in result

    def test_rag_status(self, agent, mock_llm_response):
        """Test RAG system status."""
        with patch.object(agent.chat, "send_messages", return_value=mock_llm_response):
            result = agent.process_query("Show RAG system status")
            assert result["status"] in ["success", "incomplete"]
            assert "result" in result

    def test_query_without_documents(self, agent, mock_llm_response):
        """Test querying when no documents are indexed."""
        with patch.object(agent.chat, "send_messages", return_value=mock_llm_response):
            result = agent.process_query("What is machine learning?")
            assert result["status"] in ["success", "incomplete"]
            # Should handle gracefully

    @pytest.mark.parametrize(
        "query,expected_keys",
        [
            ("What is AI?", ["What is AI?", "AI"]),
            ("How to train a model?", ["How to train a model?", "train model"]),
        ],
    )
    def test_search_key_generation(self, agent, query, expected_keys):
        """Test search key generation."""
        keys = agent._generate_search_keys(query)
        assert len(keys) > 0
        assert query in keys  # Original query should always be included
        # Check if at least one expected key is present
        assert any(key in " ".join(keys) for key in expected_keys)

    def test_indexed_document_reaches_the_next_turn(self, agent):
        """After /index, the next turn names the document; the prompt stays put.

        The indexed set is sent with each turn rather than in the system
        prompt, so indexing never invalidates the server's prompt cache.
        """
        # Use a test file in the project directory (within allowed paths)
        test_dir = Path(__file__).parent / "test_data"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "test_document_for_prompt.txt"

        try:
            test_file.write_text("This is test content about machine learning and AI.")

            # Verify initial state: no documents indexed
            assert "[Indexed documents:" not in agent.get_memory_dynamic_context()
            prompt_before = agent.system_prompt

            # Mock the LemonadeClient to avoid needing server
            mock_lemonade = Mock()
            mock_lemonade.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }

            # Create a custom _load_embedder that sets our mock
            def mock_load_embedder():
                agent.rag.llm_client = mock_lemonade
                agent.rag.embedder = mock_lemonade
                agent.rag.use_lemonade_embeddings = True

            with (
                patch("gaia.rag.sdk.faiss") as mock_faiss,
                patch.object(agent.rag, "_load_embedder", mock_load_embedder),
            ):
                # Setup mock FAISS index
                mock_index = Mock()
                mock_index.ntotal = 1
                mock_faiss.IndexFlatL2.return_value = mock_index

                # Step 1: Index the document (what /index command does)
                result = agent.rag.index_document(str(test_file))
                assert result.get("success"), f"Indexing failed: {result.get('error')}"

            # Step 2: Update the system prompt (what /index command should do after indexing)
            agent.rebuild_system_prompt()

            assert (
                "[Indexed documents: test_document_for_prompt.txt]"
                in agent.get_memory_dynamic_context()
            )
            assert agent.system_prompt == prompt_before
        finally:
            # Cleanup
            if test_file.exists():
                test_file.unlink()
            if test_dir.exists() and not any(test_dir.iterdir()):
                test_dir.rmdir()

    def test_indexed_files_tracked_after_index(self, agent):
        """Test that indexed files are tracked in agent.indexed_files after indexing."""
        test_dir = Path(__file__).parent / "test_data"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "test_tracking.txt"

        try:
            test_file.write_text("Content for tracking test.")

            # Verify initial state
            assert len(agent.rag.indexed_files) == 0

            # Mock the LemonadeClient to avoid needing server
            mock_lemonade = Mock()
            mock_lemonade.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }

            # Create a custom _load_embedder that sets our mock
            def mock_load_embedder():
                agent.rag.llm_client = mock_lemonade
                agent.rag.embedder = mock_lemonade
                agent.rag.use_lemonade_embeddings = True

            with (
                patch("gaia.rag.sdk.faiss") as mock_faiss,
                patch.object(agent.rag, "_load_embedder", mock_load_embedder),
            ):
                # Setup mock FAISS index
                mock_index = Mock()
                mock_index.ntotal = 1
                mock_faiss.IndexFlatL2.return_value = mock_index

                # Index the document
                result = agent.rag.index_document(str(test_file))
                assert result.get("success"), f"Indexing failed: {result.get('error')}"

            # Verify file is tracked
            assert str(test_file) in agent.rag.indexed_files
            assert len(agent.rag.indexed_files) == 1
        finally:
            if test_file.exists():
                test_file.unlink()
            if test_dir.exists() and not any(test_dir.iterdir()):
                test_dir.rmdir()

    def test_multiple_documents_indexed(self, agent):
        """Test indexing multiple documents updates system prompt correctly."""
        test_dir = Path(__file__).parent / "test_data"
        test_dir.mkdir(exist_ok=True)
        test_file1 = test_dir / "doc1.txt"
        test_file2 = test_dir / "doc2.txt"

        try:
            test_file1.write_text("First document about Python programming.")
            test_file2.write_text("Second document about machine learning.")

            # Mock the LemonadeClient to avoid needing server
            mock_lemonade = Mock()
            mock_lemonade.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }

            # Create a custom _load_embedder that sets our mock
            def mock_load_embedder():
                agent.rag.llm_client = mock_lemonade
                agent.rag.embedder = mock_lemonade
                agent.rag.use_lemonade_embeddings = True

            with (
                patch("gaia.rag.sdk.faiss") as mock_faiss,
                patch.object(agent.rag, "_load_embedder", mock_load_embedder),
            ):
                # Setup mock FAISS index
                mock_index = Mock()
                mock_index.ntotal = 2
                mock_faiss.IndexFlatL2.return_value = mock_index

                # Index both documents
                result1 = agent.rag.index_document(str(test_file1))
                result2 = agent.rag.index_document(str(test_file2))
                assert result1.get(
                    "success"
                ), f"Indexing failed: {result1.get('error')}"
                assert result2.get(
                    "success"
                ), f"Indexing failed: {result2.get('error')}"

            agent.rebuild_system_prompt()

            # Both reach the next turn; the system prompt names neither.
            line = agent.get_memory_dynamic_context()
            assert "[Indexed documents: doc1.txt, doc2.txt]" in line
            assert "doc1.txt" not in agent.system_prompt
            assert len(agent.rag.indexed_files) == 2
        finally:
            for f in [test_file1, test_file2]:
                if f.exists():
                    f.unlink()
            if test_dir.exists() and not any(test_dir.iterdir()):
                test_dir.rmdir()

    def test_rag_chunks_created_after_index(self, agent):
        """Test that RAG chunks are created after indexing a document."""
        test_dir = Path(__file__).parent / "test_data"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "test_chunks.txt"

        try:
            # Create content that will generate chunks
            test_file.write_text(
                "This is a test document with enough content to create chunks. "
                "It contains information about artificial intelligence and machine learning. "
                "The document discusses various topics including neural networks and deep learning."
            )

            # Verify initial state
            assert len(agent.rag.chunks) == 0

            # Mock the LemonadeClient to avoid needing server
            mock_lemonade = Mock()
            mock_lemonade.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }

            # Create a custom _load_embedder that sets our mock
            def mock_load_embedder():
                agent.rag.llm_client = mock_lemonade
                agent.rag.embedder = mock_lemonade
                agent.rag.use_lemonade_embeddings = True

            with (
                patch("gaia.rag.sdk.faiss") as mock_faiss,
                patch.object(agent.rag, "_load_embedder", mock_load_embedder),
            ):
                # Setup mock FAISS index
                mock_index = Mock()
                mock_index.ntotal = 1
                mock_faiss.IndexFlatL2.return_value = mock_index

                # Index the document
                result = agent.rag.index_document(str(test_file))
                assert result.get("success"), f"Indexing failed: {result.get('error')}"

            # Verify chunks were created
            assert len(agent.rag.chunks) > 0
            assert result.get("num_chunks", 0) > 0
        finally:
            if test_file.exists():
                test_file.unlink()
            if test_dir.exists() and not any(test_dir.iterdir()):
                test_dir.rmdir()

    def test_tier2_rag_rules_present_before_any_index(self, agent):
        """Tier 2 query rules are in the prompt before anything is indexed.

        Gating them on the first index re-read the whole ~19K-token prompt
        mid-turn (~60 s on an iGPU); always present, they are read once at
        warm-up and stay cached.

        RAG tools are always registered.  Tier 1 discovery guidance is always
        present in some form — when no files are loaded the agent shows a
        *compact* hint (search_file → index_document → query_*); when docs
        or a library are present it expands to the full SMART DISCOVERY /
        FILE SEARCH workflow.

        NOTE: POST-INDEX QUERY RULE is intentionally always present (in tool_rules)
        because Smart Discovery can trigger indexing mid-conversation even when no
        docs are initially indexed — the model needs this rule from the start.
        """
        # No documents indexed
        assert not agent.rag.indexed_files

        prompt = agent.system_prompt

        # Tier 1 (always present — LLM needs these for registered RAG tools)
        assert "SMART DISCOVERY WORKFLOW" in prompt
        # POST-INDEX QUERY RULE is always present (moved to tool_rules for Smart Discovery)
        assert "POST-INDEX QUERY RULE" in prompt
        # FILE SEARCH AND AUTO-INDEX is only present when enable_filesystem=True

        # Tier 2: present from the start, so indexing never changes the prompt
        assert "FACTUAL ACCURACY RULE" in prompt

    def test_tier2_rag_rules_present_after_indexing(self, agent):
        """Tier 2 query rules appear in prompt once a document is indexed."""
        test_dir = Path(__file__).parent / "test_data"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "test_tier2_gating.txt"

        try:
            test_file.write_text("Content for tier-2 gating test.")

            mock_lemonade = Mock()
            mock_lemonade.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }

            def mock_load_embedder():
                agent.rag.llm_client = mock_lemonade
                agent.rag.embedder = mock_lemonade
                agent.rag.use_lemonade_embeddings = True

            with (
                patch("gaia.rag.sdk.faiss") as mock_faiss,
                patch.object(agent.rag, "_load_embedder", mock_load_embedder),
            ):
                mock_index = Mock()
                mock_index.ntotal = 1
                mock_faiss.IndexFlatL2.return_value = mock_index
                result = agent.rag.index_document(str(test_file))
                assert result.get("success"), f"Indexing failed: {result.get('error')}"

            agent.rebuild_system_prompt()

            prompt = agent.system_prompt

            # Tier 1 still present
            assert "SMART DISCOVERY WORKFLOW" in prompt

            # Tier 2 now injected because has_indexed=True
            assert "FACTUAL ACCURACY RULE" in prompt
            assert "POST-INDEX QUERY RULE" in prompt
        finally:
            if test_file.exists():
                test_file.unlink()
            if test_dir.exists() and not any(test_dir.iterdir()):
                test_dir.rmdir()


class TestChatAgentSessions:
    """Test session management functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def agent(self, temp_dir):
        """Create Chat Agent instance."""
        # Use resolved paths for configuration
        resolved_temp_dir = str(Path(temp_dir).resolve())
        config = ChatAgentConfig(
            silent_mode=True,
            debug=False,
            max_steps=5,
            allowed_paths=[resolved_temp_dir, str(Path.cwd().resolve())],
        )
        agent = ChatAgent(config)
        yield agent
        agent.stop_watching()

    def test_auto_save_session(self, agent):
        """Test auto-save functionality."""
        # Create a session
        if not agent.current_session:
            agent.current_session = agent.session_manager.create_session()

        session_id = agent.current_session.session_id

        # Trigger auto-save
        agent._auto_save_session()

        # Verify session was saved
        sessions = agent.session_manager.list_sessions()
        assert any(s["session_id"] == session_id for s in sessions)

    def test_load_session(self, agent):
        """Test loading a session."""
        # Create and save a session
        if not agent.current_session:
            agent.current_session = agent.session_manager.create_session()

        session_id = agent.current_session.session_id
        agent.save_current_session()

        # Create a new agent and load the session
        new_agent = ChatAgent(ChatAgentConfig(silent_mode=True))
        success = new_agent.load_session(session_id)

        assert success
        assert new_agent.current_session.session_id == session_id

        new_agent.stop_watching()

    def test_chat_history_persistence(self, agent):
        """Test that chat history is persisted and restored across sessions."""
        # Create session and add conversation history
        if not agent.current_session:
            agent.current_session = agent.session_manager.create_session()

        # Simulate a conversation by adding messages directly
        agent.conversation_history.append(
            {"role": "user", "content": "My name is Alice"}
        )
        agent.conversation_history.append(
            {"role": "assistant", "content": "Nice to meet you, Alice!"}
        )
        agent.conversation_history.append({"role": "user", "content": "What is 2+2?"})
        agent.conversation_history.append(
            {"role": "assistant", "content": "The answer is 4."}
        )

        session_id = agent.current_session.session_id
        agent.save_current_session()

        # Verify chat_history was saved to session
        assert len(agent.current_session.chat_history) == 4

        # Create a new agent and load the session
        new_agent = ChatAgent(ChatAgentConfig(silent_mode=True))
        success = new_agent.load_session(session_id)

        assert success
        # Verify conversation_history was restored
        assert len(new_agent.conversation_history) == 4
        assert new_agent.conversation_history[0] == {
            "role": "user",
            "content": "My name is Alice",
        }
        assert new_agent.conversation_history[1] == {
            "role": "assistant",
            "content": "Nice to meet you, Alice!",
        }

        new_agent.stop_watching()

    def test_chat_history_restored_after_reload(self, agent):
        """Verify chat history is restored and available after session reload.

        This test verifies the critical bug fix: chat history persistence.
        The conversation_history is used by process_query() to prepopulate
        the messages array sent to the LLM (see base/agent.py:986-991).
        """
        # Create session with history
        if not agent.current_session:
            agent.current_session = agent.session_manager.create_session()

        # Add conversation history
        agent.conversation_history = [
            {"role": "user", "content": "My name is Alice"},
            {"role": "assistant", "content": "Nice to meet you, Alice!"},
            {"role": "user", "content": "Remember my favorite color is blue"},
            {"role": "assistant", "content": "Got it! Your favorite color is blue."},
        ]
        session_id = agent.current_session.session_id
        agent.save_current_session()

        # Verify session file has the history
        session_from_disk = agent.session_manager.load_session(session_id)
        assert len(session_from_disk.chat_history) == 4

        # Load in new agent instance (simulating restart)
        new_agent = ChatAgent(ChatAgentConfig(silent_mode=True, max_steps=1))
        new_agent.load_session(session_id)

        # Verify conversation history was restored
        assert len(new_agent.conversation_history) == 4
        assert new_agent.conversation_history[0] == {
            "role": "user",
            "content": "My name is Alice",
        }
        assert new_agent.conversation_history[1] == {
            "role": "assistant",
            "content": "Nice to meet you, Alice!",
        }
        assert new_agent.conversation_history[2] == {
            "role": "user",
            "content": "Remember my favorite color is blue",
        }
        assert new_agent.conversation_history[3] == {
            "role": "assistant",
            "content": "Got it! Your favorite color is blue.",
        }

        new_agent.stop_watching()


class TestChatAgentPathValidation:
    """Test path validation and security."""

    @pytest.fixture
    def agent(self):
        """Create Chat Agent instance with restricted paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved_tmpdir = str(Path(tmpdir).resolve())
            agent = ChatAgent(
                ChatAgentConfig(
                    silent_mode=True, debug=False, allowed_paths=[resolved_tmpdir]
                )
            )
            yield agent, resolved_tmpdir
            agent.stop_watching()

    def test_allowed_path(self, agent):
        """Test that allowed paths work."""
        chat_agent, temp_dir = agent
        assert chat_agent._is_path_allowed(temp_dir)

    def test_disallowed_path(self, agent):
        """Test that disallowed paths are rejected."""
        chat_agent, temp_dir = agent
        # Test a path outside the allowed directory
        disallowed = "/tmp/not_allowed"
        assert not chat_agent._is_path_allowed(disallowed)


class TestChatAgentHostAttributeContract:
    """Regression tests for issue #3316.

    ChatAgent is the reference host for ``RAGToolsMixin``/``FileIOToolsMixin``
    — it already binds every required attribute (``self.rag``,
    ``self.path_validator``, ...) before ``super().__init__()`` runs, so the
    host-attribute contract fix must leave it completely unaffected. Lemonade
    and the chat client are mocked so this exercises a real ``ChatAgent``
    without needing a live model server.
    """

    @pytest.fixture(autouse=True)
    def _restore_tool_registry(self):
        """Constructing a real agent rebinds the process-global tool registry;
        leaving it rebound leaks these agents' tools into the rest of a
        full-suite run."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        saved = dict(_TOOL_REGISTRY)
        yield
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    def _construct(self, cls, config):
        with (
            patch(
                "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
                return_value=True,
            ),
            patch("gaia.agents.base.agent.AgentSDK"),
        ):
            return cls(config)

    def test_chat_agent_constructs_and_rag_tool_runs(self, tmp_path):
        """AC4: ChatAgent constructs and its rag tools run without raising."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        config = ChatAgentConfig(
            silent_mode=True, max_steps=1, allowed_paths=[str(tmp_path)]
        )
        agent = self._construct(ChatAgent, config)
        try:
            result = _TOOL_REGISTRY["rag_status"]["function"]()
            assert result["status"] == "success"

            write_result = _TOOL_REGISTRY["write_file"]["function"](
                file_path=str(tmp_path / "ok.txt"), content="hello"
            )
            assert write_result["status"] == "success"
        finally:
            agent.stop_watching()

    def test_chat_agent_lite_constructs_and_tools_run(self):
        """AC4: ChatAgentLite (no rag/file_io mixins — a collateral-damage
        check, not a direct target of this fix) constructs and registers
        its tools without raising. It hardcodes skip_lemonade=True itself."""
        from gaia_agent_chat.lite_agent import ChatAgentLite, ChatAgentLiteConfig

        from gaia.agents.base.tools import _TOOL_REGISTRY

        ChatAgentLite(ChatAgentLiteConfig())
        assert "take_screenshot" in _TOOL_REGISTRY

    def test_chat_agent_rag_stays_lazily_unset_at_construction(self, tmp_path):
        """AC5: constructing ChatAgent must not trigger the lazy self.rag
        property — the read-time check added by #3316 must not probe
        speculatively (hasattr/getattr-with-default) at any point outside an
        actual tool call."""
        from gaia_agent_chat.agent import _UNSET

        config = ChatAgentConfig(
            silent_mode=True, max_steps=1, allowed_paths=[str(tmp_path)]
        )
        agent = self._construct(ChatAgent, config)
        try:
            assert agent._rag is _UNSET
        finally:
            agent.stop_watching()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
