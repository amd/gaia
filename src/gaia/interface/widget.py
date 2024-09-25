# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# This Python file uses the following encoding: utf-8
import sys
import os
import re
import time
import socket
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
import textwrap
import subprocess
import multiprocessing
from aiohttp import web, ClientSession

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QMessageBox,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpacerItem,
    QSizePolicy,
    QGraphicsDropShadowEffect,
    QProgressBar,
)
from PySide6.QtCore import Qt, QUrl, QEvent, QSize, QObject, Signal, Slot, QThread
from PySide6.QtGui import QPixmap, QDesktopServices, QMovie, QIcon, QColor, QPalette, QFont
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from huggingface_hub import HfFolder, HfApi

import gaia.agents as agents
from gaia.interface.ui_form import Ui_Widget
from gaia.llm.server import launch_llm_server

# This is a temporary workaround since the Qt Creator generated files
# do not import from the gui package.
sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    gaia_folder = Path(__file__).parent / "gaia"
else:
    gaia_folder = Path(__file__).parent.parent


# SetupLLM class performs tasks in a separate thread
class SetupLLM(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

        # Set up logging
        logging.basicConfig(level=logging.DEBUG)
        self.log = logging.getLogger(__name__)

    # Request Agent Server to update connection to LLM Server
    async def request_llm_load(self):
        async with ClientSession() as session:
            async with session.post(
                "http://127.0.0.1:8001/load_llm",
                json={"model": self.widget.ui.model.currentText()},
            ) as response:
                # Wait for response from server
                response_data = await response.json()
                # Check if LLM has been successfully loaded
                if response_data.get("status") == "Success":
                    self.log.debug("LLM has been loaded successfully!")
                else:
                    self.log.error("Failed to load LLM.")

    @Slot()
    def do_work(self):
        # Close previously open servers, if any
        if self.widget.agent_server is not None:
            self.log.debug("Closing open Agent server")
            self.widget.agent_server.terminate()
            self.widget.agent_server = None
        if self.widget.llm_server is not None:
            self.log.debug("Closing open LLM server")
            self.widget.llm_server.terminate()
            self.widget.llm_server = None

        # Switch visibility of UI elements
        self.widget.ui.loading.setVisible(True)
        self.widget.ui.loading.setVisible(True)
        self.widget.ui.loadingLabel.setVisible(True)
        self.widget.ui.loadingGif.setVisible(True)
        self.widget.ui.ask.setEnabled(False)
        self.widget.ui.model.setEnabled(False)
        self.widget.ui.device.setEnabled(False)
        self.widget.ui.agent.setEnabled(False)

        # Initialize Agent server
        self.widget.ui.loadingLabel.setText("Initializing Agent Server...")

        # Open using subprocess or multiprocessing depending on the selected settings
        creationflags = subprocess.CREATE_NEW_CONSOLE
        selected_agent = self.widget.ui.agent.currentText()
        if self.widget.settings["dev_mode"]:
            app_dot_py = gaia_folder / "agents" / selected_agent / "app.py"
            command = [sys.executable, app_dot_py]
            self.widget.agent_server = subprocess.Popen(
                command, creationflags=creationflags
            )
        else:
            self.widget.agent_server = multiprocessing.Process(
                target=getattr(agents, selected_agent.lower())
            )
            self.widget.agent_server.start()

        while not is_server_available("127.0.0.1", 8001):
            time.sleep(1)

        # Initialize LLM server
        selected_model = self.widget.ui.model.currentText()
        selected_device_dtype = self.widget.ui.device.currentText()
        selected_device, selected_dtype = self.widget.device_list_mapping[
            selected_device_dtype
        ]
        model_settings = self.widget.settings["models"][selected_model]
        self.widget.ui.loadingLabel.setText(
            f"Initializing LLM server for {selected_model} on {self.widget.ui.device.currentText()}..."
        )

        llm_server_kwargs = {
            "backend" : model_settings["backend"],
            "checkpoint": model_settings["checkpoint"],
            "max_new_tokens": int(self.widget.settings["max_new_tokens"]),
            "device": selected_device.lower(),
            "dtype": selected_dtype.lower(),
        }
        self.log.info(f"Starting LLM server with params: {llm_server_kwargs}")

        if self.widget.settings["dev_mode"]:
            server_dot_py = gaia_folder / "llm" / "server.py"
            command = [
                sys.executable,
                server_dot_py,
            ] + sum(
                ([f"--{key}", str(value)] for key, value in llm_server_kwargs.items()),
                [],
            )
            if self.widget.settings["llm_server"]:
                self.widget.llm_server = subprocess.Popen(
                    command, creationflags=creationflags
                )
        else:
            if self.widget.settings["llm_server"]:
                self.widget.llm_server = multiprocessing.Process(
                    target=launch_llm_server, kwargs=llm_server_kwargs
                )
                self.widget.llm_server.start()
                while not is_server_available("127.0.0.1", 8000):
                    time.sleep(1)
        if self.widget.settings["llm_server"]:
            asyncio.run(self.request_llm_load())

        # Done
        self.widget.ui.loadingLabel.setText(
            f"Ready to run {selected_model} on {self.widget.ui.device.currentText()}!"
        )
        self.widget.ui.loadingGif.setVisible(False)

        self.widget.ui.ask.setEnabled(True)
        self.widget.ui.model.setEnabled(True)
        self.widget.ui.device.setEnabled(True)
        self.widget.ui.agent.setEnabled(True)

        asyncio.run(self.widget.request_restart())

        self.finished.emit()


class StreamToAgent(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    async def prompt_llm(self, prompt):
        async with ClientSession() as session:
            async with session.post(
                "http://127.0.0.1:8001/prompt", json={"prompt": prompt}
            ) as response:
                await response.read()

    @Slot()
    def do_work(self):
        # Prompt LLM and stream results
        _, message_frame, _, _ = self.widget.cards[str(self.widget.card_count - 2)]

        # Get the text from the main label inside the message frame
        main_label = message_frame.layout().itemAt(0).widget()
        if isinstance(main_label, QLabel):
            prompt = main_label.text()
        else:
            self.log.error("Main label not found in message frame")
            return

        asyncio.run(self.prompt_llm(prompt))

        # Reenable send and restart buttons
        self.widget.ui.ask.setEnabled(True)
        self.widget.ui.restart.setEnabled(True)

        self.finished.emit()


class StreamFromAgent(QObject):
    finished = Signal()
    add_card = Signal(str, str, bool, dict)
    update_card = Signal(str, str, dict, bool, bool)

    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        self.app = web.Application()
        self.host = "127.0.0.1"
        self.port = 8002
        self.app.router.add_post("/stream_to_ui", self.receive_stream_from_agent)
        self.complete_message = ""
        self.agent_card_count = 0

    @property
    def last_agent_card_id(self):
        return f"agent_{self.agent_card_count}"

    async def receive_stream_from_agent(self, request):
        data = await request.json()
        chunk = data["chunk"]
        new_card = data["new_card"]
        stats = data.get("stats")
        final_update = data.get("last")
        if new_card:
            self.complete_message = chunk
            self.agent_card_count += 1
            self.add_card.emit(self.complete_message, self.last_agent_card_id, False, stats)
        else:
            self.complete_message = self.complete_message + chunk
            self.update_card.emit(self.complete_message, self.last_agent_card_id, stats, final_update, False)
        return web.json_response({"status": "Received"})

    @Slot()
    def do_work(self):
        web.run_app(self.app, host=self.host, port=self.port)
        self.finished.emit()


def is_server_available(host, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((host, port))
        s.close()
        return True

    except (ConnectionRefusedError, TimeoutError):
        # If connection is refused, return False
        return False


class Widget(QWidget):
    def __init__(self, parent=None, server=True):
        super().__init__(parent)

        # control enabling of web server
        self.server = server

        # Set up logging
        logging.basicConfig(level=logging.DEBUG)
        self.log = logging.getLogger(__name__)

        # Configure the logging level for aiohttp.access
        logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

        # Set size policy for the main widget
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.network_manager = QNetworkAccessManager(self)

        # Connect the finished signal to our custom slot
        self.network_manager.finished.connect(self.on_network_request_finished)

        # Add a dictionary to store supported preview types and their handlers
        self.preview_handlers = {
            'youtube': self.create_youtube_preview,
            'webpage': self.create_webpage_preview,
        }

        self.ui = Ui_Widget()
        self.ui.setupUi(self)
        self.content_layout = self.ui.boardLayout
        self.setStyleSheet("background-color: black;")
        self.setWindowTitle("RyzenAI GAIA")
        self.card_count = 0
        self.cards = {}
        self.agent_server = None
        self.llm_server = None
        self.device_list_mapping = {}

        # Read settings
        settings_dot_json = gaia_folder / "interface" / "settings.json"
        with open(settings_dot_json, "r", encoding="utf-8") as file:
            self.settings = json.load(file)

        # Populate all models and update device list
        for model in self.settings["models"]:
            self.ui.model.addItem(model)
        self.ui.model.setCurrentIndex(0)
        self.update_device_list()

        # Populate agents
        for agent in self.settings["agents"]:
            self.ui.agent.addItem(agent)
        self.ui.agent.setCurrentIndex(0)

        # Connect buttons
        self.ui.ask.clicked.connect(self.send_message)
        self.ui.restart.clicked.connect(
            lambda: self.restart_conversation(notify_agent_server=True)
        )
        self.ui.model.currentIndexChanged.connect(self.update_device_list)
        self.ui.model.currentIndexChanged.connect(self.deployment_changed)
        self.ui.device.currentIndexChanged.connect(self.deployment_changed)
        self.ui.agent.currentIndexChanged.connect(self.deployment_changed)

        # Ensure that we are scrolling to the bottom every time the range
        # of the card scroll area changes
        self.ui.scrollArea.verticalScrollBar().rangeChanged.connect(
            self.scrollToBottom,
        )

        # Keep track of spacers indexes to properly remove and add them back
        self.top_spacer_index = self.ui.mainLayout.indexOf(self.ui.welcomeSpacerTop)
        self.bottom_spacer_index = self.ui.mainLayout.indexOf(
            self.ui.welcomeSpacerBottom
        )

        # Install event filter to prompt text box
        self.ui.prompt.installEventFilter(self)

        # Hide/disable some of the components initially
        self.ui.chat.setVisible(False)
        if self.settings["hide_agents"]:
            self.ui.agent.setVisible(False)
        if self.settings["hide_agents"]:
            self.ui.agent.setVisible(False)
        if self.settings["hide_agents"]:
            self.ui.agent.setVisible(False)

        # Loading symbol
        self.movie = QMovie(r":/img/loading.gif")
        self.movie.setScaledSize(QSize(300, 300))
        self.ui.loadingGif.setFixedSize(QSize(300, 25))
        self.ui.loadingGif.setMovie(self.movie)
        self.movie.start()

        # Create setup thread
        if self.server:
            self.setupThread = QThread()
            self.setupWorker = SetupLLM(self)
            self.setupWorker.moveToThread(self.setupThread)
            self.setupThread.started.connect(self.setupWorker.do_work)
            self.setupWorker.finished.connect(self.setupThread.quit)
            self.setupThread.start()

            # Create threads for interfacing with the agent
            self.agentSendThread = QThread()
            self.agentSendWorker = StreamToAgent(self)
            self.agentSendWorker.moveToThread(self.agentSendThread)
            self.agentSendThread.started.connect(self.agentSendWorker.do_work)
            self.agentSendWorker.finished.connect(self.agentSendThread.quit)

        self.agentReceiveThread = QThread()
        self.agentReceiveWorker = StreamFromAgent(self)
        self.agentReceiveWorker.moveToThread(self.agentReceiveThread)
        self.agentReceiveThread.started.connect(self.agentReceiveWorker.do_work)
        self.agentReceiveWorker.finished.connect(self.agentReceiveThread.quit)
        self.agentReceiveWorker.add_card.connect(self.add_card)
        self.agentReceiveWorker.update_card.connect(self.update_card)
        self.agentReceiveThread.start()


    def _format_value(self, val):
        if isinstance(val, float):
            return f"{val:.1f}"
        return str(val)


    def closeEvent(self, *args, **kwargs):  # pylint: disable=unused-argument
        # Make sure  servers are killed when application exits
        if self.agent_server is not None:
            self.log.debug("Closing agent server")
            self.agent_server.terminate()
        if self.llm_server is not None:
            self.log.debug("Closing LLM server")
            self.llm_server.terminate()


    def update_device_list(self):
        selected_model = self.ui.model.currentText()
        model_device_settings = self.settings["models"][selected_model]["device"]
        for device in model_device_settings:
            for dtype in model_device_settings[device]:
                device_dtype_text = f"{device} ({dtype})"
                self.ui.device.addItem(device_dtype_text)
                self.device_list_mapping[device_dtype_text] = (device, dtype)


    def make_chat_visible(self, visible):
        if (visible and self.ui.chat.isVisible()) or (
            not visible and not self.ui.chat.isVisible()
        ):
            # Skip if we are already at the visibility we desire
            return
        if visible:
            self.ui.loadingLabel.setVisible(False)
            self.ui.loading.setVisible(False)
            self.ui.chat.setVisible(True)
            self.ui.sampleCard_1.setVisible(False)
            self.ui.sampleCard_2.setVisible(False)
            self.ui.mainLayout.removeItem(self.ui.welcomeSpacerTop)
            self.ui.mainLayout.removeItem(self.ui.welcomeSpacerBottom)
        else:
            self.ui.loadingLabel.setVisible(True)
            self.ui.loading.setVisible(True)
            self.ui.chat.setVisible(False)
            self.ui.mainLayout.insertItem(
                self.top_spacer_index, self.ui.welcomeSpacerTop
            )
            self.ui.mainLayout.insertItem(
                self.bottom_spacer_index, self.ui.welcomeSpacerBottom
            )


    def eventFilter(self, obj, event):
        """
        Event filter used to send message when enter is pressed inside the prompt box
        """
        if (
            event.type() == QEvent.KeyPress
            and not (event.modifiers() & Qt.ShiftModifier)
            and obj is self.ui.prompt
        ):
            if (
                event.key() == Qt.Key_Return
                and self.ui.prompt.hasFocus()
                and self.ui.ask.isEnabled()
            ):
                # Send message and consume the event, preventing return from being added to prompt box
                self.send_message()
                return True
        return super().eventFilter(obj, event)


    def deployment_changed(self):
        self.restart_conversation(notify_agent_server=False)
        if self.server:
            self.setupThread.quit()
            self.setupThread.wait()
            self.setupThread.start()


    # Request LLM server to also restart
    async def request_restart(self):
        if self.server:
            async with ClientSession() as session:
                async with session.post(
                    "http://127.0.0.1:8001/restart", json={}
                ) as response:
                    await response.read()


    def restart_conversation(self, notify_agent_server):
        # Disable chat
        self.make_chat_visible(False)

        # Delete existing cards
        for card in self.cards:
            # Remove the frame from its parent layout if it has one
            card_frame, _, _, _ = self.cards[card]
            if card_frame.parent():
                card_frame.setParent(None)
            # Delete the frame
            card_frame.deleteLater()
        self.cards = {}
        self.card_count = 0

        # Let agent server know that we are restarting the conversation
        if notify_agent_server:
            asyncio.run(self.request_restart())


    def send_message(self):
        prompt = self.ui.prompt.toPlainText()
        self.ui.prompt.clear()
        if prompt:
            # Disable send and restart buttons
            self.ui.ask.setEnabled(False)
            self.ui.restart.setEnabled(False)

            # Send message
            self.add_card(message=prompt, card_id=None, from_user=True)

            # Create a placeholder "loading" message
            self.add_card(message="", card_id="loading", from_user=False)

            # Send prompt to agent
            if self.server:
                self.agentSendThread.start()
            self.make_chat_visible(True)


    def split_into_chunks(self, message, chuck_size=75):
        chunks = []
        lines = message.split("\n")
        for line in lines:
            chunks.extend(textwrap.wrap(line, width=chuck_size))
        return "\n".join(chunks)


    def add_card(self, message="", card_id=None, from_user=False, stats=None):
        self.log.debug(f"add_card called with card_id: {card_id}")
        self.make_chat_visible(True)

        chunked_message = self.split_into_chunks(message)

        # If there is already a "loading" card waiting we will take that one
        if "loading" in self.cards and not from_user:
            card, message_frame, label, firstTokenAnimation = self.cards.pop("loading")
            message_frame.setVisible(True)
            main_label = message_frame.layout().itemAt(0).widget()
            main_label.setText(chunked_message)
            firstTokenAnimation.setVisible(False)

        else:
            # Create the main card frame
            card = QFrame()
            card.setFrameShape(QFrame.NoFrame)
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(9, 0, 9, 0)
            card_layout.setSpacing(0)

            # Create the card message frame
            card_message = QFrame()
            card_message_layout = QVBoxLayout(card_message)
            card_message_layout.setContentsMargins(9, 0, 9, 0)
            card_message_layout.setSpacing(0)

            # Create the message frame (QFrame instead of QPushButton)
            message_frame = QFrame()
            message_frame_layout = QVBoxLayout(message_frame)
            message_frame_layout.setContentsMargins(0, 0, 0, 0)
            message_frame_layout.setSpacing(0)

            # Create and add the main text label to the message frame
            main_label = QLabel(chunked_message)
            main_label.setWordWrap(True)
            main_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            message_frame_layout.addWidget(main_label)

            firstTokenAnimation = None
            if from_user:
                self.apply_user_style(message_frame)
            else:
                firstTokenAnimation = QLabel()
                firstTokenMovie = QMovie(r":/img/waiting_token.gif")
                firstTokenMovie.setScaledSize(QSize(50, 50))
                firstTokenAnimation.setFixedSize(QSize(50, 50))
                firstTokenAnimation.setMovie(firstTokenMovie)
                card_message_layout.addWidget(firstTokenAnimation)

                self.apply_assistant_style(message_frame)

                if message == "":
                    message_frame.setVisible(False)
                    firstTokenMovie.start()
                else:
                    firstTokenAnimation.setVisible(False)

            label_text = f'{datetime.now().strftime("%H:%M:%S")}   '
            if stats:
                label_text += "   ".join(f"{key}: {self._format_value(val)}"
                                       for key, val in stats.items() if val is not None)
            label = QLabel(label_text)
            label.setVisible(self.settings["show_label"])
            label.setStyleSheet("color: rgb(255, 255, 255);")

            card_message_layout.addWidget(message_frame)
            card_message_layout.addWidget(label)

            # Add the card message layout to the card
            spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
            if from_user:
                card_layout.addItem(spacer)
                card_layout.addWidget(card_message)
                label.setAlignment(Qt.AlignRight)
            else:
                card_layout.addWidget(card_message)
                card_layout.addItem(spacer)
                label.setAlignment(Qt.AlignLeft)

            # Add the card to the main layout
            self.ui.boardLayout.addWidget(card)

        # Keep track of card
        if card_id is None:
            card_id = str(self.card_count)
        self.cards[card_id] = (card, message_frame, label, firstTokenAnimation)
        self.card_count += 1
        self.repaint()

        self.log.debug(f"Card added with id: {card_id}, content: {message}")
        return card_id


    def update_card(self, message, card_id, stats=None, final_update=False, from_user=False):
        self.log.debug(f"update_card called with message: {message}, card_id: {card_id}, final_update: {final_update}")
        if card_id not in self.cards:
            self.log.error(f"Card with id {card_id} not found.")
            return

        _, message_frame, label, firstTokenAnimation = self.cards[card_id]

        # Update timestamp and stats
        label_text = f'{datetime.now().strftime("%H:%M:%S")}   '
        if stats:
            label_text += "   ".join(f"{key}: {self._format_value(val)}"
                                   for key, val in stats.items() if val is not None)
        label.setText(label_text)

        # Hide the loading animation if it exists
        if firstTokenAnimation:
            firstTokenAnimation.setVisible(False)

        message_frame.setVisible(True)

        # Process and add new content
        if final_update:
            # Clear existing content
            while message_frame.layout().count():
                child = message_frame.layout().takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            formatted_message = self.format_message(message)
            for part_type, part_content in formatted_message:
                self.add_content_to_card(message_frame.layout(), part_type, part_content, from_user)
        else:
            # If there's already content, update the first label
            existing_label = message_frame.layout().itemAt(0).widget()
            if isinstance(existing_label, QLabel):
                existing_label.setText(message)

        self.repaint()
        self.log.debug(f"Card {card_id} updated successfully. New content: {message}")


    def format_message(self, message):
        # Split the message into parts (regular text, code blocks, and URLs)
        parts = re.split(r'(```[\s\S]*?```|https?://\S+)', message)

        formatted_parts = []
        for part in parts:
            part = part.strip()
            if part.startswith('```') and part.endswith('```'):
                # This is a code block
                if '\n' in part:
                    # Multi-line code block
                    code = part.split('\n', 1)[1].rsplit('\n', 1)[0]
                else:
                    # Single-line code block
                    code = part[3:-3]  # Remove ``` from start and end
                formatted_parts.append(('code', code))
            # Found a url link
            elif part.startswith('http://') or part.startswith('https://'):
                youtube_pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=)?([^\s&]+)'
                youtube_match = re.match(youtube_pattern, part)
                if youtube_match:
                    # This is a YouTube link
                    formatted_parts.append(('youtube', youtube_match.group(1)))
                else:
                    # This is a general webpage link
                    formatted_parts.append(('webpage', part))
            else:
                # This is regular text
                if part:
                    formatted_parts.append(('text', part))

        return formatted_parts


    def add_content_to_card(self, card_layout, content_type, content, from_user):
        if content_type == 'text':
            label = QLabel(content)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if from_user:
                self.apply_user_style(label)
            else:
                self.apply_assistant_style(label)
            card_layout.addWidget(label)

        elif content_type == 'code':
            code_label = QLabel(content)
            code_label.setWordWrap(True)
            code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.apply_code_style(code_label)
            card_layout.addWidget(code_label)

        elif content_type == 'youtube':
            preview_frame = QFrame()
            preview_layout = QVBoxLayout(preview_frame)
            self.create_youtube_preview(preview_layout, content)
            card_layout.addWidget(preview_frame)

        elif content_type == 'webpage':
            preview_frame = QFrame()
            preview_layout = QVBoxLayout(preview_frame)
            self.create_webpage_preview(preview_layout, content)
            card_layout.addWidget(preview_frame)

        else:
            self.log.error(f"Unknown content type: {content_type}")


    def apply_user_style(self, message_frame):
        message_frame.setStyleSheet(
            """
            font-size: 12pt;
            border-radius: 3px;
            border: 1px solid rgb(0, 0, 0);
            background-color:rgb(77, 77, 77);
            color: rgb(255, 255, 255);
            padding: 8px 8px;
            text-align: left;
            """
        )


    def apply_code_style(self, message_frame):
        message_frame.setStyleSheet(
            """
            font-family: 'Courier New', monospace;
            font-size: 11pt;
                border: 1px solid #2C2C2C;
                border-radius: 3px;
                background-color: #1E1E1E;
                color: #D4D4D4;
                padding: 8px;
            }
        """)


    def apply_assistant_style(self, message_frame):
        message_frame.setStyleSheet(
            """
            font-size: 12pt;
            border-radius: 3px;
            border: 1px solid #0A819A;
            background-color: #0A819A;
            color: rgb(255, 255, 255);
            padding: 8px 8px;
            text-align: left;
            """
        )

    def create_youtube_preview(self, layout, video_id):
        self.log.debug(f"Creating YouTube preview for video ID: {video_id}")

        outer_frame = QFrame()
        outer_frame.setObjectName("youtubePreviewFrame")
        outer_frame.setStyleSheet("""
            #youtubePreviewFrame {
                border: 1px solid #cccccc;
                border-radius: 5px;
                background-color: #f0f0f0;
                padding: 10px;
            }
        """)
        outer_layout = QVBoxLayout(outer_frame)

        thumbnail_label = QLabel("Loading thumbnail...")
        thumbnail_label.setFixedSize(320, 180)
        thumbnail_label.setAlignment(Qt.AlignCenter)
        thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #e0e0e0;
                border: 1px solid #b0b0b0;
            }
        """)
        outer_layout.addWidget(thumbnail_label)

        open_button = QPushButton("Watch on YouTube")
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://www.youtube.com/watch?v={video_id}")))
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 3px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        outer_layout.addWidget(open_button)  # Add the button to the layout

        layout.addWidget(outer_frame)

        # Fetch the thumbnail
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/0.jpg"
        self.log.debug(f"Fetching thumbnail from URL: {thumbnail_url}")
        request = QNetworkRequest(QUrl(thumbnail_url))
        reply = self.network_manager.get(request)
        reply.setProperty("thumbnail_label", thumbnail_label)


    @Slot(QNetworkReply)
    def on_network_request_finished(self, reply):
        self.log.debug("Network request finished")
        error = reply.error()
        if error == QNetworkReply.NoError:
            self.log.debug("Network request successful")
            thumbnail_label = reply.property("thumbnail_label")
            if thumbnail_label:
                data = reply.readAll()
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    self.log.debug("Thumbnail loaded successfully")
                    thumbnail_label.setPixmap(pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    self.log.error("Failed to create pixmap from downloaded data")
                    thumbnail_label.setText("Failed to load thumbnail")
            else:
                self.log.error("Thumbnail label not found in reply properties")
        else:
            self.log.error(f"Network request error: {reply.errorString()}")
            thumbnail_label = reply.property("thumbnail_label")
            if thumbnail_label:
                thumbnail_label.setText(f"Error: {reply.errorString()}")

        reply.deleteLater()


    def create_webpage_preview(self, layout, url):
        self.log.debug(f"Creating webpage preview for URL: {url}")

        outer_frame = QFrame()
        outer_frame.setObjectName("webpagePreviewFrame")
        outer_frame.setStyleSheet("""
            #webpagePreviewFrame {
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                background-color: #f8f8f8;
                padding: 8px;
            }
        """)
        outer_layout = QVBoxLayout(outer_frame)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(8)

        # Create QWebEngineView for web browsing
        web_view = QWebEngineView()
        web_view.setFixedSize(400, 300)  # Adjust size as needed

        # Create a custom page with error handling
        custom_page = CustomWebPage(web_view)
        web_view.setPage(custom_page)

        # Create a progress bar
        progress_bar = QProgressBar()
        progress_bar.setTextVisible(False)
        progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #d0d0d0;
                border-radius: 2px;
                background-color: #f0f0f0;
                height: 5px;
            }
            QProgressBar::chunk {
                background-color: #0066cc;
            }
        """)
        outer_layout.addWidget(progress_bar)

        # Connect signals
        web_view.loadStarted.connect(lambda: progress_bar.setVisible(True))
        web_view.loadProgress.connect(progress_bar.setValue)
        web_view.loadFinished.connect(lambda: progress_bar.setVisible(False))

        outer_layout.addWidget(web_view)

        # Load the URL
        web_view.setUrl(QUrl(url))

        # Add URL label
        url_label = QLabel(url)
        url_label.setWordWrap(True)
        url_label.setStyleSheet("""
            QLabel {
                color: #333333;
                font-size: 13px;
            }
        """)
        outer_layout.addWidget(url_label)

        # Create button layout
        button_layout = QHBoxLayout()

        # Add "Open in Browser" button
        open_button = QPushButton("Open in Browser")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 3px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        button_layout.addWidget(open_button)

        # Add "Refresh" button
        refresh_button = QPushButton("Refresh")
        refresh_button.setCursor(Qt.PointingHandCursor)
        refresh_button.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 3px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        refresh_button.clicked.connect(web_view.reload)
        button_layout.addWidget(refresh_button)

        outer_layout.addLayout(button_layout)

        layout.addWidget(outer_frame)


    def scrollToBottom(
        self, minVal=None, maxVal=None  # pylint: disable=unused-argument
    ):
        # Additional params 'minVal' and 'maxVal' are declared because
        # rangeChanged signal sends them, but we set it to optional
        self.ui.scrollArea.verticalScrollBar().setValue(
            self.ui.scrollArea.verticalScrollBar().maximum()
        )

class CustomWebPage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self.log = logging.getLogger(__name__)

        # Enable JavaScript
        self.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)

    def javaScriptConsoleMessage(self, message, lineNumber, sourceID):
        self.log.debug(f"JS Console: {message} (line {lineNumber}, source: {sourceID})")

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if _type == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, _type, isMainFrame)

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
        content_frame.setStyleSheet("""
            #contentFrame {
                background-color: #1e1e1e;
                border-radius: 10px;
                border: 1px solid #333333;
            }
        """)

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
        title_label.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
            color: #ffffff;
        """)

        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Enter your token here")
        self.token_input.setStyleSheet("""
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
        """)

        button_layout = QHBoxLayout()
        self.verify_button = QPushButton("Verify")
        self.submit_button = QPushButton("Submit")
        self.cancel_button = QPushButton("Cancel")

        for button in [self.verify_button, self.submit_button, self.cancel_button]:
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet("""
                QPushButton {
                    padding: 10px 20px;
                    border-radius: 5px;
                    font-weight: bold;
                    color: #ffffff;
                    background-color: #0078d4;
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
            """)

        self.submit_button.setEnabled(False)

        button_layout.addWidget(self.verify_button)
        button_layout.addWidget(self.submit_button)
        button_layout.addWidget(self.cancel_button)

        content_layout.addWidget(title_label)
        content_layout.addWidget(self.token_input)
        content_layout.addLayout(button_layout)

        main_layout.addWidget(content_frame)

        self.verify_button.clicked.connect(self.verify_token)
        self.submit_button.clicked.connect(self.submit_token)
        self.cancel_button.clicked.connect(self.close)
        self.token_input.textChanged.connect(self.on_token_changed)

    def verify_token(self):
        token = self.token_input.text()
        if self.is_token_valid(token):
            QMessageBox.information(self, "SUCCESS", "Token verified successfully!")
            self.token_verified = True
            self.submit_button.setEnabled(True)
        else:
            QMessageBox.warning(self, "ERROR", "Invalid token. Please check and try again.")
            self.token_verified = False
            self.submit_button.setEnabled(False)

    def is_token_valid(self, token):
        try:
            api = HfApi()
            api.whoami(token)
            return True
        except Exception as e: #pylint: disable=W0718
            self.log.error(e)
            return False

    def submit_token(self):
        if not self.token_verified:
            QMessageBox.warning(self, "Warning", "Please verify the token before submitting.")
            return

        self.token = self.token_input.text()
        if self.token:
            os.environ["HUGGINGFACE_TOKEN"] = self.token
            HfFolder.save_token(self.token)
            QMessageBox.information(self, "SUCCESS", "Token saved successfully!")
            self.close()
        else:
            QMessageBox.warning(self, "ERROR", "Please enter a valid token.")

    def on_token_changed(self):
        self.token_verified = False
        self.submit_button.setEnabled(False)

def get_huggingface_token():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

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

def main():
    from gaia.llm.download_nltk_data import download_nltk_data
    download_nltk_data()

    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(r":/img\gaia.ico"))
    widget = Widget()
    widget.show()
    sys.exit(app.exec())

if __name__ == "__main__":

    main()
