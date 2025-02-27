# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import sys
import getpass

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
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
        super().__init__()
        self.log = get_logger(__name__)
        self.token = None
        self.token_verified = False
        self.setWindowTitle("Hugging Face Login")
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
                background-color: rgb(20, 20, 20);
                border-radius: 10px;
                border: 1px solid #0A819A;
            }
            """
        )

        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)

        title_label = QLabel("Enter Hugging Face Token")
        title_label.setAlignment(Qt.AlignLeft)
        title_label.setFont(QFont("Segoe UI", 18))
        title_label.setStyleSheet(
            """
            QLabel {
                color: rgb(255, 255, 255);
                padding: 5px 0px;
            }
            """
        )

        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText(
            "Please enter your token, needed to access HF gated models"
        )
        self.token_input.setFont(QFont("Segoe UI", 12))
        self.token_input.setStyleSheet(
            """
            QLineEdit {
                border-radius: 3px;
                border: 1px solid #0A819A;
                color: white;
                padding: 5px;
                background-color: rgb(20, 20, 20);
            }
            QLineEdit:focus {
                border: 1px solid #4AA9BD;
            }
            """
        )

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.setContentsMargins(0, 0, 0, 0)

        # Create buttons with more compact sizing
        self.verify_button = QPushButton("Verify")
        self.submit_button = QPushButton("Submit")
        self.cancel_button = QPushButton("Cancel")

        # Create two rows of buttons
        top_button_layout = QHBoxLayout()
        top_button_layout.addWidget(self.verify_button)

        bottom_button_layout = QHBoxLayout()
        bottom_button_layout.addWidget(self.submit_button)
        bottom_button_layout.addWidget(self.cancel_button)

        # Add both button layouts to a vertical layout
        buttons_container = QVBoxLayout()
        buttons_container.addLayout(top_button_layout)
        buttons_container.addLayout(bottom_button_layout)
        buttons_container.setSpacing(10)

        for button in [self.verify_button, self.submit_button, self.cancel_button]:
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(QFont("Segoe UI", 12))
            button.setStyleSheet(
                """
                QPushButton {
                    border-radius: 3px;
                    border: 1px solid #0A819A;
                    background-color: #0A819A;
                    color: rgb(255, 255, 255);
                    padding: 8px 16px;
                    min-width: 120px;
                }
                QPushButton:hover {
                    background-color: #4AA9BD;
                }
                QPushButton:disabled {
                    background-color: rgb(77, 77, 77);
                    border-color: rgb(77, 77, 77);
                    color: rgb(180, 180, 180);
                }
                QToolTip {
                    background-color: rgb(20, 20, 20);
                    color: white;
                    border: 1px solid #0A819A;
                    padding: 5px;
                    border-radius: 3px;
                }
                """
            )

        # Disable submit button by default and add tooltip
        self.submit_button.setEnabled(False)
        self.submit_button.setToolTip("Need to verify before submitting.")

        content_layout.addWidget(title_label)
        content_layout.addWidget(self.token_input)
        content_layout.addLayout(buttons_container)

        main_layout.addWidget(content_frame)

        self.verify_button.clicked.connect(self.verify_token)
        self.submit_button.clicked.connect(self.submit_token)
        self.cancel_button.clicked.connect(self.close)
        self.token_input.textChanged.connect(self.on_token_changed)

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

    def show(self):
        super().show()
        self.raise_()
        self.activateWindow()


def get_huggingface_token(cli_mode=False):
    if cli_mode:
        print("Please enter your Hugging Face token:")
        token = getpass.getpass()
        return token

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


def set_huggingface_token(cli_mode=False):
    try:
        # Check if the user is logged in to Hugging Face
        token = HfFolder.get_token()

        if not token:
            token = os.getenv("HF_TOKEN")

        if not token:
            token = os.getenv("HUGGINGFACE_ACCESS_TOKEN")

        if not token:
            token = get_huggingface_token(cli_mode)

        # Verify the token
        if token:
            os.environ["HF_TOKEN"] = token
            os.environ["HUGGINGFACE_ACCESS_TOKEN"] = token
            api = HfApi(token=token)
            try:
                api.whoami(token)
                if cli_mode:
                    print("Token verified successfully!")
            except Exception as e:
                error_msg = f"Invalid Hugging Face token: {str(e)}"
                if cli_mode:
                    print(f"Error: {error_msg}", file=sys.stderr)
                else:
                    UIMessage.error(error_msg)
                return None
        return token

    except Exception as e:
        UIMessage.error(f"An unexpected error occurred: {str(e)}", cli_mode=cli_mode)
        return None


# Update the test function to support CLI testing
def test_get_huggingface_token(cli_mode=False):
    print("Testing get_huggingface_token function...")
    token = get_huggingface_token(cli_mode)
    if token:
        print(f"Token received: {token[:4]}...{token[-4:]}")
    else:
        print("No token received or operation was cancelled.")


if __name__ == "__main__":
    # Add command line argument support
    cli_mode = "--cli" in sys.argv
    test_get_huggingface_token(cli_mode)
