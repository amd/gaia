# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Test fixtures and factories for GAIA agent testing."""

import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Type, TypeVar

from gaia.testing.mocks import MockVLMClient
from gaia.testing.scripted import ScriptedLLMClient

# Type variable for agent classes
AgentT = TypeVar("AgentT")


@contextmanager
def temp_directory():
    """
    Create a temporary directory for testing with automatic cleanup.

    Yields a Path object to the temporary directory. The directory
    and all its contents are automatically deleted when the context exits.

    Example:
        from gaia.testing import temp_directory

        def test_file_processing():
            with temp_directory() as tmp_dir:
                # Create test files
                test_file = tmp_dir / "test.txt"
                test_file.write_text("Hello World")

                # Test your agent
                agent = MyAgent(data_dir=str(tmp_dir))
                result = agent.process_file(str(test_file))

                assert result["status"] == "success"

            # Directory automatically deleted after context exits

    Yields:
        Path: Path to the temporary directory
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@contextmanager
def temp_file(
    content: str = "",
    suffix: str = ".txt",
    prefix: str = "test_",
):
    """
    Create a temporary file for testing with automatic cleanup.

    Args:
        content: Initial content to write to the file
        suffix: File extension (default: .txt)
        prefix: Filename prefix (default: test_)

    Yields:
        Path: Path to the temporary file

    Example:
        from gaia.testing import temp_file

        def test_file_reading():
            with temp_file(content="test data", suffix=".json") as tmp_path:
                result = my_agent.read_file(str(tmp_path))
                assert "test data" in result
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        prefix=prefix,
        delete=False,
    ) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def _inject_model(agent, model: ScriptedLLMClient) -> None:
    """Make *model* the client the agent's loop calls, where it has one."""
    agent._mock_llm = model  # pylint: disable=protected-access
    chat = getattr(agent, "chat", None)
    if chat is not None and hasattr(chat, "llm_client"):
        chat.llm_client = model


def create_test_agent(
    agent_class: Type[AgentT],
    mock_responses: Optional[List[str]] = None,
    mock_vlm_text: Optional[str] = None,
    inject_mocks: bool = True,
    llm_client: Optional[ScriptedLLMClient] = None,
    **agent_kwargs,
) -> AgentT:
    """
    Create an agent instance configured for testing.

    Creates an agent with silent mode enabled and a scripted model in place
    of the real one, so ``process_query`` runs the whole loop with no network.

    Args:
        agent_class: The Agent class to instantiate
        mock_responses: Text replies the model gives, in order
        mock_vlm_text: Text for mock VLM to return
        inject_mocks: Whether to inject the scripted model (default: True)
        llm_client: A ScriptedLLMClient to use instead of one built from
            ``mock_responses``; needed to script tool calls
        **agent_kwargs: Additional arguments passed to agent constructor

    Returns:
        Configured agent instance; the scripted model is ``agent._mock_llm``

    Example:
        from gaia.testing import ScriptedLLMClient, create_test_agent
        from my_agent import CustomerAgent

        def test_customer_search():
            model = ScriptedLLMClient()
            model.push(call="search", name="John")
            model.push(say="Found 1 customer named John.")
            agent = create_test_agent(
                CustomerAgent, llm_client=model, db_url="sqlite:///:memory:"
            )

            result = agent.process_query("Find customer John")
            assert result["status"] == "success"
            assert "John" in result["result"]

    Note:
        The agent is created with `skip_lemonade=True` and `silent_mode=True`
        to avoid requiring a running Lemonade server during tests. A model
        call the script has no reply for raises ``ScriptExhausted``.
    """
    if llm_client is not None and mock_responses is not None:
        raise ValueError(
            "Pass mock_responses or llm_client, not both: queue the replies on "
            "the client with push()."
        )
    # Ensure testing-friendly defaults
    agent_kwargs.setdefault("skip_lemonade", True)
    agent_kwargs.setdefault("silent_mode", True)

    # Create the agent
    agent = agent_class(**agent_kwargs)

    # Inject mocks if requested
    if inject_mocks:
        _inject_model(agent, llm_client or ScriptedLLMClient(mock_responses))

        # Create and attach mock VLM if text provided
        if mock_vlm_text is not None:
            mock_vlm = MockVLMClient(extracted_text=mock_vlm_text)
            agent._mock_vlm = mock_vlm  # pylint: disable=protected-access

            if hasattr(agent, "vlm"):
                agent.vlm = mock_vlm

    return agent


class AgentTestContext:
    """
    Context manager for comprehensive agent testing.

    Provides a clean testing environment with temporary directories,
    mock providers, and automatic cleanup.

    Example:
        from gaia.testing import AgentTestContext
        from my_agent import IntakeAgent

        def test_intake_processing():
            with AgentTestContext(IntakeAgent) as ctx:
                # Create test file
                test_file = ctx.create_file("form.txt", "Patient: John Doe")

                # Process with agent
                result = ctx.agent.process_query(f"Process {test_file}")

                # Verify
                assert ctx.mock_llm.was_called
                assert "John" in result["result"]
    """

    def __init__(
        self,
        agent_class: Type[AgentT],
        mock_responses: Optional[List[str]] = None,
        mock_vlm_text: Optional[str] = None,
        **agent_kwargs,
    ):
        """
        Initialize test context.

        Args:
            agent_class: Agent class to instantiate
            mock_responses: LLM responses to use
            mock_vlm_text: VLM extraction text to use
            **agent_kwargs: Additional agent arguments
        """
        self.agent_class = agent_class
        self.mock_responses = mock_responses
        self.mock_vlm_text = mock_vlm_text
        self.agent_kwargs = agent_kwargs

        self.agent: Optional[AgentT] = None
        self.mock_llm: Optional[ScriptedLLMClient] = None
        self.mock_vlm: Optional[MockVLMClient] = None
        self._temp_dir: Optional[Path] = None
        self._temp_dir_context = None

    def __enter__(self) -> "AgentTestContext":
        """Enter context and set up test environment."""
        # Create temporary directory
        self._temp_dir_context = tempfile.TemporaryDirectory()
        self._temp_dir = Path(self._temp_dir_context.name)

        # Create mock providers
        self.mock_llm = ScriptedLLMClient(self.mock_responses)
        if self.mock_vlm_text is not None:
            self.mock_vlm = MockVLMClient(extracted_text=self.mock_vlm_text)

        # Create agent with mocks
        self.agent_kwargs.setdefault("skip_lemonade", True)
        self.agent_kwargs.setdefault("silent_mode", True)

        self.agent = self.agent_class(**self.agent_kwargs)

        # Inject mocks
        _inject_model(self.agent, self.mock_llm)

        if self.mock_vlm is not None:
            self.agent._mock_vlm = self.mock_vlm  # pylint: disable=protected-access
            if hasattr(self.agent, "vlm"):
                self.agent.vlm = self.mock_vlm

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context and clean up."""
        # Clean up agent if it has cleanup method
        if self.agent is not None:
            for method in ("close", "close_db", "stop_all_watchers"):
                cleanup = getattr(self.agent, method, None)
                if cleanup is None:
                    continue
                try:
                    cleanup()
                except Exception as e:  # pylint: disable=broad-except
                    # Warn, not raise: raising here would hide the test's own failure.
                    warnings.warn(
                        f"AgentTestContext teardown: "
                        f"{type(self.agent).__name__}.{method}() failed: {e!r}",
                        RuntimeWarning,
                        stacklevel=2,
                    )

        # Clean up temp directory
        if self._temp_dir_context is not None:
            self._temp_dir_context.cleanup()

        return False  # Don't suppress exceptions

    @property
    def temp_dir(self) -> Path:
        """Get the temporary directory path."""
        if self._temp_dir is None:
            raise RuntimeError("Context not entered. Use 'with' statement.")
        return self._temp_dir

    def create_file(
        self,
        name: str,
        content: str = "",
        subdir: Optional[str] = None,
    ) -> Path:
        """
        Create a file in the temporary directory.

        Args:
            name: Filename
            content: File content
            subdir: Optional subdirectory to create file in

        Returns:
            Path to created file
        """
        if subdir:
            file_dir = self.temp_dir / subdir
            file_dir.mkdir(parents=True, exist_ok=True)
        else:
            file_dir = self.temp_dir

        file_path = file_dir / name
        file_path.write_text(content)
        return file_path

    def create_directory(self, name: str) -> Path:
        """
        Create a subdirectory in the temporary directory.

        Args:
            name: Directory name

        Returns:
            Path to created directory
        """
        dir_path = self.temp_dir / name
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def set_llm_responses(self, responses: List[str]) -> None:
        """
        Set new LLM responses.

        Args:
            responses: New list of responses
        """
        if self.mock_llm:
            self.mock_llm.set_responses(responses)

    def set_vlm_text(self, text: str) -> None:
        """
        Set new VLM extraction text.

        Args:
            text: New extraction text
        """
        if self.mock_vlm:
            self.mock_vlm.set_extracted_text(text)
