# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Test fixtures and factories for GAIA agent testing."""

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Type, TypeVar

from gaia.testing.mocks import MockLLMProvider, MockVLMClient

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


def create_test_agent(
    agent_class: Type[AgentT],
    mock_responses: Optional[List[str]] = None,
    mock_vlm_text: Optional[str] = None,
    inject_mocks: bool = True,
    **agent_kwargs,
) -> AgentT:
    """
    Create an agent instance configured for testing.

    Creates an agent with silent mode enabled and optionally injects
    mock LLM and VLM providers for testing without real API calls.

    Args:
        agent_class: The Agent class to instantiate
        mock_responses: List of LLM responses to return in sequence
        mock_vlm_text: Text for mock VLM to return
        inject_mocks: Whether to inject mock providers (default: True)
        **agent_kwargs: Additional arguments passed to agent constructor

    Returns:
        Configured agent instance

    Example:
        from gaia.testing import create_test_agent
        from my_agent import CustomerAgent

        def test_customer_search():
            agent = create_test_agent(
                CustomerAgent,
                mock_responses=[
                    '{"tool": "search", "args": {"name": "John"}}',
                    "Found 1 customer named John."
                ],
                db_url="sqlite:///:memory:",
            )

            result = agent.process_query("Find customer John")
            assert "John" in result["answer"]

    Note:
        The agent is created with `skip_lemonade=True` and `silent_mode=True`
        to avoid requiring a running Lemonade server during tests.
    """
    # Ensure testing-friendly defaults
    agent_kwargs.setdefault("skip_lemonade", True)
    agent_kwargs.setdefault("silent_mode", True)

    # Create the agent
    agent = agent_class(**agent_kwargs)

    # Inject mocks if requested
    if inject_mocks:
        # Create and attach mock LLM
        mock_llm = MockLLMProvider(responses=mock_responses)
        agent._mock_llm = mock_llm  # pylint: disable=protected-access

        # Inject into chat attribute if it exists
        if hasattr(agent, "chat"):
            agent.chat = mock_llm

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
                assert "John" in result["answer"]
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
        self.mock_llm: Optional[MockLLMProvider] = None
        self.mock_vlm: Optional[MockVLMClient] = None
        self._temp_dir: Optional[Path] = None
        self._temp_dir_context = None

    def __enter__(self) -> "AgentTestContext":
        """Enter context and set up test environment."""
        # Create temporary directory
        self._temp_dir_context = tempfile.TemporaryDirectory()
        self._temp_dir = Path(self._temp_dir_context.name)

        # Create mock providers
        self.mock_llm = MockLLMProvider(responses=self.mock_responses)
        if self.mock_vlm_text is not None:
            self.mock_vlm = MockVLMClient(extracted_text=self.mock_vlm_text)

        # Create agent with mocks
        self.agent_kwargs.setdefault("skip_lemonade", True)
        self.agent_kwargs.setdefault("silent_mode", True)

        self.agent = self.agent_class(**self.agent_kwargs)

        # Inject mocks
        self.agent._mock_llm = self.mock_llm  # pylint: disable=protected-access
        if hasattr(self.agent, "chat"):
            self.agent.chat = self.mock_llm

        if self.mock_vlm is not None:
            self.agent._mock_vlm = self.mock_vlm  # pylint: disable=protected-access
            if hasattr(self.agent, "vlm"):
                self.agent.vlm = self.mock_vlm

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context and clean up."""
        # Clean up agent if it has cleanup method
        if self.agent is not None:
            if hasattr(self.agent, "close"):
                try:
                    self.agent.close()
                except Exception:
                    pass
            if hasattr(self.agent, "close_db"):
                try:
                    self.agent.close_db()
                except Exception:
                    pass
            if hasattr(self.agent, "stop_all_watchers"):
                try:
                    self.agent.stop_all_watchers()
                except Exception:
                    pass

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
