# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import os
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QSizePolicy,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QFont

from huggingface_hub import HfFolder, HfApi
from gaia.logger import get_logger
from gaia.interface.util import UIMessage


class WindowDragMixin:
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_position = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self.drag_start_position
            self.move(self.pos() + delta)
            self.drag_start_position = event.globalPosition().toPoint()


class HuggingFaceTokenDialog(QWidget, WindowDragMixin):
    def __init__(self):
        _app = None
        super().__init__()
        self.log = get_logger(__name__)
        self.token = None
        self.token_verified = False
        self.setWindowTitle("Hugging Face Token")
        self.setFixedSize(400, 250)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        content_frame = QFrame(self)
        content_frame.setObjectName("contentFrame")
        content_frame.setStyleSheet(
            """
            #contentFrame {
                background-color: #1e1e1e;
                border-radius: 10px;
                border: 1px solid #333333;
            }
        """
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 0)
        content_frame.setGraphicsEffect(shadow)

        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)

        title_label = QLabel("Enter Hugging Face Token")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(
            """
            font-size: 18px;
            font-weight: bold;
            color: #ffffff;
        """
        )

        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Enter your token here")
        self.token_input.setStyleSheet(
            """
            QLineEdit {
                padding: 10px;
                border: 1px solid #555555;
                border-radius: 5px;
                background-color: #2a2a2a;
                color: #ffffff;
            }
            QLineEdit:focus {
                border: 2px solid #0078d4;
            }
        """
        )

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.setContentsMargins(0, 0, 0, 0)

        self.cli_login_button = QPushButton("Verify Login")
        self.verify_button = QPushButton("Verify Token")
        self.submit_button = QPushButton("Submit Token")
        self.cancel_button = QPushButton("Cancel")

        for button in [
            self.cli_login_button,
            self.verify_button,
            self.submit_button,
            self.cancel_button,
        ]:
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                """
                QPushButton {
                    padding: 10px 0px;
                    border-radius: 5px;
                    font-weight: bold;
                    color: #ffffff;
                    background-color: #0078d4;
                    min-width: 70px;
                }
                QPushButton:hover {
                    background-color: #1084d8;
                }
                QPushButton:pressed {
                    background-color: #005a9e;
                }
                QPushButton:disabled {
                    background-color: #444444;
                    color: #888888;
                }
            """
            )
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        self.submit_button.setEnabled(False)

        button_layout.addWidget(self.cli_login_button)
        button_layout.addWidget(self.verify_button)
        button_layout.addWidget(self.submit_button)
        button_layout.addWidget(self.cancel_button)

        content_layout.addWidget(title_label)
        content_layout.addWidget(self.token_input)
        content_layout.addLayout(button_layout)

        main_layout.addWidget(content_frame)

        self.cli_login_button.clicked.connect(self.huggingface_cli_login)
        self.verify_button.clicked.connect(self.verify_token)
        self.submit_button.clicked.connect(self.submit_token)
        self.cancel_button.clicked.connect(self.close)
        self.token_input.textChanged.connect(self.on_token_changed)

    def huggingface_cli_login(self):
        try:
            # Check if a token is already saved
            existing_token = HfFolder.get_token()
            if existing_token:
                # Verify the existing token
                if self.is_token_valid(existing_token):
                    self.token = existing_token
                    self.token_input.setText(self.token)
                    self.token_verified = True
                    self.submit_button.setEnabled(True)
                    UIMessage.info(
                        "SUCCESS! You are already logged in to Hugging Face!"
                    )
                else:
                    UIMessage.warning(
                        "Existing token is invalid. Please enter a new token."
                    )
            else:
                UIMessage.warning(
                    "No Hugging Face token found. Please login by entering `huggingface-cli login` in the command shell."
                )

        except Exception as e:
            UIMessage.error(f"An error occurred while verifying login status: {str(e)}")

    def verify_token(self):
        token = self.token_input.text()
        if self.is_token_valid(token):
            UIMessage.info("SUCCESS! Token verified successfully!")
            self.token_verified = True
            self.submit_button.setEnabled(True)
        else:
            self.token_verified = False
            self.submit_button.setEnabled(False)

    def is_token_valid(self, token):
        try:
            api = HfApi()
            api.whoami(token)
            return True
        except Exception as e:
            UIMessage.error(str(e))
            return False

    def submit_token(self):
        if not self.token_verified:
            UIMessage.warning("Please verify the token before submitting.")
            return

        self.token = self.token_input.text()
        if self.token:
            os.environ["HUGGINGFACE_TOKEN"] = self.token
            HfFolder.save_token(self.token)
            UIMessage.info("Token saved successfully!")
            self.close()
        else:
            UIMessage.warning("Please enter a valid token.")

    def on_token_changed(self):
        self.token_verified = False
        self.submit_button.setEnabled(False)


def get_huggingface_token():
    app = QApplication.instance()
    if not app:
        app = QApplication()

    # Set application style
    app.setStyle("Fusion")

    # Set a dark color palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
    palette.setColor(QPalette.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    # Set a modern font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    dialog = HuggingFaceTokenDialog()
    dialog.show()
    app.exec()

    return dialog.token


# Add the following test function at the bottom of the file
def test_get_huggingface_token():
    print("Testing get_huggingface_token function...")
    token = get_huggingface_token()
    if token:
        print(f"Token received: {token[:4]}...{token[-4:]}")
    else:
        print("No token received or dialog was cancelled.")


if __name__ == "__main__":
    test_get_huggingface_token()
