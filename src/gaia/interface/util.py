import os
import sys
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QApplication, QProgressDialog
from PySide6.QtGui import QIcon

class UIBase:
    _app = None

    @staticmethod
    def _ensure_app():
        if not UIBase._app:
            UIBase._app = QApplication.instance() or QApplication(sys.argv)

    @staticmethod
    def resource_path(relative_path):
        """ Get absolute path to resource, works for dev and for PyInstaller """
        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = sys._MEIPASS # pylint:disable=W0212,E1101
        except Exception: # pylint:disable=W0718
            # If not running as a PyInstaller bundle, use the current file's directory
            base_path = os.path.dirname(os.path.abspath(__file__))

        return os.path.join(base_path, relative_path)

class UIMessage(UIBase):
    @staticmethod
    def display(message, title="Message", icon="gaia.ico", message_type=QMessageBox.Information):
        """
        Display a message in a window with an optional icon.

        Args:
        message (str): The message to display.
        title (str): The title of the message window.
        icon (str, optional): Name of the icon file in the 'img' folder.
        message_type (QMessageBox.Icon): Type of message (e.g., Information, Warning, Critical).
        """
        UIMessage._ensure_app()

        # Set up logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)

        # Check message type
        if message_type == QMessageBox.Critical:
            logger.error(f"{title}: {message}")
        elif message_type == QMessageBox.Warning:
            logger.warning(f"{title}: {message}")
        elif message_type == QMessageBox.Information:
            logger.info(f"{title}: {message}")
        else:
            logger.info(f"{title}: {message}")

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
                logger.warning(f"Icon file not found at {icon_path}")

        msg_box.exec()

    @staticmethod
    def error(message, title='Error', icon="gaia.ico"):
        """
        Display an error message in a window with an optional icon.
        """
        UIMessage.display(message, title, icon, QMessageBox.Critical)

    @staticmethod
    def info(message, title='Info', icon="gaia.ico"):
        """
        Display an information message in a window with an optional icon.
        """
        UIMessage.display(message, title, icon, QMessageBox.Information)

    @staticmethod
    def warning(message, title='Warning', icon="gaia.ico"):
        """
        Display a warning message in a window with an optional icon.
        """
        UIMessage.display(message, title, icon, QMessageBox.Warning)

    @staticmethod
    def progress(message: str = "Processing...", title: str = "Progress", icon="gaia.ico"):
        """
        Display a progress dialog for long-running operations like model downloads.

        Returns:
        tuple: (QProgressDialog, function to update progress)
        """
        UIMessage._ensure_app()

        progress_dialog = QProgressDialog()
        progress_dialog.setWindowTitle(title)
        progress_dialog.setLabelText(message)  # Set the message explicitly
        progress_dialog.setCancelButtonText("Cancel")
        progress_dialog.setRange(0, 100)  # Set initial range
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)
        progress_dialog.setMinimumWidth(300)  # Ensure dialog is wide enough for messages
        progress_dialog.show()

        if icon:
            icon_path = UIMessage.resource_path(os.path.join("img", icon))
            if os.path.exists(icon_path):
                progress_dialog.setWindowIcon(QIcon(icon_path))

        original_message = message  # Store the original message

        def update_progress(current_value, max_value, status=None):
            if max_value > sys.maxsize:
                # Scale down the values if they exceed the maximum integer size
                scale = max_value / sys.maxsize
                current_value = int(current_value / scale)
                max_value = sys.maxsize

            progress_dialog.setMaximum(max_value)
            progress_dialog.setValue(current_value)

            # Update label text with original message and status
            if status:
                progress_dialog.setLabelText(f"{original_message}\n{status}")
            else:
                progress_dialog.setLabelText(original_message)

            QApplication.processEvents()

        return progress_dialog, update_progress


def main():
    # Examples of using the utility functions
    # UIMessage.error("An error occurred")
    # UIMessage.info("Operation completed successfully")
    # UIMessage.warning("This is a warning")

    # For a custom message type
    # UIMessage.display("This is a custom message", message_type=QMessageBox.Question)

    # Test function for UIMessage.progress()
    def test_progress():
        import time
        progress_dialog, update_progress = UIMessage.progress("Simulating a long operation...")
        total_steps = 100
        for i in range(total_steps + 1):
            time.sleep(0.05)  # Simulate some work being done
            update_progress(i, total_steps, f"Step {i} of {total_steps}")
            if progress_dialog.wasCanceled():
                break
        progress_dialog.close()

    test_progress()

if __name__ == "__main__":
    main()
