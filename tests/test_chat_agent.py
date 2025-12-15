# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for Chat Agent with RAG capabilities.
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig


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
    def agent(self):
        """Create Chat Agent instance."""
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, debug=False, max_steps=5))
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


class TestChatAgentEval:
    """Evaluation tests for Chat Agent quality metrics."""

    @pytest.fixture
    def agent_with_docs(self, temp_dir):
        """Create agent with test documents."""
        # This would be expanded with actual test documents
        agent = ChatAgent(
            ChatAgentConfig(
                silent_mode=True, debug=False, rag_documents=[], max_steps=10
            )
        )
        yield agent
        agent.stop_watching()

    def test_eval_retrieval_accuracy(self, agent_with_docs):
        """
        Evaluate retrieval accuracy.

        This test should:
        1. Index known documents
        2. Query with known questions
        3. Measure if correct context is retrieved
        """
        # Placeholder for evaluation test
        # Would be expanded with actual documents and ground truth
        agent = agent_with_docs
        assert agent is not None

    def test_eval_answer_quality(self, agent_with_docs):
        """
        Evaluate answer quality against ground truth.

        This test should:
        1. Index documents with known facts
        2. Query for those facts
        3. Compare answers to ground truth
        """
        # Placeholder for evaluation test
        agent = agent_with_docs
        assert agent is not None

    def test_eval_search_key_quality(self, agent_with_docs):
        """
        Evaluate search key generation quality.

        This test should:
        1. Generate search keys for various queries
        2. Measure if generated keys improve retrieval
        """
        agent = agent_with_docs

        # Test queries
        test_queries = [
            "What is machine learning?",
            "How to train a neural network?",
            "When was AI invented?",
        ]

        for query in test_queries:
            keys = agent._generate_search_keys(query)
            # Keys should include original query
            assert query in keys
            # Should generate additional keys
            assert len(keys) > 1
            # Keys should be non-empty
            assert all(len(k) > 0 for k in keys)


class TestChatAgentTools:
    """Test chat agent tools from mixins."""

    @pytest.fixture
    def agent(self):
        """Create Chat Agent instance."""
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, debug=False, max_steps=5))
        yield agent
        agent.stop_watching()

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def sample_txt_file(self, temp_dir):
        """Create a sample text file."""
        txt_path = Path(temp_dir) / "test.txt"
        txt_path.write_text(
            "This is a test document about machine learning.\nIt contains information about AI."
        )
        return str(txt_path)

    @pytest.fixture
    def sample_md_file(self, temp_dir):
        """Create a sample markdown file."""
        md_path = Path(temp_dir) / "test.md"
        md_path.write_text(
            "# Test Document\n\nThis is a markdown file about neural networks."
        )
        return str(md_path)

    @pytest.fixture
    def sample_csv_file(self, temp_dir):
        """Create a sample CSV file."""
        csv_path = Path(temp_dir) / "test.csv"
        csv_path.write_text("Name,Age,City\nAlice,30,NYC\nBob,25,LA")
        return str(csv_path)

    @pytest.fixture
    def sample_json_file(self, temp_dir):
        """Create a sample JSON file."""
        json_path = Path(temp_dir) / "test.json"
        json_path.write_text('{"name": "Test", "description": "A test JSON document"}')
        return str(json_path)

    @pytest.fixture
    def sample_python_file(self, temp_dir):
        """Create a sample Python file."""
        py_path = Path(temp_dir) / "test.py"
        code = '''# Sample Python code
def authenticate(username, password):
    """Authenticate user with credentials."""
    if not username or not password:
        return False
    return check_database(username, password)

class UserAuth:
    """Handle user authentication."""
    def __init__(self, db_connection):
        self.db = db_connection
'''
        py_path.write_text(code)
        return str(py_path)

    @pytest.fixture
    def sample_js_file(self, temp_dir):
        """Create a sample JavaScript file."""
        js_path = Path(temp_dir) / "test.js"
        code = """// Sample JavaScript code
function fetchUserData(userId) {
    return fetch(`/api/users/${userId}`)
        .then(response => response.json());
}

class DataManager {
    constructor() {
        this.cache = new Map();
    }
}
"""
        js_path.write_text(code)
        return str(js_path)

    def test_index_text_file(self, agent, sample_txt_file):
        """Test indexing a text file."""
        success = agent.rag.index_document(sample_txt_file)
        assert success
        assert sample_txt_file in agent.rag.indexed_files

    def test_index_markdown_file(self, agent, sample_md_file):
        """Test indexing a markdown file."""
        success = agent.rag.index_document(sample_md_file)
        assert success
        assert sample_md_file in agent.rag.indexed_files

    def test_index_csv_file(self, agent, sample_csv_file):
        """Test indexing a CSV file."""
        success = agent.rag.index_document(sample_csv_file)
        assert success
        assert sample_csv_file in agent.rag.indexed_files

    def test_index_json_file(self, agent, sample_json_file):
        """Test indexing a JSON file."""
        success = agent.rag.index_document(sample_json_file)
        assert success
        assert sample_json_file in agent.rag.indexed_files

    def test_query_after_indexing(self, agent, sample_txt_file):
        """Test querying after indexing a document."""
        agent.rag.index_document(sample_txt_file)
        response = agent.rag.query("What is this document about?")
        assert response.text
        assert len(response.text) > 0

    def test_index_python_file(self, agent, sample_python_file):
        """Test indexing a Python code file."""
        success = agent.rag.index_document(sample_python_file)
        assert success
        assert sample_python_file in agent.rag.indexed_files

    def test_index_javascript_file(self, agent, sample_js_file):
        """Test indexing a JavaScript file."""
        success = agent.rag.index_document(sample_js_file)
        assert success
        assert sample_js_file in agent.rag.indexed_files

    def test_query_code_file(self, agent, sample_python_file):
        """Test querying code after indexing."""
        agent.rag.index_document(sample_python_file)
        response = agent.rag.query("What does the authenticate function do?")
        assert response.text
        assert len(response.text) > 0
        # The response should contain information about authentication
        # (We can't assert exact content without mocking the LLM)

    def test_search_in_code(self, agent, sample_python_file):
        """Test searching for specific code patterns."""
        agent.rag.index_document(sample_python_file)
        # Search for class definition
        response = agent.rag.query("Where is UserAuth defined?")
        assert response.text
        # Should have retrieved chunks containing UserAuth

    def test_rag_tools_mixin(self, agent):
        """Test RAG tools are registered."""
        # Check that RAG tools exist
        tools = agent.get_tools()
        rag_tool_names = [
            "query_documents",
            "query_specific_file",
            "search_file_content",
            "evaluate_retrieval",
            "index_document",
            "list_indexed_documents",
            "rag_status",
            "summarize_document",
        ]

        for tool_name in rag_tool_names:
            assert any(
                t["name"] == tool_name for t in tools
            ), f"Tool {tool_name} not found"

    def test_file_tools_mixin(self, agent):
        """Test file tools are registered."""
        tools = agent.get_tools()
        assert any(t["name"] == "add_watch_directory" for t in tools)

    def test_shell_tools_mixin(self, agent):
        """Test shell tools are registered."""
        tools = agent.get_tools()
        assert any(t["name"] == "run_shell_command" for t in tools)

    def test_shell_command_ls(self, agent, temp_dir):
        """Test running ls/dir shell command."""
        # This tests the shell tool functionality
        # Note: On Windows, 'dir' is used; on Unix, 'ls' is used
        import platform

        if platform.system() == "Windows":
            cmd = f'dir "{temp_dir}"'
        else:
            cmd = f'ls "{temp_dir}"'

        # We can't easily test this without mocking, but we can test the path validation
        assert agent._is_path_allowed(temp_dir)


class TestChatAgentSummarization:
    """Test document summarization functionality."""

    @pytest.fixture
    def agent(self):
        """Create Chat Agent instance."""
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, debug=False, max_steps=5))
        yield agent
        agent.stop_watching()

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def long_document(self, temp_dir):
        """Create a long document for summarization testing."""
        doc_path = Path(temp_dir) / "long_doc.txt"
        # Create a document with multiple paragraphs
        content = "\n\n".join(
            [
                f"This is paragraph {i}. It contains information about topic {i}."
                for i in range(50)
            ]
        )
        doc_path.write_text(content)
        return str(doc_path)

    def test_summarize_small_document(self, agent, temp_dir):
        """Test summarizing a small document."""
        doc_path = Path(temp_dir) / "small.txt"
        doc_path.write_text("This is a short document about testing.")

        agent.rag.index_document(str(doc_path))

        # Test summarization (would need to mock LLM for real test)
        # For now, just verify the tool exists
        tools = agent.get_tools()
        assert any(t["name"] == "summarize_document" for t in tools)


class TestChatAgentSessions:
    """Test session management functionality."""

    @pytest.fixture
    def agent(self):
        """Create Chat Agent instance."""
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, debug=False, max_steps=5))
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
            agent = ChatAgent(
                ChatAgentConfig(silent_mode=True, debug=False, allowed_paths=[tmpdir])
            )
            yield agent, tmpdir
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


class TestChatAgentCodeSupport:
    """Test code file indexing and retrieval capabilities."""

    @pytest.fixture
    def agent(self):
        """Create Chat Agent instance."""
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, debug=False, max_steps=5))
        yield agent
        agent.stop_watching()

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def sample_codebase(self, temp_dir):
        """Create a sample codebase with multiple files."""
        # Create directory structure
        src_dir = Path(temp_dir) / "src"
        src_dir.mkdir()

        # Python file
        (src_dir / "auth.py").write_text(
            '''
class UserAuth:
    """Authentication handler."""
    def authenticate(self, username, password):
        # TODO: Add rate limiting
        return self.check_credentials(username, password)
'''
        )

        # JavaScript file
        (src_dir / "api.js").write_text(
            """
// API client
class APIClient {
    constructor(baseUrl) {
        this.baseUrl = baseUrl;
    }

    async fetchUser(userId) {
        // TODO: Add error handling
        return fetch(`${this.baseUrl}/users/${userId}`);
    }
}
"""
        )

        # Config file
        (src_dir / "config.yaml").write_text(
            """
database:
  host: localhost
  port: 5432
  name: myapp
"""
        )

        return str(src_dir)

    @pytest.fixture
    def sample_web_project(self, temp_dir):
        """Create a sample web development project."""
        web_dir = Path(temp_dir) / "web"
        web_dir.mkdir()

        # HTML file
        (web_dir / "index.html").write_text(
            """
<!DOCTYPE html>
<html>
<head>
    <title>My App</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <div class="container">
        <h1>Welcome</h1>
    </div>
</body>
</html>
"""
        )

        # CSS file
        (web_dir / "styles.css").write_text(
            """
.container {
    max-width: 1200px;
    margin: 0 auto;
}

.button:hover {
    background-color: #0056b3;
    transform: scale(1.05);
}
"""
        )

        # Vue component
        (web_dir / "UserCard.vue").write_text(
            """
<template>
  <div class="user-card">
    <h2>{{ user.name }}</h2>
    <p>{{ user.email }}</p>
  </div>
</template>

<script>
export default {
  props: ['user'],
  mounted() {
    console.log('User card mounted');
  }
}
</script>
"""
        )

        # React component (JSX)
        (web_dir / "Button.jsx").write_text(
            """
import React from 'react';

export function Button({ onClick, children }) {
  return (
    <button className="btn" onClick={onClick}>
      {children}
    </button>
  );
}
"""
        )

        # SCSS file
        (web_dir / "variables.scss").write_text(
            """
$primary-color: #007bff;
$secondary-color: #6c757d;

.btn-primary {
  background-color: $primary-color;
  &:hover {
    background-color: darken($primary-color, 10%);
  }
}
"""
        )

        return str(web_dir)

    def test_index_multiple_code_files(self, agent, sample_codebase):
        """Test indexing multiple code files."""
        src_dir = Path(sample_codebase)

        # Index all files
        for file in src_dir.glob("*"):
            if file.is_file():
                success = agent.rag.index_document(str(file))
                assert success

        # Should have indexed 3 files
        assert len(agent.rag.indexed_files) == 3

    def test_query_across_codebase(self, agent, sample_codebase):
        """Test querying across multiple code files."""
        src_dir = Path(sample_codebase)

        # Index all files
        for file in src_dir.glob("*"):
            if file.is_file():
                agent.rag.index_document(str(file))

        # Query should search across all indexed files
        response = agent.rag.query("Find TODO comments")
        assert response.text
        # Should retrieve chunks from multiple files with TODOs

    def test_file_type_detection(self, agent):
        """Test that different code file types are correctly detected."""
        file_types = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".java": "Java",
            ".go": "Go",
            ".rs": "Rust",
            ".css": "CSS",
            ".html": "HTML",
            ".vue": "Vue",
            ".jsx": "React JSX",
            ".scss": "SCSS",
        }

        for ext, lang in file_types.items():
            file_type = agent.rag._get_file_type(f"test{ext}")
            assert file_type == ext

    def test_index_web_files(self, agent, sample_web_project):
        """Test indexing web development files."""
        web_dir = Path(sample_web_project)

        # Index all files
        for file in web_dir.glob("*"):
            if file.is_file():
                success = agent.rag.index_document(str(file))
                assert success

        # Should have indexed HTML, CSS, Vue, JSX, SCSS files
        assert len(agent.rag.indexed_files) == 5

    def test_query_css_content(self, agent, sample_web_project):
        """Test querying CSS files."""
        web_dir = Path(sample_web_project)
        css_file = web_dir / "styles.css"

        agent.rag.index_document(str(css_file))
        response = agent.rag.query("Find CSS classes with hover effects")
        assert response.text
        # Should find .button:hover

    def test_query_vue_components(self, agent, sample_web_project):
        """Test querying Vue component files."""
        web_dir = Path(sample_web_project)
        vue_file = web_dir / "UserCard.vue"

        agent.rag.index_document(str(vue_file))
        response = agent.rag.query("What props does this component use?")
        assert response.text
        # Should mention 'user' prop

    def test_query_react_components(self, agent, sample_web_project):
        """Test querying React JSX files."""
        web_dir = Path(sample_web_project)
        jsx_file = web_dir / "Button.jsx"

        agent.rag.index_document(str(jsx_file))
        response = agent.rag.query("What does the Button component do?")
        assert response.text
        # Should describe the button component

    def test_query_scss_variables(self, agent, sample_web_project):
        """Test querying SCSS files with variables."""
        web_dir = Path(sample_web_project)
        scss_file = web_dir / "variables.scss"

        agent.rag.index_document(str(scss_file))
        response = agent.rag.query("What is the primary color?")
        assert response.text
        # Should find $primary-color definition

    def test_query_across_web_project(self, agent, sample_web_project):
        """Test querying across multiple web files."""
        web_dir = Path(sample_web_project)

        # Index all web files
        for file in web_dir.glob("*"):
            if file.is_file():
                agent.rag.index_document(str(file))

        # Query should search across all files
        response = agent.rag.query("Find all references to buttons")
        assert response.text
        # Should find button in CSS, JSX, etc.


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
