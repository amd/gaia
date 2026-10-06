# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent document indexing, retrieval and tool surface — no live server.

The RAG SDK's two external boundaries are replaced: the Lemonade client that
loads the embedder and computes embeddings (an autospec of the real class, so
every call is checked against the real signature), and the chat SDK that turns
retrieved chunks into an answer. Chunking, FAISS indexing and retrieval all run
for real.
"""

import hashlib
import platform
import re
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import numpy as np
import pytest
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

from gaia.agents.tools.rag_tools import SUMMARY_MAX_TOKENS
from gaia.llm.lemonade_client import MODELS, LemonadeClient

EMBED_DIM = 64


def _embed(text: str) -> list:
    """Bag-of-words hash embedding: texts sharing words land close together."""
    vec = np.zeros(EMBED_DIM, dtype=np.float32)
    for word in re.findall(r"\w+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBED_DIM] += 1.0
    if not vec.any():
        vec[0] = 1.0
    return (vec / np.linalg.norm(vec)).tolist()


def _fake_embeddings(input_texts, model=None, timeout=None):
    texts = [input_texts] if isinstance(input_texts, str) else input_texts
    return {"data": [{"index": i, "embedding": _embed(t)} for i, t in enumerate(texts)]}


@pytest.fixture(autouse=True)
def _restore_tool_registry():
    """A real agent rebinds the process-global tool registry; undo it."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def lemonade():
    client = create_autospec(LemonadeClient, instance=True)
    client.ensure_model_downloaded.return_value = True
    client.load_model.return_value = {"status": "success"}
    client.unload_model.return_value = {"status": "success"}
    client.embeddings.side_effect = _fake_embeddings
    return client


@pytest.fixture
def workdir(tmp_path):
    # The agent compares realpaths, and macOS tmp dirs sit behind a symlink.
    work = (tmp_path / "work").resolve()
    work.mkdir()
    return work


@pytest.fixture
def agent(tmp_path, workdir, lemonade, monkeypatch):
    # Memory probes the embedder at construction; it is not under test here.
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    config = ChatAgentConfig(
        silent_mode=True, debug=False, max_steps=5, allowed_paths=[str(workdir)]
    )
    with (
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch("gaia.agents.base.agent.AgentSDK"),
        patch("gaia.rag.sdk.AgentSDK"),
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=lemonade),
    ):
        chat_agent = ChatAgent(config)
        chat_agent._rag_config.cache_dir = str(tmp_path / "rag_cache")
        rag = chat_agent.rag
        assert rag is not None, "RAG SDK failed to build — is faiss installed?"
        rag.chat = MagicMock()
        rag.chat.send.return_value = MagicMock(text="mocked answer", stats={})
        yield chat_agent
        chat_agent.stop_watching()


def _index(agent, path) -> None:
    result = agent.rag.index_document(str(Path(path).resolve()))
    assert result["success"], f"Indexing {path} failed: {result.get('error')}"


def _llm_prompt(agent) -> str:
    return agent.rag.chat.send.call_args.args[0]


# ── Indexing ─────────────────────────────────────────────────────────────


SINGLE_FILES = {
    "text": (
        "test.txt",
        "This is a test document about machine learning.\n"
        "It contains information about AI.",
    ),
    "markdown": (
        "test.md",
        "# Test Document\n\nThis is a markdown file about neural networks.",
    ),
    "csv": ("test.csv", "Name,Age,City\nAlice,30,NYC\nBob,25,LA"),
    "json": ("test.json", '{"name": "Test", "description": "A test JSON document"}'),
    "python": (
        "test.py",
        "def authenticate(username, password):\n"
        '    """Authenticate user with credentials."""\n'
        "    return check_database(username, password)\n\n"
        "class UserAuth:\n"
        "    def __init__(self, db_connection):\n"
        "        self.db = db_connection\n",
    ),
    "javascript": (
        "test.js",
        "function fetchUserData(userId) {\n"
        "    return fetch(`/api/users/${userId}`).then(r => r.json());\n}\n\n"
        "class DataManager {\n    constructor() { this.cache = new Map(); }\n}\n",
    ),
}


@pytest.mark.parametrize("kind", list(SINGLE_FILES))
def test_index_document_by_type(agent, workdir, kind):
    name, content = SINGLE_FILES[kind]
    path = workdir / name
    path.write_text(content)

    _index(agent, path)

    assert str(path) in agent.rag.indexed_files
    assert agent.rag.chunks, "indexing produced no chunks"


def test_embedder_load_and_embed_requests_match_lemonade_contract(
    agent, workdir, lemonade
):
    """The embedder is registered with its checkpoint/recipe (a ``user.``
    model is not a Lemonade built-in), loaded scoped, and embedded by name."""
    path = workdir / "doc.txt"
    path.write_text("Lemonade serves the embedding model for retrieval.")

    _index(agent, path)

    model = agent.rag.config.embedding_model
    record = next((m for m in MODELS.values() if m.model_id == model), None)
    if record and model.startswith("user."):
        kwargs = lemonade.ensure_model_downloaded.call_args.kwargs
        assert lemonade.ensure_model_downloaded.call_args.args[0] == model
        assert kwargs["checkpoint"] == record.checkpoint
        assert kwargs["recipe"] == record.recipe
        assert kwargs["embedding"] == record.embedding
    lemonade.unload_model.assert_called_once_with(model, ignore_if_not_loaded=True)
    lemonade.load_model.assert_called_once_with(
        model, llamacpp_args="--ubatch-size 2048"
    )
    texts = lemonade.embeddings.call_args.args[0]
    assert isinstance(texts, list) and all(isinstance(t, str) for t in texts)
    assert lemonade.embeddings.call_args.kwargs["model"] == model


# ── Retrieval ────────────────────────────────────────────────────────────


def test_query_after_indexing_sends_retrieved_context_to_llm(agent, workdir):
    path = workdir / "ml.txt"
    path.write_text("This document is about machine learning and neural networks.")
    _index(agent, path)

    response = agent.rag.query("What is this document about?")

    assert response.text == "mocked answer"
    assert any("machine learning" in c for c in response.chunks)
    prompt = _llm_prompt(agent)
    assert "machine learning" in prompt
    assert "What is this document about?" in prompt


def test_query_code_file_retrieves_definition(agent, workdir):
    path = workdir / "auth.py"
    path.write_text(SINGLE_FILES["python"][1])
    _index(agent, path)

    response = agent.rag.query("Where is UserAuth defined?")

    assert any("class UserAuth" in c for c in response.chunks)
    assert "class UserAuth" in _llm_prompt(agent)
    assert response.query_metadata["source_files"] == ["auth.py"]


# ── Code and web projects ────────────────────────────────────────────────


@pytest.fixture
def sample_codebase(workdir):
    src = workdir / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "class UserAuth:\n"
        "    def authenticate(self, username, password):\n"
        "        # TODO: Add rate limiting\n"
        "        return self.check_credentials(username, password)\n"
    )
    (src / "api.js").write_text(
        "class APIClient {\n"
        "    async fetchUser(userId) {\n"
        "        // TODO: Add error handling\n"
        "        return fetch(`${this.baseUrl}/users/${userId}`);\n"
        "    }\n}\n"
    )
    (src / "config.yaml").write_text(
        "database:\n  host: localhost\n  port: 5432\n  name: myapp\n"
    )
    return src


@pytest.fixture
def sample_web_project(workdir):
    web = workdir / "web"
    web.mkdir()
    (web / "index.html").write_text(
        "<html><body><div class='container'><h1>Welcome</h1></div></body></html>"
    )
    (web / "styles.css").write_text(
        ".container { max-width: 1200px; }\n"
        ".button:hover { background-color: #0056b3; transform: scale(1.05); }\n"
    )
    (web / "UserCard.vue").write_text(
        "<template><div class='user-card'><h2>{{ user.name }}</h2></div></template>\n"
        "<script>\nexport default { props: ['user'] }\n</script>\n"
    )
    (web / "Button.jsx").write_text(
        "export function Button({ onClick, children }) {\n"
        "  return <button className='btn' onClick={onClick}>{children}</button>;\n}\n"
    )
    (web / "variables.scss").write_text(
        "$primary-color: #007bff;\n.btn-primary { background-color: $primary-color; }\n"
    )
    return web


def test_index_multiple_code_files(agent, sample_codebase):
    for path in sample_codebase.iterdir():
        _index(agent, path)

    assert agent.rag.indexed_files == {str(p) for p in sample_codebase.iterdir()}


def test_query_across_codebase_spans_files(agent, sample_codebase):
    for path in sample_codebase.iterdir():
        _index(agent, path)

    response = agent.rag.query("Find TODO comments")

    sources = set(response.query_metadata["source_files"])
    assert {"auth.py", "api.js"} <= sources
    prompt = _llm_prompt(agent)
    assert "TODO: Add rate limiting" in prompt
    assert "TODO: Add error handling" in prompt


def test_index_web_files(agent, sample_web_project):
    for path in sample_web_project.iterdir():
        _index(agent, path)

    assert len(agent.rag.indexed_files) == 5


@pytest.mark.parametrize(
    "filename,question,expected",
    [
        ("styles.css", "Find CSS classes with hover effects", ".button:hover"),
        ("UserCard.vue", "What props does this component use?", "props: ['user']"),
        ("Button.jsx", "What does the Button component do?", "export function Button"),
        ("variables.scss", "What is the primary color?", "$primary-color: #007bff"),
    ],
    ids=["css", "vue", "react", "scss"],
)
def test_query_web_file(agent, sample_web_project, filename, question, expected):
    _index(agent, sample_web_project / filename)

    response = agent.rag.query(question)

    assert any(expected in c for c in response.chunks)
    assert expected in _llm_prompt(agent)


def test_query_across_web_project_finds_buttons(agent, sample_web_project):
    for path in sample_web_project.iterdir():
        _index(agent, path)

    response = agent.rag.query("Find all references to button")

    sources = set(response.query_metadata["source_files"])
    assert "Button.jsx" in sources
    assert len(sources) > 1


def test_file_type_detection(agent):
    for ext in (".py", ".js", ".ts", ".java", ".go", ".rs", ".css", ".html"):
        assert agent.rag._get_file_type(f"test{ext}") == ext
    for ext in (".vue", ".jsx", ".scss"):
        assert agent.rag._get_file_type(f"Test{ext.upper()}") == ext
    assert agent.rag._get_file_type("Makefile") == ".unknown"


# ── Summarization ────────────────────────────────────────────────────────


def test_summarize_document_sends_full_text_to_llm(agent, workdir):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    path = workdir / "small.txt"
    path.write_text("This is a short document about testing release pipelines.")
    _index(agent, path)
    agent.rag.chat.send.return_value = MagicMock(text="A summary.", stats={})

    result = _TOOL_REGISTRY["summarize_document"]["function"](file_path=str(path))

    assert result["status"] == "success", result
    assert result["summary"] == "A summary."
    prompt = _llm_prompt(agent)
    assert "testing release pipelines" in prompt
    assert agent.rag.chat.send.call_args.kwargs == {
        "no_history": True,
        "max_tokens": SUMMARY_MAX_TOKENS,
    }


# ── Search keys and tool surface ─────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "What is machine learning?",
        "How to train a neural network?",
        "When was AI invented?",
    ],
)
def test_search_keys_keep_query_and_add_keywords(agent, query):
    keys = agent._generate_search_keys(query)

    assert keys[0] == query
    assert len(keys) > 1
    assert all(k.strip() for k in keys)


def test_rag_tools_registered(agent):
    names = {t["name"] for t in agent.get_tools()}
    expected = {
        "query_documents",
        "query_specific_file",
        "search_file_content",
        "evaluate_retrieval",
        "index_document",
        "list_indexed_documents",
        "rag_status",
        "summarize_document",
    }
    assert expected <= names, f"missing: {expected - names}"


def test_file_and_shell_tools_registered(agent):
    names = {t["name"] for t in agent.get_tools()}
    assert "add_watch_directory" in names
    assert "run_shell_command" in names


def test_shell_command_lists_allowed_directory(agent, workdir):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    (workdir / "marker.txt").write_text("x")
    command = "dir" if platform.system() == "Windows" else "ls"

    result = _TOOL_REGISTRY["run_shell_command"]["function"](
        command=command, working_directory=str(workdir)
    )

    assert result["status"] == "success", result
    assert "marker.txt" in result["stdout"], result
