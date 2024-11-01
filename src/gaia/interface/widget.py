# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# This Python file uses the following encoding: utf-8
import sys
import os
import re
import time
import socket
import json
import asyncio
from pathlib import Path
from datetime import datetime
import textwrap
import subprocess
import multiprocessing
from urllib.parse import urlparse
from aiohttp import web, ClientSession

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QSpacerItem,
    QSizePolicy,
    QProgressBar,
    QComboBox,
)
from PySide6.QtCore import Qt, QUrl, QEvent, QSize, QObject, Signal, Slot, QThread
from PySide6.QtGui import QPixmap, QDesktopServices, QMovie, QIcon
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from gaia.logger import get_logger
import gaia.agents as agents
from gaia.interface.util import UIMessage
from gaia.interface.ui_form import Ui_Widget
from gaia.llm.server import launch_llm_server

# Conditional import for Ollama
try:
    from gaia.llm.ollama_server import launch_ollama_client_server, launch_ollama_model_server
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    launch_ollama_client_server = None
    launch_ollama_model_server = None

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
        self.log = get_logger(__name__)

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
        self.log.debug("SetupLLM do_work started")
        self.widget.terminate_servers()

        # Switch visibility of UI elements
        self.widget.ui.loading.setVisible(True)
        self.widget.ui.loading.setVisible(True)
        self.widget.ui.loadingLabel.setVisible(True)
        self.widget.ui.loadingGif.setVisible(True)
        self.widget.ui.ask.setEnabled(False)
        self.widget.ui.model.setEnabled(False)
        self.widget.ui.device.setEnabled(False)
        self.widget.ui.agent.setEnabled(False)

        if self.widget.settings["llm_server"]:
            self.initialize_servers()
        else:
            self.log.debug("Skipping initilize_servers()")

        selected_model = self.widget.ui.model.currentText()
        self.widget.ui.loadingLabel.setText(
            f"Ready to run {selected_model} on {self.widget.ui.device.currentText()}!"
        )
        self.widget.ui.loadingGif.setVisible(False)

        self.widget.ui.ask.setEnabled(True)
        self.widget.ui.model.setEnabled(True)
        self.widget.ui.device.setEnabled(True)
        self.widget.ui.agent.setEnabled(True)

        # asyncio.run(self.widget.request_restart())

        self.log.debug("SetupLLM do_work finished")
        self.finished.emit()


    def initialize_servers(self):
        _, model_settings, _, _, _ = self.get_model_settings()

        # Initialize Agent server
        self.initialize_agent_server()

        if model_settings["backend"] == "ollama":
            if OLLAMA_AVAILABLE:
                # Initialize Ollama servers
                self.initialize_ollama_model_server()
                self.initialize_ollama_client_server()
            else:
                error_message = "Ollama backend selected but Ollama is not available."
                UIMessage.error(error_message)
        else:
            # Initialize LLM server
            self.widget.ui.loadingLabel.setText(f"Initializing LLM server: {self.widget.ui.device.currentText()}...")
            self.initialize_llm_server()


    def initialize_agent_server(self):
        # Get model settings to access the checkpoint
        selected_model = self.widget.ui.model.currentText()
        model_settings = self.widget.settings["models"][selected_model]
        checkpoint = model_settings["checkpoint"]

        # Open using subprocess or multiprocessing depending on the selected settings
        selected_agent = self.widget.ui.agent.currentText()
        self.log.info(f"Starting Agent {selected_agent} server...")
        self.widget.ui.loadingLabel.setText(f"Initializing Agent {selected_agent} Server...")

        if self.widget.settings["dev_mode"]:
            app_dot_py = gaia_folder / "agents" / selected_agent.lower() / "app.py"
            command = [sys.executable, str(app_dot_py), "--model", checkpoint]
            self.widget.agent_server = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            agent_module = getattr(agents, selected_agent.lower())
            agent_class = getattr(agent_module, selected_agent)
            self.widget.agent_server = multiprocessing.Process(
                target=agent_class,
                kwargs={
                    "model": checkpoint,
                    "host": "127.0.0.1",
                    "port": 8001
                }
            )
            self.widget.agent_server.start()

        host="127.0.0.1"
        port=8001
        self.check_server_available(host, port)
        self.log.info("Done.")


    def initialize_llm_server(self):
        _, model_settings, selected_device, selected_dtype, max_new_tokens = self.get_model_settings()
        llm_server_kwargs = {
            "backend" : model_settings["backend"],
            "checkpoint": model_settings["checkpoint"],
            "max_new_tokens": max_new_tokens,
            "device": selected_device,
            "dtype": selected_dtype,
        }

        self.log.info(f"Starting LLM server with params: {llm_server_kwargs}...")
        if self.widget.settings["dev_mode"]:
            server_dot_py = gaia_folder / "llm" / "server.py"
            command = [
                sys.executable,
                server_dot_py,
            ] + sum(
                ([f"--{key}", str(value)] for key, value in llm_server_kwargs.items()),
                [],
            )
            self.widget.llm_server = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self.check_server_available("127.0.0.1", 8000)
        else:
            if self.widget.settings["llm_server"]:
                self.widget.llm_server = multiprocessing.Process(
                    target=launch_llm_server, kwargs=llm_server_kwargs
                )
                self.widget.llm_server.start()
                self.check_server_available("127.0.0.1", 8000)
        asyncio.run(self.request_llm_load())
        self.log.info("Done.")


    def initialize_ollama_model_server(self):
        if not OLLAMA_AVAILABLE:
            self.log.warning("Ollama is not available. Skipping Ollama model server initialization.")
            return

        self.log.info("Initializing Ollama model server...")
        self.widget.ui.loadingLabel.setText("Initializing Ollama model server...")

        host = "http://localhost"
        port = 11434

        if self.widget.settings["dev_mode"]:
            # Construct the command to run launch_ollama_model_server in a separate shell
            command = [
                sys.executable,
                "-c",
                f"from gaia.llm.ollama_server import launch_ollama_model_server; launch_ollama_model_server(host='{host}', port={port})"
            ]
            self.widget.ollama_model_server = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            self.widget.ollama_model_server = multiprocessing.Process(
                target=launch_ollama_model_server,
                kwargs={"host": host, "port": port}
            )
            self.widget.ollama_model_server.start()

        self.check_server_available(host, port)
        self.log.info("Done.")


    def initialize_ollama_client_server(self):
        if not OLLAMA_AVAILABLE:
            self.log.warning("Ollama is not available. Skipping Ollama client server initialization.")
            return

        _, model_settings, device, _, _ = self.get_model_settings()
        checkpoint = model_settings["checkpoint"]

        self.log.info(f"Initializing Ollama client server on {device} with {checkpoint} model...")
        self.widget.ui.loadingLabel.setText(f"Initializing Ollama client server on {device} with {checkpoint} model...")

        host = "http://localhost"
        port = 8000
        ollama_kwargs = {
            "model" : checkpoint,
            "host" : host,
            "port" : port
        }

        if self.widget.settings["dev_mode"]:
            command = [
                sys.executable,
                "-c",
                f"from gaia.llm.ollama_server import launch_ollama_client_server; launch_ollama_client_server(model='{checkpoint}', host='{host}', port={port})"
            ]

            self.widget.ollama_client_server = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            self.widget.ollama_client_server = multiprocessing.Process(
                target=launch_ollama_client_server, kwargs=ollama_kwargs
            )
            self.widget.ollama_client_server.start()

        self.check_server_available(host, port)


    def get_model_settings(self):
        selected_model = self.widget.ui.model.currentText()
        selected_device_dtype = self.widget.ui.device.currentText()

        try:
            selected_device, selected_dtype = self.widget.device_list_mapping[selected_device_dtype]
        except KeyError:
            self.log.error(f"Device '{selected_device_dtype}' not found in device_list_mapping. Available devices: {self.widget.device_list_mapping}")

        model_settings = self.widget.settings["models"][selected_model]
        max_new_tokens = int(self.widget.settings["max_new_tokens"])

        return selected_model, model_settings, selected_device.lower(), selected_dtype.lower(), max_new_tokens


    def check_server_available(self, host, port, timeout=3000, check_interval=5):
        # Parse the host to remove any protocol
        parsed_host = urlparse(host)
        clean_host = parsed_host.netloc or parsed_host.path

        start_time = time.time()
        attempts = 0

        while time.time() - start_time < timeout:
            if self.is_server_available(clean_host, port):
                self.log.info(f"Server available at {host}:{port} after {attempts} attempts")
                return True

            attempts += 1
            elapsed_time = time.time() - start_time
            self.log.info(f"Waiting for server at {host}:{port}... (Attempt {attempts}, Elapsed time: {elapsed_time:.1f}s)")

            # Update the loading label with the current status
            self.widget.ui.loadingLabel.setText(f"Waiting for server... ({int(elapsed_time)}s)")
            QApplication.processEvents()  # Ensure the UI updates

            time.sleep(check_interval)

        UIMessage.error(f"Server unavailable at {host}:{port} after {timeout} seconds")
        return False


    def is_server_available(self, host, port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            s.close()
            return True
        except (ConnectionRefusedError, TimeoutError):
            return False


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


class Widget(QWidget):
    def __init__(self, parent=None, server=True):
        super().__init__(parent)

        # control enabling of web server
        self.server = server
        self.is_restarting = False
        self.log = get_logger(__name__)
        self.current_backend = None

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
        self.setStyleSheet("""
            QWidget {
                background-color: black;
                border: none;
            }
            QFrame {
                border: none;
            }
        """)
        self.setWindowTitle("RyzenAI GAIA")

        # Set a much wider minimum width for the chat area
        self.ui.scrollAreaWidgetContents.setMinimumWidth(800)

        self.card_count = 0
        self.cards = {}
        self.agent_server = None
        self.llm_server = None
        self.ollama_model_server = None
        self.ollama_client_server = None
        self.device_list_mapping = {}

        # Adjust the width based on the content
        self.ui.model.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.ui.model.setMinimumContentsLength(20)  # Adjust this value if needed

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
        self.ui.restart.clicked.connect(self.restart_conversation)
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


    def closeEvent(self, event):
        self.terminate_servers()
        super().closeEvent(event)

    def terminate_servers(self):
        # Make sure servers are killed when application exits
        self.log.info("Terminating servers.")
        if self.agent_server is not None:
            self.log.debug("Closing agent server")
            try:
                self.agent_server.terminate()
            except AttributeError:
                self.log.warning("Agent server was already terminated or not initialized.")
            self.agent_server = None

        if self.llm_server is not None:
            self.log.debug("Closing LLM server")
            try:
                self.llm_server.terminate()
            except AttributeError:
                self.log.warning("LLM server was already terminated or not initialized.")
            self.llm_server = None

        if OLLAMA_AVAILABLE:
            if self.ollama_model_server is not None:
                self.log.debug("Closing Ollama model server")
                try:
                    self.ollama_model_server.terminate()
                except AttributeError:
                    self.log.warning("Ollama model server was already terminated or not initialized.")
                self.ollama_model_server = None

            if self.ollama_client_server is not None:
                self.log.debug("Closing Ollama client server")
                try:
                    self.ollama_client_server.terminate()
                except AttributeError:
                    self.log.warning("Ollama client server was already terminated or not initialized.")
                self.ollama_client_server = None


    def update_device_list(self):
        self.log.debug("update_device_list called")
        selected_model = self.ui.model.currentText()
        model_settings = self.settings["models"][selected_model]
        model_device_settings = model_settings["device"]
        self.current_backend = model_settings["backend"]

        # Safely disconnect the signal
        self.ui.device.currentIndexChanged.disconnect(self.deployment_changed)

        # Clear existing items
        self.ui.device.clear()
        self.device_list_mapping.clear()

        for device in model_device_settings:
            for dtype in model_device_settings[device]:
                device_dtype_text = f"{device} ({dtype})"
                self.ui.device.addItem(device_dtype_text)
                self.device_list_mapping[device_dtype_text] = (device, dtype)

        # Set the current index to 0 if there are items in the combo box
        if self.ui.device.count() > 0:
            self.ui.device.setCurrentIndex(0)

        # Reconnect the signal
        self.ui.device.currentIndexChanged.connect(self.deployment_changed)

        # Log the updated device_list_mapping for debugging
        self.log.debug(f"Updated device_list_mapping: {self.device_list_mapping}")
        self.log.debug(f"Current device text: {self.ui.device.currentText()}")
        self.log.debug(f"Number of items in device combo box: {self.ui.device.count()}")


    def deployment_changed(self):
        self.log.debug("deployment_changed called")
        if self.is_restarting:
            self.log.debug("Skipping deployment_changed as restart is already in progress")
            return

        self.is_restarting = True
        try:
            # Show loading screen immediately
            self.make_chat_visible(False)
            self.ui.loadingLabel.setVisible(True)
            self.ui.loading.setVisible(True)
            self.ui.loadingGif.setVisible(True)

            # Update the device list before restarting the conversation
            self.update_device_list()
            self.restart_conversation()

            selected_model = self.ui.model.currentText()
            model_settings = self.settings["models"][selected_model]
            self.current_backend = model_settings["backend"]

            # Update the loading label
            self.ui.loadingLabel.setText(f"Switching to {selected_model}...")
            self.ui.loadingLabel.setVisible(True)

            if self.server:
                self.log.debug("Starting setup thread")
                self.setupThread.quit()
                self.setupThread.wait()
                self.setupThread.start()

            # Call request_restart after starting the setup thread
            asyncio.run(self.request_restart())

        finally:
            self.is_restarting = False


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


    # Request LLM server to also restart
    async def request_restart(self):
        self.log.debug("request_restart called")
        if self.server:
            async with ClientSession() as session:
                async with session.post(
                    "http://127.0.0.1:8001/restart", json={}
                ) as response:
                    await response.read()


    def restart_conversation(self):
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
        # self.log.debug(f"add_card called with card_id: {card_id}")
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

        # self.log.debug(f"Card added with id: {card_id}, content: {message}")
        return card_id


    def update_card(self, message, card_id, stats=None, final_update=False, from_user=False):
        # self.log.debug(f"update_card called with message: {message}, card_id: {card_id}, final_update: {final_update}")

        if card_id not in self.cards:
            self.log.warning(f"Card with id {card_id} not found. Creating a new card.")
            new_card_id = self.add_card(message, card_id, from_user, stats)
            if new_card_id != card_id:
                self.log.error(f"Failed to create card with id {card_id}. New card created with id {new_card_id}")
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
        # self.log.debug(f"Card {card_id} updated successfully. New content: {message}")


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
                youtube_pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=)?([^\s&\.]+)'
                youtube_match = re.match(youtube_pattern, part)
                if youtube_match:
                    # This is a YouTube link
                    video_id = youtube_match.group(1).strip("'")  # Remove any trailing single quotes
                    formatted_parts.append(('youtube', video_id))
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

        # Remove any extra characters from the video_id
        video_id = video_id.strip("'")

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
        outer_layout.addWidget(open_button)

        layout.addWidget(outer_frame)

        # Fetch the thumbnail
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/0.jpg"
        self.log.debug(f"Fetching thumbnail from URL: {thumbnail_url}")
        request = QNetworkRequest(QUrl(thumbnail_url))
        reply = self.network_manager.get(request)
        reply.setProperty("thumbnail_label", thumbnail_label)
        reply.setProperty("video_id", video_id)

    @Slot(QNetworkReply)
    def on_network_request_finished(self, reply):
        self.log.debug("Network request finished")
        error = reply.error()
        if error == QNetworkReply.NoError:
            self.log.debug("Network request successful")
            thumbnail_label = reply.property("thumbnail_label")
            video_id = reply.property("video_id")
            if thumbnail_label:
                data = reply.readAll()
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    self.log.debug(f"Thumbnail loaded successfully for video ID: {video_id}")
                    scaled_pixmap = pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    thumbnail_label.setPixmap(scaled_pixmap)
                    if scaled_pixmap.isNull():
                        self.log.error("Scaled pixmap is null")
                        thumbnail_label.setText("Failed to scale thumbnail")
                else:
                    self.log.error(f"Failed to create pixmap from downloaded data for video ID: {video_id}")
                    thumbnail_label.setText("Failed to load thumbnail")
            else:
                self.log.error("Thumbnail label not found in reply properties")
        else:
            self.log.error(f"Network request error: {reply.errorString()}")
            thumbnail_label = reply.property("thumbnail_label")
            video_id = reply.property("video_id")
            if thumbnail_label:
                thumbnail_label.setText("Error: Unable to load thumbnail")
            self.log.error(f"Failed to load thumbnail for video ID: {video_id}")

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
        self.log = get_logger(__name__)

        # Enable JavaScript
        self.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)

    def javaScriptConsoleMessage(self, message, lineNumber, sourceID):
        self.log.debug(f"JS Console: {message} (line {lineNumber}, source: {sourceID})")

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if _type == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, _type, isMainFrame)

def main():
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(r":/img\gaia.ico"))
    widget = Widget()
    widget.show()
    sys.exit(app.exec())

if __name__ == "__main__":

    main()
