# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# This Python file uses the following encoding: utf-8
import sys
import os
import re
import time
import json
import asyncio
from threading import Lock
from pathlib import Path
from datetime import datetime
import textwrap
import subprocess
import multiprocessing
from urllib.parse import urlparse
from aiohttp import web, ClientSession
import requests

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
    QMessageBox,
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
    from gaia.llm.ollama_server import (
        launch_ollama_client_server,
        launch_ollama_model_server,
    )

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
    cancelled = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        self.log = get_logger(__name__)
        self._is_cancelled = False
        self._lock = Lock()
        self._workers_lock = Lock()
        self.server_check_workers = []

    def cancel(self):
        """Signal that the operation should be cancelled."""
        with self._lock:
            self._is_cancelled = True

        with self._workers_lock:
            workers = (
                self.server_check_workers.copy()
            )  # Create a copy to iterate safely

        for worker in workers:
            if isinstance(worker, ServerCheckWorker):
                worker.cancel()
                worker.wait()
            else:
                self.log.error(f"Unexpected object in server_check_workers: {worker}")

        self.cancelled.emit()

    def is_cancelled(self):
        """Thread-safe way to check cancellation status"""
        with self._lock:
            return self._is_cancelled

    def on_server_check_complete(self, is_ready):
        if not is_ready and not self._is_cancelled:
            self.log.error("Server failed to become available")
            self.widget.ui.loadingLabel.setText("Error: Server failed to initialize")

    @Slot()
    def do_work(self):
        self.log.debug("SetupLLM do_work started")
        self._is_cancelled = False
        self.widget.terminate_servers()

        # Switch visibility of UI elements
        self.widget.ui.loading.setVisible(True)
        self.widget.ui.loadingLabel.setVisible(True)
        self.widget.ui.loadingGif.setVisible(True)
        self.widget.ui.cancel.setVisible(True)
        self.widget.ui.ask.setEnabled(False)
        self.widget.ui.model.setEnabled(False)
        self.widget.ui.device.setEnabled(False)
        self.widget.ui.agent.setEnabled(False)

        if self.widget.settings["llm_server"]:
            try:
                self.initialize_servers()
                if self._is_cancelled:
                    return

                # Check all servers asynchronously
                workers = []

                # Check agent server
                workers.append(
                    self.check_server_available("127.0.0.1", self.widget.agent_port)
                )

                # Check LLM server
                workers.append(
                    self.check_server_available("127.0.0.1", self.widget.llm_port)
                )

                # Check Ollama server if needed
                selected_model = self.widget.ui.model.currentText()
                model_settings = self.widget.settings["models"][selected_model]
                if model_settings["backend"] == "ollama" and OLLAMA_AVAILABLE:
                    workers.append(
                        self.check_server_available(
                            "127.0.0.1",
                            self.widget.ollama_port,
                            endpoint="/api/version",
                        )
                    )

                # Wait for all workers to complete
                for worker in workers:
                    if isinstance(worker, ServerCheckWorker):
                        worker.wait()
                    else:
                        self.log.error(
                            f"Unexpected object in server_check_workers: {worker}"
                        )
                    if self._is_cancelled:
                        return

                if not self._is_cancelled:
                    self.widget.ui.loadingLabel.setText(
                        f"Ready to run {selected_model} on {self.widget.ui.device.currentText()}!"
                    )
                    # Request welcome message from the new agent instead of chat_restart
                    asyncio.run(self.widget.request_welcome_message())

            except Exception as e:
                if not self._is_cancelled:
                    self.log.error(f"Error during setup: {str(e)}")
                return
        else:
            self.log.debug("Skipping initialize_servers()")

        if not self._is_cancelled:
            self.widget.ui.loadingGif.setVisible(False)
            self.widget.ui.cancel.setVisible(False)
            self.widget.ui.ask.setEnabled(True)
            self.widget.ui.model.setEnabled(True)
            self.widget.ui.device.setEnabled(True)
            self.widget.ui.agent.setEnabled(True)

            # Add call to chat_restarted via agent server
            if self.widget.settings["llm_server"]:
                asyncio.run(self.request_chat_restart())

        self.log.debug("SetupLLM do_work finished")
        self.finished.emit()

    async def request_chat_restart(self):
        """Request Agent Server to restart chat"""
        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{self.widget.agent_port}/restart",
                    json={},
                ) as response:
                    await response.read()
        except Exception as e:
            self.log.error(f"Failed to request chat restart: {str(e)}")

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
            self.widget.ui.loadingLabel.setText(
                f"Initializing LLM server for {self.widget.ui.model.currentText()}..."
            )
            self.initialize_llm_server()

    def initialize_agent_server(self):
        # Get model settings to access the checkpoint
        selected_model = self.widget.ui.model.currentText()
        model_settings = self.widget.settings["models"][selected_model]
        checkpoint = model_settings["checkpoint"]

        # Convert "No Agent" back to "Llm" for internal use
        selected_agent = (
            "Llm"
            if self.widget.ui.agent.currentText() == "No Agent"
            else self.widget.ui.agent.currentText()
        )

        self.log.info(f"Starting Agent {selected_agent} server...")
        self.widget.ui.loadingLabel.setText(
            f"Initializing Agent {selected_agent} Server..."
        )

        if self.widget.settings["dev_mode"]:
            app_dot_py = gaia_folder / "agents" / selected_agent.lower() / "app.py"
            command = [sys.executable, str(app_dot_py), "--model", checkpoint]
            self.widget.agent_server = subprocess.Popen(
                command, creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            agent_class = getattr(agents, selected_agent.lower())
            self.widget.agent_server = multiprocessing.Process(
                target=agent_class,
                kwargs={
                    "model": checkpoint,
                    "host": "127.0.0.1",
                    "port": self.widget.agent_port,
                },
            )
            self.widget.agent_server.start()

        host = "127.0.0.1"
        port = self.widget.agent_port
        self.check_server_available(host, port)
        self.log.info("Done.")

    def initialize_llm_server(self):
        _, model_settings, selected_device, selected_dtype, max_new_tokens = (
            self.get_model_settings()
        )
        llm_server_kwargs = {
            "backend": model_settings["backend"],
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
                command, creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self.check_server_available("127.0.0.1", self.widget.llm_port)
        else:
            if self.widget.settings["llm_server"]:
                self.widget.llm_server = multiprocessing.Process(
                    target=launch_llm_server, kwargs=llm_server_kwargs
                )
                self.widget.llm_server.start()
                self.check_server_available("127.0.0.1", self.widget.llm_port)
        asyncio.run(self.request_llm_load())
        self.log.info("Done.")

    def initialize_ollama_model_server(self):
        if not OLLAMA_AVAILABLE:
            self.log.warning(
                "Ollama is not available. Skipping Ollama model server initialization."
            )
            return

        self.log.info("Initializing Ollama model server...")
        self.widget.ui.loadingLabel.setText("Initializing Ollama model server...")

        host = "http://localhost"
        port = self.widget.ollama_port

        if self.widget.settings["dev_mode"]:
            # Construct the command to run launch_ollama_model_server in a separate shell
            command = [
                sys.executable,
                "-c",
                f"from gaia.llm.ollama_server import launch_ollama_model_server; launch_ollama_model_server(host='{host}', port={port})",
            ]
            self.widget.ollama_model_server = subprocess.Popen(
                command, creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            self.widget.ollama_model_server = multiprocessing.Process(
                target=launch_ollama_model_server, kwargs={"host": host, "port": port}
            )
            self.widget.ollama_model_server.start()

        # Check if Ollama model server is ready, note this server uses a different endpoint for health check.
        self.check_server_available(host, port, endpoint="/api/version")
        self.log.info("Done.")

    def initialize_ollama_client_server(self):
        if not OLLAMA_AVAILABLE:
            self.log.warning(
                "Ollama is not available. Skipping Ollama client server initialization."
            )
            return

        _, model_settings, device, _, _ = self.get_model_settings()
        checkpoint = model_settings["checkpoint"]

        self.log.info(
            f"Initializing Ollama client server on {device} with {checkpoint} model..."
        )
        self.widget.ui.loadingLabel.setText(
            f"Initializing Ollama client server on {device} with {checkpoint} model..."
        )

        host = "http://localhost"
        port = self.widget.llm_port
        ollama_kwargs = {"model": checkpoint, "host": host, "port": port}

        if self.widget.settings["dev_mode"]:
            # Create a Python script string that imports and calls the function
            script = (
                "from gaia.llm.ollama_server import launch_ollama_client_server; "
                f"launch_ollama_client_server(model='{checkpoint}', host='{host}', port={port})"
            )

            self.widget.ollama_client_server = subprocess.Popen(
                [sys.executable, "-c", script],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
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
            selected_device, selected_dtype = self.widget.device_list_mapping[
                selected_device_dtype
            ]
        except KeyError:
            self.log.error(
                f"Device '{selected_device_dtype}' not found in device_list_mapping. Available devices: {self.widget.device_list_mapping}"
            )

        model_settings = self.widget.settings["models"][selected_model]
        max_new_tokens = int(self.widget.settings["max_new_tokens"])

        return (
            selected_model,
            model_settings,
            selected_device.lower(),
            selected_dtype.lower(),
            max_new_tokens,
        )

    def check_server_available(
        self, host, port, endpoint="/health", timeout=3000, check_interval=1
    ):
        """Check if server is available with a longer timeout for model downloads."""
        self.log.info(f"Checking server availability at {host}:{port}{endpoint}...")

        # Parse the host to remove any protocol
        parsed_host = urlparse(host)
        clean_host = parsed_host.netloc or parsed_host.path

        start_time = time.time()
        attempts = 0

        while (
            time.time() - start_time < timeout and not self._is_cancelled
        ):  # Check cancellation in while condition
            try:
                if self.is_server_available(clean_host, port, endpoint):
                    self.log.info(
                        f"Server available at {host}:{port}{endpoint} after {attempts} attempts"
                    )
                    return True

                attempts += 1
                elapsed_time = time.time() - start_time
                self.log.info(
                    f"Waiting for server at {host}:{port}{endpoint}... (Attempt {attempts}, Elapsed time: {elapsed_time:.1f}s)"
                )

                # Use a shorter sleep interval and check cancellation more frequently
                for _ in range(
                    int(check_interval * 10)
                ):  # Split sleep into smaller chunks
                    if self._is_cancelled:
                        self.log.info("Server check cancelled during sleep")
                        return False
                    time.sleep(0.1)  # Sleep in 100ms intervals

            except Exception as e:
                self.log.error(f"Error checking server: {str(e)}")
                if self._is_cancelled:
                    return False

        if self._is_cancelled:
            self.log.info("Server check cancelled")
            return False
        elif not self._is_cancelled:
            UIMessage.error(
                f"Server unavailable at {host}:{port}{endpoint} after {timeout} seconds"
            )
        return False

    def is_server_available(self, host, port, endpoint="/health"):
        """Check if a server is available with a short timeout."""
        try:
            url = f"http://{host}:{port}{endpoint}"
            response = requests.get(
                url, timeout=1
            )  # Shorter timeout for quicker cancellation
            return response.status_code == 200
        except (requests.RequestException, ConnectionError):
            return False

    async def request_llm_load(self):
        """Request Agent Server to update connection to LLM Server"""
        async with ClientSession() as session:
            # Get the model settings
            selected_model = self.widget.ui.model.currentText()
            model_settings = self.widget.settings["models"][selected_model]
            checkpoint = model_settings["checkpoint"]

            async with session.post(
                f"http://127.0.0.1:{self.widget.agent_port}/load_llm",
                json={
                    "model": self.widget.ui.model.currentText(),
                    "checkpoint": checkpoint,
                },
            ) as response:
                # Wait for response from server
                response_data = await response.json()
                # Check if LLM has been successfully loaded
                if response_data.get("status") == "Success":
                    self.log.debug("LLM has been loaded successfully!")
                else:
                    self.log.error("Failed to load LLM.")


class StreamToAgent(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    async def prompt_llm(self, prompt):
        async with ClientSession() as session:
            # Enable stop button when starting generation
            self.widget.ui.stop.setEnabled(True)

            try:
                async with session.post(
                    f"http://127.0.0.1:{self.widget.agent_port}/prompt",
                    json={"prompt": prompt},
                ) as response:
                    await response.read()
            finally:
                # Disable stop button when generation is complete or fails
                self.widget.ui.stop.setEnabled(False)

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

        self.widget.ui.ask.setEnabled(True)
        self.widget.ui.restart.setEnabled(True)
        self.widget.ui.stop.setEnabled(False)
        self.widget.ui.cancel.setEnabled(True)
        self.widget.ui.model.setEnabled(True)
        self.widget.ui.device.setEnabled(False)
        self.widget.ui.agent.setEnabled(True)

        self.finished.emit()


class StreamFromAgent(QObject):
    finished = Signal()
    add_card = Signal(str, str, bool, dict)
    update_card = Signal(str, str, dict, bool, bool)

    def __init__(self, widget):
        super().__init__()
        self.log = get_logger(__name__)
        self.widget = widget
        self.app = web.Application()
        self.host = "127.0.0.1"
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
        final_update = data.get("last")
        is_llm_response = data.get("is_llm_response", False)

        stats = {}
        if final_update and is_llm_response:  # Only get stats for LLM responses
            stats = await self.get_stats()

        if new_card:
            self.complete_message = chunk
            self.agent_card_count += 1
            self.add_card.emit(
                self.complete_message, self.last_agent_card_id, False, stats
            )
        else:
            self.complete_message = self.complete_message + chunk
            self.update_card.emit(
                self.complete_message,
                self.last_agent_card_id,
                stats,
                final_update,
                False,
            )
        return web.json_response({"status": "Received"})

    @Slot()
    def do_work(self):
        web.run_app(self.app, host=self.host, port=self.widget.ui_port)
        self.finished.emit()

    async def get_stats(self):
        """Fetch statistics from the agent server."""
        try:
            async with ClientSession() as session:
                async with session.get("http://127.0.0.1:8000/stats") as response:
                    stats = await response.json()
                    if "decode_token_times" in stats:
                        del stats["decode_token_times"]
                    return stats
        except Exception as e:
            self.log.error(f"Failed to get stats from agent: {str(e)}")
            return {}


class Widget(QWidget):
    def __init__(self, parent=None, server=True):
        super().__init__(parent)

        # control enabling of web server
        self.server = server
        self.llm_port = 8000
        self.agent_port = 8001
        self.ui_port = 8002
        self.ollama_port = 11434
        self.is_restarting = False
        self.log = get_logger(__name__)
        self.current_backend = None
        self.switch_worker = None

        # Set size policy for the main widget
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.network_manager = QNetworkAccessManager(self)

        # Connect the finished signal to our custom slot
        self.network_manager.finished.connect(self.on_network_request_finished)

        # Add a dictionary to store supported preview types and their handlers
        self.preview_handlers = {
            "youtube": self.create_youtube_preview,
            "webpage": self.create_webpage_preview,
        }

        self.ui = Ui_Widget()
        self.ui.setupUi(self)
        self.content_layout = self.ui.boardLayout
        self.setStyleSheet(
            """
            QWidget {
                background-color: black;
                border: none;
            }
            QFrame {
                border: none;
            }
        """
        )
        self.setWindowTitle("Ryzen AI GAIA")

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

        # Populate available agents
        self.update_available_agents()

        # Connect buttons
        self.ui.ask.clicked.connect(self.send_message)
        self.ui.restart.clicked.connect(self.restart_conversation)
        self.ui.stop.clicked.connect(self.stop_generation)
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
        self.agentReceiveWorker.add_card.connect(self.add_card)
        self.agentReceiveWorker.update_card.connect(self.update_card)
        self.agentReceiveThread.start()

        # Initialize stop button as disabled
        self.ui.stop.setEnabled(False)

        # Connect cancel button to setupWorker's cancel method
        self.ui.cancel.clicked.connect(self.cancel_loading)
        if hasattr(self, "setupWorker"):
            self.setupWorker.cancelled.connect(self.terminate_servers)

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
                self.log.warning(
                    "Agent server was already terminated or not initialized."
                )
            self.agent_server = None
        if self.llm_server is not None:
            self.log.debug("Closing LLM server")
            try:
                self.llm_server.terminate()
            except AttributeError:
                self.log.warning(
                    "LLM server was already terminated or not initialized."
                )
            self.llm_server = None

        if OLLAMA_AVAILABLE:
            if self.ollama_model_server is not None:
                self.log.debug("Closing Ollama model server")
                try:
                    self.ollama_model_server.terminate()
                except AttributeError:
                    self.log.warning(
                        "Ollama model server was already terminated or not initialized."
                    )
                self.ollama_model_server = None

            if self.ollama_client_server is not None:
                self.log.debug("Closing Ollama client server")
                try:
                    self.ollama_client_server.terminate()
                except AttributeError:
                    self.log.warning(
                        "Ollama client server was already terminated or not initialized."
                    )
                self.ollama_client_server = None

    def update_device_list(self):
        """Update the device dropdown based on selected model."""
        self.log.debug("update_device_list called")
        selected_model = self.ui.model.currentText()
        model_settings = self.settings["models"][selected_model]
        model_device_settings = model_settings["device"]
        self.current_backend = model_settings["backend"]

        # Disconnect the signal temporarily to prevent recursive calls
        try:
            self.ui.device.currentIndexChanged.disconnect(self.deployment_changed)
        except (TypeError, RuntimeError):
            # Signal was not connected
            pass

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
            self.log.debug(
                "Skipping deployment_changed as restart is already in progress"
            )
            return

        self.is_restarting = True

        # Show loading screen and cancel button immediately
        self.ui.loading.setVisible(True)
        self.ui.loadingLabel.setVisible(True)
        self.ui.loadingGif.setVisible(True)
        self.ui.cancel.setVisible(True)
        self.ui.cancel.setEnabled(True)

        # Update loading label with initial message
        selected_model = self.ui.model.currentText()
        self.ui.loadingLabel.setText(f"Switching to {selected_model}...")

        # Disable UI elements
        self.ui.ask.setEnabled(False)
        self.ui.model.setEnabled(False)
        self.ui.device.setEnabled(False)
        self.ui.agent.setEnabled(False)

        # Force UI update
        QApplication.processEvents()

        # Create and start worker thread
        self.switch_worker = ModelSwitchWorker(self)
        self.switch_worker.finished.connect(self._on_switch_complete)
        self.switch_worker.error.connect(self._on_switch_error)
        self.switch_worker.start()

    def _on_switch_complete(self):
        self.is_restarting = False
        self.ui.loadingGif.setVisible(False)
        self.ui.cancel.setVisible(False)

        # Re-enable UI elements
        self.ui.ask.setEnabled(True)
        self.ui.model.setEnabled(True)
        self.ui.device.setEnabled(True)
        self.ui.agent.setEnabled(True)

        # Check if switch_worker exists before trying to delete it
        if hasattr(self, "switch_worker") and self.switch_worker is not None:
            self.switch_worker.deleteLater()
            self.switch_worker = None

    def _on_switch_error(self, error_msg):
        self.is_restarting = False
        self.log.error(f"Model switch failed: {error_msg}")
        self.ui.loadingLabel.setText("Error switching models!")
        self.ui.loadingGif.setVisible(False)
        self.ui.cancel.setVisible(False)  # Hide cancel button on error

        # Re-enable UI elements
        self.ui.ask.setEnabled(True)
        self.ui.model.setEnabled(True)
        self.ui.device.setEnabled(True)
        self.ui.agent.setEnabled(True)

        if hasattr(self, "switch_worker"):
            self.switch_worker.deleteLater()
        QMessageBox.critical(self, "Error", f"Failed to switch models: {error_msg}")

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
                and self.server  # Add check for server availability
            ):
                # Send message and consume the event, preventing return from being added to prompt box
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    # Request LLM server to also restart
    async def request_restart(self):
        self.log.debug("request_restart called")
        if self.server:
            try:
                async with ClientSession() as session:
                    async with session.post(
                        f"http://127.0.0.1:{self.agent_port}/restart",
                        json={},
                        timeout=5,  # Add timeout
                    ) as response:
                        await response.read()
            except Exception as e:
                self.log.warning(f"Failed to request restart: {str(e)}")

    # Add new method to request stop
    async def request_stop(self):
        self.log.debug(f"request_stop called on port: {self.llm_port}")
        if self.server:
            try:
                async with ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{self.llm_port}/halt",
                        json={},
                        timeout=5,
                    ) as response:
                        await response.read()
            except Exception as e:
                self.log.error(f"Failed to request stop: {str(e)}")

    def restart_conversation(self):
        """Reset the conversation state."""
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

        # Request agent to restart chat
        if self.server:
            asyncio.run(self.request_restart())

    def stop_generation(self):
        # Request agent to stop generation
        if self.server:
            asyncio.run(self.request_stop())

    def cancel_loading(self):
        self.log.debug("Cancelling loading process")

        # Cancel model switch worker if it exists and is still valid
        if hasattr(self, "switch_worker") and self.switch_worker is not None:
            self.switch_worker.cancel()
            if self.switch_worker.isRunning():
                self.switch_worker.wait()
            self.switch_worker = None

        # Cancel setup worker and all its server check workers
        if self.server and hasattr(self, "setupWorker"):
            self.setupWorker.cancel()
            if hasattr(self, "setupThread") and self.setupThread.isRunning():
                self.setupThread.quit()
                self.setupThread.wait()

        # Cancel agent send thread if running
        if hasattr(self, "agentSendThread") and self.agentSendThread.isRunning():
            self.agentSendThread.quit()
            self.agentSendThread.wait()

        # Terminate all servers
        self.terminate_servers()

        # Reset UI elements
        self.ui.loadingGif.setVisible(False)
        self.ui.loadingLabel.setText("Loading cancelled. Select a model to continue.")
        self.ui.cancel.setVisible(False)

        # Disable send button and keep other controls enabled
        self.ui.ask.setEnabled(False)
        self.ui.model.setEnabled(True)
        self.ui.device.setEnabled(True)
        self.ui.agent.setEnabled(True)

        # Reset restart flag
        self.is_restarting = False

    def send_message(self):
        prompt = self.ui.prompt.toPlainText()
        self.ui.prompt.clear()
        if prompt:
            # Disable send, restart buttons and dropdowns
            self.ui.ask.setEnabled(False)
            self.ui.restart.setEnabled(False)
            self.ui.stop.setEnabled(True)
            self.ui.model.setEnabled(False)
            self.ui.device.setEnabled(False)
            self.ui.agent.setEnabled(False)

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
            # Create the main card frame with fixed width
            card = QFrame()
            card.setFrameShape(QFrame.NoFrame)
            card.setFixedWidth(750)
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(5, 0, 5, 0)
            card_layout.setSpacing(0)

            # Create the card message frame with dynamic width for user messages
            card_message = QFrame()
            if from_user:
                card_message.setMaximumWidth(650)  # Set maximum width instead of fixed
                card_message.setSizePolicy(
                    QSizePolicy.Maximum, QSizePolicy.Preferred
                )  # Allow shrinking
            else:
                card_message.setFixedWidth(650)
            card_message_layout = QVBoxLayout(card_message)
            card_message_layout.setContentsMargins(5, 0, 5, 0)
            card_message_layout.setSpacing(0)

            # Create the message frame with dynamic width for user messages
            message_frame = QFrame()
            if from_user:
                message_frame.setMaximumWidth(640)  # Set maximum width instead of fixed
                message_frame.setSizePolicy(
                    QSizePolicy.Maximum, QSizePolicy.Preferred
                )  # Allow shrinking
            else:
                message_frame.setFixedWidth(640)
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
                label_text += "   ".join(
                    f"{key}: {self._format_value(val)}"
                    for key, val in stats.items()
                    if val is not None
                )
            label = QLabel(label_text)
            label.setVisible(self.settings["show_label"])
            label.setStyleSheet("color: rgb(255, 255, 255);")

            card_message_layout.addWidget(message_frame)
            card_message_layout.addWidget(label)

            # Add the card message layout to the card
            spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
            if from_user:
                card.setLayoutDirection(Qt.RightToLeft)
                card_layout.addWidget(card_message)
                card_layout.addItem(spacer)
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

    def update_card(
        self, message, card_id, stats=None, final_update=False, from_user=False
    ):
        # self.log.debug(f"update_card called with message: {message}, card_id: {card_id}, final_update: {final_update}")

        if card_id not in self.cards:
            self.log.warning(f"Card with id {card_id} not found. Creating a new card.")
            new_card_id = self.add_card(message, card_id, from_user, stats)
            if new_card_id != card_id:
                self.log.error(
                    f"Failed to create card with id {card_id}. New card created with id {new_card_id}"
                )
            return

        _, message_frame, label, firstTokenAnimation = self.cards[card_id]

        # Update timestamp and stats
        label_text = f'{datetime.now().strftime("%H:%M:%S")}   '
        if stats:
            label_text += "   ".join(
                f"{key}: {self._format_value(val)}"
                for key, val in stats.items()
                if val is not None
            )
        label.setText(label_text)

        # Hide the loading animation if it exists
        if firstTokenAnimation:
            firstTokenAnimation.setVisible(False)

        message_frame.setVisible(True)

        # Process and add new content
        if final_update:
            # Disable stop button when generation is complete
            self.ui.stop.setEnabled(False)
            # Clear existing content
            while message_frame.layout().count():
                child = message_frame.layout().takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            formatted_message = self.format_message(message)
            for part_type, part_content in formatted_message:
                self.add_content_to_card(
                    message_frame.layout(), part_type, part_content, from_user
                )
        else:
            # If there's already content, update the first label
            existing_label = message_frame.layout().itemAt(0).widget()
            if isinstance(existing_label, QLabel):
                existing_label.setText(message)

        self.repaint()
        # self.log.debug(f"Card {card_id} updated successfully. New content: {message}")

    def format_message(self, message):
        # Split the message into parts (regular text, code blocks, and URLs)
        parts = re.split(r"(```[\s\S]*?```|{.*?}|https?://\S+)", message)

        formatted_parts = []
        for part in parts:
            part = part.strip()
            if not part:  # Skip empty parts
                continue

            if part.startswith("```") and part.endswith("```"):
                # Handle code blocks
                if "\n" in part:
                    code = part.split("\n", 1)[1].rsplit("\n", 1)[0]
                else:
                    # Single-line code block
                    code = part[3:-3]  # Remove ``` from start and end
                formatted_parts.append(("code", code))
            elif part.startswith("{") and part.endswith("}"):
                # Handle JSON parts - treat as code
                formatted_parts.append(("code", part))
            else:
                # Process remaining parts for URLs
                url_parts = re.split(r"(https?://\S+)", part)
                for url_part in url_parts:
                    url_part = url_part.strip()
                    if not url_part:  # Skip empty parts
                        continue

                    if url_part.startswith("http://") or url_part.startswith(
                        "https://"
                    ):
                        youtube_pattern = r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=)?([^\s&\.]+)"
                        youtube_match = re.match(youtube_pattern, url_part)
                        if youtube_match:
                            video_id = youtube_match.group(1).strip("'")
                            formatted_parts.append(("youtube", video_id))
                        else:
                            formatted_parts.append(("webpage", url_part))
                    else:
                        formatted_parts.append(("text", url_part))

        return formatted_parts

    def add_content_to_card(self, card_layout, content_type, content, from_user):
        if content_type == "text":
            label = QLabel(content)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if from_user:
                self.apply_user_style(label)
            else:
                self.apply_assistant_style(label)
            card_layout.addWidget(label)

        elif content_type == "code":
            code_label = QLabel(content)
            code_label.setWordWrap(True)
            code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.apply_code_style(code_label)
            card_layout.addWidget(code_label)

        elif content_type == "youtube":
            preview_frame = QFrame()
            preview_layout = QVBoxLayout(preview_frame)
            self.create_youtube_preview(preview_layout, content)
            card_layout.addWidget(preview_frame)

        elif content_type == "webpage":
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
            border: none;
            background-color:rgb(77, 77, 77);
            color: rgb(255, 255, 255);
            padding: 8px 8px;
            text-align: left;
            """
        )

    def apply_code_style(self, message_frame):
        message_frame.setStyleSheet(
            """
            QLabel {
                font-family: 'Courier New', monospace;
                font-size: 11pt;
                border: 1px solid #2C2C2C;
                border-radius: 3px;
                background-color: #1E1E1E;
                color: #D4D4D4;
                padding: 8px;
            }
            """
        )

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
        outer_frame.setStyleSheet(
            """
            #youtubePreviewFrame {
                border: 1px solid #cccccc;
                border-radius: 5px;
                background-color: #f0f0f0;
                padding: 10px;
            }
        """
        )
        outer_layout = QVBoxLayout(outer_frame)

        thumbnail_label = QLabel("Loading thumbnail...")
        thumbnail_label.setFixedSize(320, 180)
        thumbnail_label.setAlignment(Qt.AlignCenter)
        thumbnail_label.setStyleSheet(
            """
            QLabel {
                background-color: #e0e0e0;
                border: 1px solid #b0b0b0;
            }
        """
        )
        outer_layout.addWidget(thumbnail_label)

        open_button = QPushButton("Watch on YouTube")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(f"https://www.youtube.com/watch?v={video_id}")
            )
        )
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.setStyleSheet(
            """
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
        """
        )
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
                    self.log.debug(
                        f"Thumbnail loaded successfully for video ID: {video_id}"
                    )
                    scaled_pixmap = pixmap.scaled(
                        320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    thumbnail_label.setPixmap(scaled_pixmap)
                    if scaled_pixmap.isNull():
                        self.log.error("Scaled pixmap is null")
                        thumbnail_label.setText("Failed to scale thumbnail")
                else:
                    self.log.error(
                        f"Failed to create pixmap from downloaded data for video ID: {video_id}"
                    )
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
        outer_frame.setStyleSheet(
            """
            #webpagePreviewFrame {
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                background-color: #f8f8f8;
                padding: 8px;
            }
        """
        )
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
        progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #d0d0d0;
                border-radius: 2px;
                background-color: #f0f0f0;
                height: 5px;
            }
            QProgressBar::chunk {
                background-color: #0066cc;
            }
        """
        )
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
        url_label.setStyleSheet(
            """
            QLabel {
                color: #333333;
                font-size: 13px;
            }
        """
        )
        outer_layout.addWidget(url_label)

        # Create button layout
        button_layout = QHBoxLayout()

        # Add "Open in Browser" button
        open_button = QPushButton("Open in Browser")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.setStyleSheet(
            """
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
        """
        )
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        button_layout.addWidget(open_button)

        # Add "Refresh" button
        refresh_button = QPushButton("Refresh")
        refresh_button.setCursor(Qt.PointingHandCursor)
        refresh_button.setStyleSheet(
            """
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
        """
        )
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

    def update_available_agents(self):
        """Update the agent dropdown with available agents."""
        self.ui.agent.clear()
        available_agents = []

        # Get ordered list of agents from settings
        ordered_agents = self.settings.get("agents", [])

        for agent in ordered_agents:
            # Map "Llm" to "No Agent" for display
            if agent == "Llm":
                available_agents.append("No Agent")
            # For other agents, check if they exist in the agents module
            else:
                agent_module = agent.lower()
                if hasattr(agents, agent_module) and getattr(agents, agent_module):
                    available_agents.append(agent)

        # Add agents to dropdown in the specified order
        for agent in available_agents:
            self.ui.agent.addItem(agent)

        # Set default selection
        self.ui.agent.setCurrentIndex(0)

        # Hide agents dropdown if configured to do so
        if self.settings["hide_agents"]:
            self.ui.agent.setVisible(False)

    async def request_welcome_message(self):
        """Request Agent Server to send its welcome message"""
        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{self.agent_port}/welcome",
                    json={},
                ) as response:
                    await response.read()
        except Exception as e:
            self.log.error(f"Failed to request welcome message: {str(e)}")


class CustomWebPage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self.log = get_logger(__name__)

        # Enable JavaScript
        self.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        self.log.debug(
            f"JS Console ({level}): {message} (line {lineNumber}, source: {sourceID})"
        )

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if _type == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, _type, isMainFrame)


class ModelSwitchWorker(QThread):
    finished = Signal()
    error = Signal(str)

    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        self.log = widget.log
        self._is_cancelled = False

    def cancel(self):
        """Signal that the operation should be cancelled."""
        self._is_cancelled = True

    def run(self):
        try:
            # Update the device list before restarting the conversation
            self.widget.update_device_list()
            self.widget.restart_conversation()

            if self._is_cancelled:
                return

            selected_model = self.widget.ui.model.currentText()
            model_settings = self.widget.settings["models"][selected_model]
            self.widget.current_backend = model_settings["backend"]

            if self.widget.server:
                self.log.debug("Starting setup thread")
                # Just restart the existing setup thread
                if not self.widget.setupThread.isRunning():
                    self.widget.setupThread.start()
                else:
                    self.log.warning("Setup thread is already running")

            if not self._is_cancelled:
                self.finished.emit()
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(str(e))


class ServerCheckWorker(QThread):
    server_ready = Signal(bool)

    def __init__(self, host, port, endpoint="/health", timeout=3000):
        super().__init__()
        self.host = host
        self.port = port
        self.endpoint = endpoint
        self.timeout = timeout
        self._is_cancelled = False
        self.log = get_logger(__name__)

    def cancel(self):
        """Signal that the operation should be cancelled."""
        self._is_cancelled = True

    def run(self):
        self.log.info(
            f"Checking server availability at {self.host}:{self.port}{self.endpoint}..."
        )

        # Parse the host to remove any protocol
        parsed_host = urlparse(self.host)
        clean_host = parsed_host.netloc or parsed_host.path

        start_time = time.time()
        attempts = 0

        while time.time() - start_time < self.timeout and not self._is_cancelled:
            try:
                if self.is_server_available(clean_host, self.port, self.endpoint):
                    self.log.info(f"Server available after {attempts} attempts")
                    self.server_ready.emit(True)
                    return

                attempts += 1
                elapsed_time = time.time() - start_time
                self.log.info(
                    f"Waiting for server... (Attempt {attempts}, Elapsed time: {elapsed_time:.1f}s)"
                )

                # Sleep in small chunks to remain responsive to cancellation
                for _ in range(10):  # 100ms * 10 = 1 second
                    if self._is_cancelled:
                        self.log.info("Server check cancelled")
                        self.server_ready.emit(False)
                        return
                    time.sleep(0.1)

            except Exception as e:
                self.log.error(f"Error checking server: {str(e)}")
                if self._is_cancelled:
                    self.server_ready.emit(False)
                    return

        self.server_ready.emit(False)

    def is_server_available(self, host, port, endpoint="/health"):
        try:
            url = f"http://{host}:{port}{endpoint}"
            response = requests.get(url, timeout=1)
            if response.status_code != 200:
                self.log.warning(f"Server returned status code {response.status_code}")
                return False
            return True
        except requests.Timeout:
            self.log.debug(f"Timeout connecting to {url}")
            return False
        except requests.ConnectionError:
            self.log.debug(f"Connection refused to {url}")
            return False
        except Exception as e:
            self.log.error(f"Unexpected error checking server: {str(e)}")
            return False


def main():
    if len(sys.argv) > 1:
        raise Exception(
            "Command line arguments are not supported. Please use the gaia-cli command line tool instead. If you need to change any settings, you can modify settings.json."
        )

    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(r":/img\gaia.ico"))
    widget = Widget()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":

    main()
