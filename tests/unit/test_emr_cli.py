# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for EMR CLI commands.

Tests cover:
- Argument parsing for all commands
- Command execution with mocked dependencies
- Error handling scenarios
- Console output validation
"""

import argparse
import sys
import unittest
from unittest.mock import MagicMock, patch

from gaia.testing import temp_directory


class TestCLIArgumentParsing(unittest.TestCase):
    """Test CLI argument parsing for all commands."""

    def setUp(self):
        """Set up argument parser."""
        # We'll test by importing the module and checking parser behavior
        self.original_argv = sys.argv.copy()

    def tearDown(self):
        """Restore original argv."""
        sys.argv = self.original_argv

    def test_no_command_shows_help(self):
        """Test that running without a command shows help."""
        from gaia.agents.emr.cli import main

        sys.argv = ["gaia-emr"]
        # Should return 0 and print help
        result = main()
        self.assertEqual(result, 0)

    def test_watch_command_parses_defaults(self):
        """Test watch command parses with default arguments."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        _add_common_args(parser)
        args = parser.parse_args([])

        self.assertEqual(args.watch_dir, "./intake_forms")
        self.assertEqual(args.db, "./data/patients.db")
        self.assertEqual(args.vlm_model, "Qwen3-VL-4B-Instruct-GGUF")
        self.assertFalse(args.debug)

    def test_watch_command_parses_custom_args(self):
        """Test watch command parses custom arguments."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        _add_common_args(parser)
        args = parser.parse_args(
            [
                "--watch-dir",
                "/custom/watch",
                "--db",
                "/custom/db.sqlite",
                "--vlm-model",
                "custom-model",
                "--debug",
            ]
        )

        self.assertEqual(args.watch_dir, "/custom/watch")
        self.assertEqual(args.db, "/custom/db.sqlite")
        self.assertEqual(args.vlm_model, "custom-model")
        self.assertTrue(args.debug)

    def test_process_command_requires_file(self):
        """Test process command requires file argument."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        parser_process = subparsers.add_parser("process")
        _add_common_args(parser_process)
        parser_process.add_argument("file")

        # Should work with file
        args = parser.parse_args(["process", "test.jpg"])
        self.assertEqual(args.file, "test.jpg")

        # Should fail without file
        with self.assertRaises(SystemExit):
            parser.parse_args(["process"])

    def test_query_command_requires_question(self):
        """Test query command requires question argument."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        parser_query = subparsers.add_parser("query")
        _add_common_args(parser_query)
        parser_query.add_argument("question")

        # Should work with question
        args = parser.parse_args(["query", "How many patients?"])
        self.assertEqual(args.question, "How many patients?")

    def test_reset_command_has_force_flag(self):
        """Test reset command has force flag option."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        parser_reset = subparsers.add_parser("reset")
        _add_common_args(parser_reset)
        parser_reset.add_argument("--force", "-f", action="store_true")

        # Without force
        args = parser.parse_args(["reset"])
        self.assertFalse(args.force)

        # With force
        args = parser.parse_args(["reset", "--force"])
        self.assertTrue(args.force)

        # With short flag
        args = parser.parse_args(["reset", "-f"])
        self.assertTrue(args.force)

    def test_dashboard_command_parses_port(self):
        """Test dashboard command parses host and port."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        parser_dashboard = subparsers.add_parser("dashboard")
        _add_common_args(parser_dashboard)
        parser_dashboard.add_argument("--host", default="127.0.0.1")
        parser_dashboard.add_argument("--port", type=int, default=8080)
        parser_dashboard.add_argument("--no-open", action="store_true")
        parser_dashboard.add_argument("--browser", action="store_true")

        # Default values
        args = parser.parse_args(["dashboard"])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8080)
        self.assertFalse(args.no_open)
        self.assertFalse(args.browser)

        # Custom values
        args = parser.parse_args(
            [
                "dashboard",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--no-open",
                "--browser",
            ]
        )
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9000)
        self.assertTrue(args.no_open)
        self.assertTrue(args.browser)

    def test_test_command_parses_image_options(self):
        """Test test command parses image optimization options."""
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        parser_test = subparsers.add_parser("test")
        parser_test.add_argument("file")
        parser_test.add_argument("--vlm-model", default="Qwen3-VL-4B-Instruct-GGUF")
        parser_test.add_argument("--max-dimension", type=int, default=1024)
        parser_test.add_argument("--jpeg-quality", type=int, default=85)
        parser_test.add_argument("--clear-context", action="store_true")

        # Default values
        args = parser.parse_args(["test", "image.jpg"])
        self.assertEqual(args.file, "image.jpg")
        self.assertEqual(args.max_dimension, 1024)
        self.assertEqual(args.jpeg_quality, 85)
        self.assertFalse(args.clear_context)

        # Custom values
        args = parser.parse_args(
            [
                "test",
                "image.jpg",
                "--max-dimension",
                "2048",
                "--jpeg-quality",
                "90",
                "--clear-context",
            ]
        )
        self.assertEqual(args.max_dimension, 2048)
        self.assertEqual(args.jpeg_quality, 90)
        self.assertTrue(args.clear_context)


class TestCmdProcess(unittest.TestCase):
    """Test cmd_process command."""

    def test_process_file_not_found(self):
        """Test process command returns error for missing file."""
        from gaia.agents.emr.cli import cmd_process

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                file=str(tmp_dir / "nonexistent.jpg"),
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
            )

            result = cmd_process(args)
            self.assertEqual(result, 1)

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    def test_process_success(self, mock_agent_class):
        """Test successful file processing."""
        from gaia.agents.emr.cli import cmd_process

        # Setup mock agent
        mock_agent = MagicMock()
        mock_agent._process_intake_form.return_value = {
            "id": 1,
            "first_name": "John",
            "last_name": "Doe",
        }
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            # Create a test file
            test_file = tmp_dir / "test.jpg"
            test_file.write_bytes(b"fake image data")

            args = argparse.Namespace(
                file=str(test_file),
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
            )

            result = cmd_process(args)
            self.assertEqual(result, 0)
            mock_agent.stop.assert_called_once()

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    def test_process_failure(self, mock_agent_class):
        """Test failed file processing."""
        from gaia.agents.emr.cli import cmd_process

        # Setup mock agent that fails
        mock_agent = MagicMock()
        mock_agent._process_intake_form.return_value = None
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            # Create a test file
            test_file = tmp_dir / "test.jpg"
            test_file.write_bytes(b"fake image data")

            args = argparse.Namespace(
                file=str(test_file),
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
            )

            result = cmd_process(args)
            self.assertEqual(result, 1)
            mock_agent.stop.assert_called_once()


class TestCmdStats(unittest.TestCase):
    """Test cmd_stats command."""

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    def test_stats_displays_correctly(self, mock_agent_class):
        """Test stats command displays statistics."""
        from gaia.agents.emr.cli import cmd_stats

        # Setup mock agent
        mock_agent = MagicMock()
        mock_agent.get_stats.return_value = {
            "total_patients": 10,
            "new_patients": 7,
            "returning_patients": 3,
            "processed_today": 2,
            "files_processed": 15,
            "extraction_success": 14,
            "extraction_failed": 1,
            "success_rate": "93.3%",
            "time_saved_minutes": 45,
            "time_saved_percent": "85%",
            "unacknowledged_alerts": 0,
        }
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            result = cmd_stats(args)
            self.assertEqual(result, 0)
            mock_agent.get_stats.assert_called_once()
            mock_agent.stop.assert_called_once()


class TestCmdQuery(unittest.TestCase):
    """Test cmd_query command."""

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    def test_query_executes(self, mock_agent_class):
        """Test query command processes question."""
        from gaia.agents.emr.cli import cmd_query

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                question="How many patients were processed today?",
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            result = cmd_query(args)
            self.assertEqual(result, 0)
            mock_agent.process_query.assert_called_once_with(
                "How many patients were processed today?"
            )
            mock_agent.stop.assert_called_once()


class TestCmdReset(unittest.TestCase):
    """Test cmd_reset command."""

    def test_reset_no_database(self):
        """Test reset when database doesn't exist."""
        from gaia.agents.emr.cli import cmd_reset

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                db=str(tmp_dir / "nonexistent.db"),
                watch_dir=str(tmp_dir / "intake"),
                vlm_model="test-model",
                force=False,
            )

            result = cmd_reset(args)
            self.assertEqual(result, 0)

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    def test_reset_with_force(self, mock_agent_class):
        """Test reset with force flag skips confirmation."""
        from gaia.agents.emr.cli import cmd_reset

        mock_agent = MagicMock()
        mock_agent.get_stats.return_value = {"total_patients": 5}
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            # Create a fake database file
            db_path = tmp_dir / "patients.db"
            db_path.write_bytes(b"fake database")

            args = argparse.Namespace(
                db=str(db_path),
                watch_dir=str(tmp_dir / "intake"),
                vlm_model="test-model",
                force=True,
            )

            result = cmd_reset(args)
            self.assertEqual(result, 0)
            self.assertFalse(db_path.exists())

    @patch("rich.prompt.Confirm.ask", return_value=False)
    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    def test_reset_cancelled(self, mock_agent_class, mock_confirm_ask):
        """Test reset cancelled by user."""
        from gaia.agents.emr.cli import cmd_reset

        mock_agent = MagicMock()
        mock_agent.get_stats.return_value = {"total_patients": 5}
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            # Create a fake database file
            db_path = tmp_dir / "patients.db"
            db_path.write_bytes(b"fake database")

            args = argparse.Namespace(
                db=str(db_path),
                watch_dir=str(tmp_dir / "intake"),
                vlm_model="test-model",
                force=False,
            )

            result = cmd_reset(args)
            self.assertEqual(result, 0)
            self.assertTrue(db_path.exists())  # File should still exist


class TestCmdTest(unittest.TestCase):
    """Test cmd_test command (VLM pipeline test)."""

    def test_test_file_not_found(self):
        """Test test command returns error for missing file."""
        from gaia.agents.emr.cli import cmd_test

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                file=str(tmp_dir / "nonexistent.jpg"),
                vlm_model="test-model",
                max_dimension=1024,
                jpeg_quality=85,
                clear_context=False,
                debug=False,
            )

            result = cmd_test(args)
            self.assertEqual(result, 1)


class TestPrintStatsTable(unittest.TestCase):
    """Test _print_stats_table helper."""

    def test_print_stats_with_alerts(self):
        """Test stats table prints alert count when present."""
        from gaia.agents.emr.cli import _print_stats_table

        stats = {
            "total_patients": 10,
            "new_patients": 7,
            "returning_patients": 3,
            "processed_today": 2,
            "files_processed": 15,
            "extraction_success": 14,
            "extraction_failed": 1,
            "success_rate": "93.3%",
            "time_saved_minutes": 45,
            "time_saved_percent": "85%",
            "unacknowledged_alerts": 3,
        }

        # Should not raise - just verify it runs without error
        _print_stats_table(stats)

    def test_print_stats_without_alerts(self):
        """Test stats table when no alerts present."""
        from gaia.agents.emr.cli import _print_stats_table

        stats = {
            "total_patients": 0,
            "new_patients": 0,
            "returning_patients": 0,
            "processed_today": 0,
            "files_processed": 0,
            "extraction_success": 0,
            "extraction_failed": 0,
            "success_rate": "N/A",
            "time_saved_minutes": 0,
            "time_saved_percent": "0%",
            "unacknowledged_alerts": 0,
        }

        # Should not raise
        _print_stats_table(stats)


class TestPrintHeader(unittest.TestCase):
    """Test _print_header helper."""

    def test_print_header_displays(self):
        """Test header displays without error."""
        from gaia.agents.emr.cli import _print_header

        # Should not raise
        _print_header("./intake_forms", "./data/patients.db")


class TestAddCommonArgs(unittest.TestCase):
    """Test _add_common_args helper."""

    def test_adds_all_required_args(self):
        """Test all common arguments are added."""
        import argparse

        from gaia.agents.emr.cli import _add_common_args

        parser = argparse.ArgumentParser()
        _add_common_args(parser)

        # Parse empty args to get defaults
        args = parser.parse_args([])

        # Verify all common args exist
        self.assertTrue(hasattr(args, "watch_dir"))
        self.assertTrue(hasattr(args, "db"))
        self.assertTrue(hasattr(args, "vlm_model"))
        self.assertTrue(hasattr(args, "debug"))


class TestCmdDashboard(unittest.TestCase):
    """Test cmd_dashboard command."""

    @patch(
        "gaia.agents.emr.dashboard.server.run_dashboard",
        side_effect=ImportError("No FastAPI"),
    )
    def test_dashboard_missing_dependencies(self, mock_run):
        """Test dashboard shows error when dependencies missing."""
        from gaia.agents.emr.cli import cmd_dashboard

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                host="127.0.0.1",
                port=8080,
                no_open=True,
                browser=False,
                debug=False,
            )

            result = cmd_dashboard(args)
            self.assertEqual(result, 1)


class TestCmdInit(unittest.TestCase):
    """Test cmd_init command."""

    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_init_lemonade_not_available(self, mock_client_class):
        """Test init when Lemonade server is not running."""
        from gaia.agents.emr.cli import cmd_init

        # Mock client to raise connection error
        mock_client = MagicMock()
        mock_client.health_check.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        args = argparse.Namespace(
            vlm_model="Qwen3-VL-4B-Instruct-GGUF",
            debug=False,
        )

        result = cmd_init(args)
        self.assertEqual(result, 1)

    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_init_lemonade_not_ok(self, mock_client_class):
        """Test init when Lemonade returns non-ok status."""
        from gaia.agents.emr.cli import cmd_init

        mock_client = MagicMock()
        mock_client.health_check.return_value = {"status": "error"}
        mock_client_class.return_value = mock_client

        args = argparse.Namespace(
            vlm_model="Qwen3-VL-4B-Instruct-GGUF",
            debug=False,
        )

        result = cmd_init(args)
        self.assertEqual(result, 1)


class TestCmdWatch(unittest.TestCase):
    """Test cmd_watch command."""

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    @patch("builtins.input", side_effect=["quit"])
    def test_watch_quit_command(self, mock_input, mock_agent_class):
        """Test watch mode responds to quit command."""
        from gaia.agents.emr.cli import cmd_watch

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            # Should exit cleanly when quit is entered
            cmd_watch(args)
            mock_agent.stop.assert_called_once()

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    @patch("builtins.input", side_effect=["exit"])
    def test_watch_exit_command(self, mock_input, mock_agent_class):
        """Test watch mode responds to exit command."""
        from gaia.agents.emr.cli import cmd_watch

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            cmd_watch(args)
            mock_agent.stop.assert_called_once()

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    @patch("builtins.input", side_effect=["stats", "quit"])
    def test_watch_stats_command(self, mock_input, mock_agent_class):
        """Test watch mode handles stats command."""
        from gaia.agents.emr.cli import cmd_watch

        mock_agent = MagicMock()
        mock_agent.get_stats.return_value = {
            "total_patients": 10,
            "new_patients": 7,
            "returning_patients": 3,
            "processed_today": 2,
            "files_processed": 15,
            "extraction_success": 14,
            "extraction_failed": 1,
            "success_rate": "93.3%",
            "time_saved_minutes": 45,
            "time_saved_percent": "85%",
            "unacknowledged_alerts": 0,
        }
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            cmd_watch(args)
            mock_agent.get_stats.assert_called_once()
            mock_agent.stop.assert_called_once()

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    @patch("builtins.input", side_effect=["How many patients?", "quit"])
    def test_watch_query_command(self, mock_input, mock_agent_class):
        """Test watch mode handles free-form queries."""
        from gaia.agents.emr.cli import cmd_watch

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            cmd_watch(args)
            mock_agent.process_query.assert_called_once_with("How many patients?")
            mock_agent.stop.assert_called_once()

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    @patch("builtins.input", side_effect=EOFError())
    def test_watch_handles_eof(self, mock_input, mock_agent_class):
        """Test watch mode handles EOF gracefully."""
        from gaia.agents.emr.cli import cmd_watch

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            # Should not raise
            cmd_watch(args)
            mock_agent.stop.assert_called_once()

    @patch("gaia.agents.emr.cli.MedicalIntakeAgent")
    @patch("builtins.input", side_effect=KeyboardInterrupt())
    def test_watch_handles_keyboard_interrupt(self, mock_input, mock_agent_class):
        """Test watch mode handles Ctrl+C gracefully."""
        from gaia.agents.emr.cli import cmd_watch

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        with temp_directory() as tmp_dir:
            args = argparse.Namespace(
                watch_dir=str(tmp_dir / "intake"),
                db=str(tmp_dir / "patients.db"),
                vlm_model="test-model",
                debug=False,
            )

            # Should not raise
            cmd_watch(args)
            mock_agent.stop.assert_called_once()


class TestLaunchElectron(unittest.TestCase):
    """Test _launch_electron helper."""

    @patch("shutil.which", return_value=None)
    def test_launch_electron_npx_not_found(self, mock_which):
        """Test launch_electron returns False when npx not found."""
        from gaia.agents.emr.cli import _launch_electron

        result = _launch_electron("http://localhost:8080", delay=0)
        self.assertFalse(result)


class TestMainEntryPoint(unittest.TestCase):
    """Test main() entry point."""

    def test_main_no_args_returns_zero(self):
        """Test main returns 0 when called with no args."""
        import sys

        from gaia.agents.emr.cli import main

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["gaia-emr"]
            result = main()
            self.assertEqual(result, 0)
        finally:
            sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
