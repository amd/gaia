# This Python file uses the following encoding: utf-8
import sys
import os
import time
import socket
import json
import asyncio
from datetime import datetime
import textwrap
import subprocess
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
)
from PySide6.QtCore import Qt, QEvent, QSize, QObject, Signal, Slot, QThread
from PySide6.QtGui import QMovie

# This is a temporary workaround since the Qt Creator generated files
# do not import from the gui package.
sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))

from gaia.interface.ui_form import Ui_Widget  # pylint: disable=wrong-import-position


# SetupLLM class performs tasks in a separate thread
class SetupLLM(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

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
                    print("LLM has been loaded successfully!")
                else:
                    print("Failed to load LLM.")

    @Slot()
    def do_work(self):
        # Close previously open servers, if any
        if self.widget.agent_server is not None:
            print("Closing open Agent server")
            self.widget.agent_server.terminate()
            self.widget.agent_server = None
        if self.widget.llm_server is not None:
            print("Closing open LLM server")
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
        gaia_folder = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        command = [
            sys.executable,
            os.path.join(
                gaia_folder, "agents", self.widget.ui.agent.currentText(), "app.py"
            ),
        ]
        if self.widget.settings["dev_mode"]:
            creationflags = subprocess.CREATE_NEW_CONSOLE
        else:
            creationflags = 0
        self.widget.agent_server = subprocess.Popen(
            command, creationflags=creationflags
        )
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
        command = [
            sys.executable,
            os.path.join(
                gaia_folder,
                "llm",
                "server.py",
            ),
            "--checkpoint",
            model_settings["checkpoint"],
            "--max_new_tokens",
            str(self.widget.settings["max_new_tokens"]),
            "--device",
            selected_device.lower(),
            "--dtype",
            selected_dtype.lower(),
        ]
        if self.widget.settings["llm_server"]:
            self.widget.llm_server = subprocess.Popen(command, creationflags=creationflags)
            while not is_server_available("127.0.0.1", 8000):
                time.sleep(1)
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
        prompt = message_frame.text()
        asyncio.run(self.prompt_llm(prompt))

        # Reenable send and restart buttons
        self.widget.ui.ask.setEnabled(True)
        self.widget.ui.restart.setEnabled(True)

        self.finished.emit()


class StreamFromAgent(QObject):
    finished = Signal()
    add_card = Signal(str, str, bool, dict)
    update_card = Signal(str, str, dict)

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
        if new_card:
            self.complete_message = chunk
            self.agent_card_count += 1
            self.add_card.emit(self.complete_message, self.last_agent_card_id, False, stats)
        else:
            self.complete_message = self.complete_message + chunk
            self.update_card.emit(self.complete_message, self.last_agent_card_id, stats)
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_Widget()
        self.ui.setupUi(self)
        self.setStyleSheet("background-color: black;")
        self.setWindowTitle("RyzenAI GAIA")
        self.card_count = 0
        self.cards = {}
        self.agent_server = None
        self.llm_server = None
        self.device_list_mapping = {}

        # Read settings
        gaia_folder = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(
            os.path.join(gaia_folder, "interface", "settings.json"),
            "r",
            encoding="utf-8",
        ) as file:
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
            print("Closing agent server")
            self.agent_server.terminate()
        if self.llm_server is not None:
            print("Closing LLM server")
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
        self.setupThread.quit()
        self.setupThread.wait()
        self.setupThread.start()

    # Request LLM server to also restart
    async def request_restart(self):
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

        chuncked_message = self.split_into_chunks(message)

        # If there is already a "loading" card waiting we will take that one
        if "loading" in self.cards and not from_user:
            card, message_frame, label, firstTokenAnimation = self.cards.pop("loading")
            message_frame.setVisible(True)
            message_frame.setText(chuncked_message)
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

            # Create and add the push button and label to the card message frame
            message_frame = QPushButton(chuncked_message)
            firstTokenAnimation = None
            if from_user:
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
            else:
                firstTokenAnimation = QLabel()
                firstTokenMovie = QMovie(r":/img/waiting_token.gif")
                firstTokenMovie.setScaledSize(QSize(50, 50))
                firstTokenAnimation.setFixedSize(QSize(50, 50))
                firstTokenAnimation.setMovie(firstTokenMovie)
                card_message_layout.addWidget(firstTokenAnimation)
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
        self.card_count = self.card_count + 1

        return card_id

    def update_card(self, message, card_id, stats=None):
        _, message_frame, label, firstTokenAnimation = self.cards[card_id]
        chuncked_message = self.split_into_chunks(message)
        message_frame.setText(chuncked_message)
        label_text = f'{datetime.now().strftime("%H:%M:%S")}   '
        if stats:
            label_text += "   ".join(f"{key}: {self._format_value(val)}"
                                   for key, val in stats.items() if val is not None)
        label.setText(label_text)
        firstTokenAnimation.setVisible(False)
        message_frame.setVisible(True)

    def scrollToBottom(
        self, minVal=None, maxVal=None  # pylint: disable=unused-argument
    ):
        # Additional params 'minVal' and 'maxVal' are declared because
        # rangeChanged signal sends them, but we set it to optional
        self.ui.scrollArea.verticalScrollBar().setValue(
            self.ui.scrollArea.verticalScrollBar().maximum()
        )


def main():
    app = QApplication(sys.argv)
    widget = Widget()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
