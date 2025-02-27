# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import sys
import inspect

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QApplication, QProgressDialog, QInputDialog
from PySide6.QtGui import QIcon

from gaia.logger import get_logger

log = get_logger(__name__)


class UIBase:
    _app = None

    @staticmethod
    def _ensure_app():
        if not UIBase._app:
            UIBase._app = QApplication.instance() or QApplication(sys.argv)

    @staticmethod
    def resource_path(relative_path):
        """Get absolute path to resource, works for dev and for PyInstaller"""
        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = sys._MEIPASS  # pylint:disable=W0212,E1101
        except Exception:
            # If not running as a PyInstaller bundle, use the current file's directory
            base_path = os.path.dirname(os.path.abspath(__file__))

        return os.path.join(base_path, relative_path)


class UIMessage(UIBase):
    @staticmethod
    def display(
        message,
        title="Message",
        icon="gaia.ico",
        cli_mode=False,
        message_type=QMessageBox.Information,
    ):
        """
        Display a message in a window with an optional icon or in the console if in CLI mode.

        Args:
        message (str): The message to display.
        title (str): The title of the message window.
        icon (str, optional): Name of the icon file in the 'img' folder.
        cli_mode (bool): If True, display message in console instead of GUI.
        message_type (QMessageBox.Icon): Type of message (e.g., Information, Warning, Critical).
        """
        # Determine the appropriate log level based on message_type
        if message_type == QMessageBox.Critical:
            log_func = log.error
        elif message_type == QMessageBox.Warning:
            log_func = log.warning
        else:
            log_func = log.info

        # Get caller information
        caller = inspect.currentframe().f_back.f_back
        func_name = caller.f_code.co_name
        filename = os.path.basename(caller.f_code.co_filename)
        line_number = caller.f_lineno

        # Prepare the full message with caller info
        dbg_message = f"{filename}:{line_number} | {func_name} | {message}"

        # Log the message
        log_func(f"{title}: {dbg_message}")

        if cli_mode:
            # In CLI mode, just print the message to the console
            log.info(f"{title}: {message}")
        else:
            UIMessage._ensure_app()

            msg_box = QMessageBox()
            msg_box.setIcon(message_type)
            msg_box.setWindowTitle(title)
            msg_box.setText(message)
            msg_box.setStandardButtons(QMessageBox.Ok)

            if icon:
                icon_path = UIMessage.resource_path(os.path.join("img", icon))
                if os.path.exists(icon_path):
                    msg_box.setWindowIcon(QIcon(icon_path))
                else:
                    log.warning(f"Icon file not found at {icon_path}")

            msg_box.exec()

    @staticmethod
    def error(message, title="Error", cli_mode=False, icon="gaia.ico"):
        """
        Display an error message in a window with an optional icon.
        """
        # Get caller information
        caller = inspect.currentframe().f_back
        filename = os.path.basename(caller.f_code.co_filename)
        func_name = caller.f_code.co_name
        line_number = caller.f_lineno

        # Prepare the full message with caller info
        dbg_message = f"{filename}:{line_number} | {func_name} | {message}"

        if cli_mode:
            log.error(f"{title}: {dbg_message}")
        else:
            UIMessage.display(message, title, icon, cli_mode, QMessageBox.Critical)

    @staticmethod
    def info(message, title="Info", cli_mode=False, icon="gaia.ico"):
        """
        Display an information message in a window with an optional icon.
        """
        # Get caller information
        caller = inspect.currentframe().f_back
        filename = os.path.basename(caller.f_code.co_filename)
        func_name = caller.f_code.co_name
        line_number = caller.f_lineno

        # Prepare the full message with caller info
        dbg_message = f"{filename}:{line_number} | {func_name} | {message}"

        if cli_mode:
            log.info(f"{title}: {dbg_message}")
        else:
            UIMessage.display(message, title, icon, cli_mode, QMessageBox.Information)

    @staticmethod
    def warning(message, title="Warning", cli_mode=False, icon="gaia.ico"):
        """
        Display a warning message in a window with an optional icon.
        """
        # Get caller information
        caller = inspect.currentframe().f_back
        filename = os.path.basename(caller.f_code.co_filename)
        func_name = caller.f_code.co_name
        line_number = caller.f_lineno

        # Prepare the full message with caller info
        dbg_message = f"{filename}:{line_number} | {func_name} | {message}"

        if cli_mode:
            log.warning(f"{title}: {dbg_message}")
        else:
            UIMessage.display(message, title, icon, cli_mode, QMessageBox.Warning)

    @staticmethod
    def progress(
        message: str = "Processing...",
        title: str = "Progress",
        cli_mode=False,
        icon="gaia.ico",
    ):
        """
        Display a progress indicator for long-running operations like model downloads.
        In GUI mode, shows a progress dialog. In CLI mode, prints progress to console.

        Returns:
        tuple: (ProgressHandler, function to update progress)
        """
        if cli_mode:
            print(f"{title}: {message}")

            def update_progress_cli(current_value, max_value, status=None):
                progress = int((current_value / max_value) * 100)
                status_text = f" - {status}" if status else ""
                print(
                    f"\r[{'#' * (progress // 2)}{' ' * (50 - (progress // 2))}] {progress}%{status_text}",
                    end="",
                    flush=True,
                )
                if progress == 100:
                    print()  # New line when complete

            return None, update_progress_cli

        UIMessage._ensure_app()

        progress_dialog = QProgressDialog()
        progress_dialog.setWindowTitle(title)
        progress_dialog.setLabelText(message)
        progress_dialog.setCancelButtonText("Cancel")
        progress_dialog.setRange(0, 100)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)
        progress_dialog.setMinimumWidth(300)

        # Add these lines to make the dialog stay on top
        progress_dialog.setWindowFlags(
            progress_dialog.windowFlags() | Qt.WindowStaysOnTopHint
        )
        progress_dialog.show()

        if icon:
            icon_path = UIMessage.resource_path(os.path.join("img", icon))
            if os.path.exists(icon_path):
                progress_dialog.setWindowIcon(QIcon(icon_path))

        progress_dialog.raise_()
        progress_dialog.activateWindow()

        original_message = message

        def update_progress_ui(current_value, max_value, status=None):
            if max_value > sys.maxsize:
                scale = max_value / sys.maxsize
                current_value = int(current_value / scale)
                max_value = sys.maxsize

            progress_dialog.setMaximum(max_value)
            progress_dialog.setValue(current_value)

            if status:
                progress_dialog.setLabelText(f"{original_message}\n{status}")
            else:
                progress_dialog.setLabelText(original_message)

            QApplication.processEvents()

        return progress_dialog, update_progress_ui

    @staticmethod
    def input(message, title="Input", default_text="", cli_mode=False, icon="gaia.ico"):
        """
        Display an input dialog and return the user's text input.

        Args:
        message (str): The prompt message to display.
        title (str): The title of the input window.
        default_text (str): Default text to show in the input field.
        cli_mode (bool): If True, use console input instead of GUI.
        icon (str, optional): Name of the icon file in the 'img' folder.

        Returns:
        tuple: (bool, str) - (True if OK was pressed, entered text) for GUI mode
               or (True, entered text) for CLI mode
        """
        if cli_mode:
            try:
                user_input = input(f"{title}: {message}\n")
                log.info(f"{title}: {user_input}")
                return True, user_input
            except (KeyboardInterrupt, EOFError):
                return False, ""
        else:
            UIMessage._ensure_app()

            input_dialog = QInputDialog()
            input_dialog.setWindowTitle(title)
            input_dialog.setLabelText(message)
            input_dialog.setTextValue(default_text)

            if icon:
                icon_path = UIMessage.resource_path(os.path.join("img", icon))
                if os.path.exists(icon_path):
                    input_dialog.setWindowIcon(QIcon(icon_path))

            ok = input_dialog.exec()
            log.info(f"{title}: {input_dialog.textValue()}")
            return ok == 1, input_dialog.textValue()


def main():
    def test_progress(cli_mode):
        import time

        progress_dialog, update_progress = UIMessage.progress(
            "Simulating a long operation...", cli_mode=cli_mode
        )
        total_steps = 100
        for i in range(total_steps + 1):
            time.sleep(0.01)  # Simulate some work being done
            update_progress(i, total_steps, f"Step {i} of {total_steps}")
            if not cli_mode and progress_dialog.wasCanceled():
                break
        if not cli_mode:
            progress_dialog.close()

    # Test both CLI and GUI modes
    for cli_mode in [True, False]:
        mode = "CLI" if cli_mode else "GUI"
        print(f"\nTesting {mode} mode:\n-----------------")

        # Test error, info, and warning messages
        UIMessage.error("An error occurred", cli_mode=cli_mode)
        UIMessage.info("Operation completed successfully", cli_mode=cli_mode)
        UIMessage.warning("This is a warning", cli_mode=cli_mode)
        UIMessage.input(
            "Please enter your name:", "Name Input", "John Doe", cli_mode=cli_mode
        )

        # Test progress
        test_progress(cli_mode)

        # For a custom message type (GUI only)
        print("\nTesting custom message type (GUI only):")
        UIMessage.display(
            "This is a custom message",
            cli_mode=cli_mode,
            message_type=QMessageBox.Question,
        )


if __name__ == "__main__":
    main()
