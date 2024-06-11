# This Python file uses the following encoding: utf-8
import sys
import os
import time
import socket
import aiohttp
import asyncio
from datetime import datetime
import textwrap
import subprocess


from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QApplication,
    QMainWindow,
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

# Important:
# You need to run the following command to generate the ui_form.py file
#     pyside6-uic form.ui -o ui_form.py, or
#     pyside2-uic form.ui -o ui_form.py
from ui_form import Ui_Widget


# SetupLLM class performs tasks in a separate thread
class SetupLLM(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    # Request Agent Server to update connection to LLM Server
    async def request_llm_load(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://127.0.0.1:8001/load_llm",
                json={"model": widget.ui.model.currentText()},
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
        if widget.agent_server is not None:
            print("Closing open Agent server")
            widget.agent_server.terminate()
            widget.agent_server = None
        if widget.llm_server is not None:
            print("Closing open Agent server")
            widget.llm_server.terminate()
            widget.llm_server = None

        # Switch visibility of UI elements
        widget.ui.loading.setVisible(True)
        widget.ui.loading.setVisible(True)
        widget.ui.loadingLabel.setVisible(True)
        widget.ui.loadingGif.setVisible(True)
        widget.ui.ask.setEnabled(False)

        # Initialize Agent server
        # Note: Remove creationflags to run in non-debug mode
        widget.ui.loadingLabel.setText(f"Initializing Agent Server...")
        gaia_folder = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        command = [
            sys.executable,
            os.path.join(gaia_folder, "src", "gaia", "agents", "Example", "app.py"),
        ]
        widget.agent_server = subprocess.Popen(
            command, creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        while not is_server_available("127.0.0.1", 8001):
            time.sleep(1)
        time.sleep(3)

        # Initialize LLM server
        widget.ui.loadingLabel.setText(
            f"Initializing LLM server for {widget.ui.model.currentText()}..."
        )
        command = [
            sys.executable,
            os.path.join(
                gaia_folder, "src", "gaia", "agents", "Example", "llm_server.py"
            ),
        ]
        widget.llm_server = subprocess.Popen(
            command, creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        while not is_server_available("127.0.0.1", 8000):
            time.sleep(1)
        asyncio.run(self.request_llm_load())

        # Perform any other actions here
        widget.ui.loadingLabel.setText("Finishing up...")
        time.sleep(3)

        # Done
        widget.ui.loadingLabel.setText(
            f"Ready to run {widget.ui.model.currentText()} on {widget.ui.device.currentText()}!"
        )
        widget.ui.loadingGif.setVisible(False)

        widget.ui.ask.setEnabled(True)

        self.finished.emit()


class LLMStreaming(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    async def prompt_llm(self, prompt):
        complete_response = ""
        last_card = str(widget.card_count - 1)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://127.0.0.1:8001/message", json={"prompt": prompt}
            ) as response:
                async for token in response.content:
                    # Update card as we receive the stream
                    complete_response = complete_response + token.decode()[:-1]
                    widget.update_card(last_card, complete_response)

    @Slot()
    def do_work(self):
        # Prompt LLM and stream results
        _, message_frame, _, _ = widget.cards[str(widget.card_count - 2)]
        prompt = message_frame.text()
        asyncio.run(self.prompt_llm(prompt))

        # Reenable send and restart buttons
        widget.ui.ask.setEnabled(True)
        widget.ui.restart.setEnabled(True)

        self.finished.emit()


def is_server_available(host, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
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

        # Connect buttons
        self.ui.ask.clicked.connect(self.send_message)
        self.ui.restart.clicked.connect(self.restart_conversation)
        self.ui.model.currentIndexChanged.connect(self.deployment_changed)
        self.ui.device.currentIndexChanged.connect(self.deployment_changed)

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

        # Hide some of the components initially
        self.ui.chat.setVisible(False)

        # Loading symbol
        self.movie = QMovie("img\loading.gif")
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

        # Create LLM streaming thread
        self.streamingThread = QThread()
        self.streamingWorker = LLMStreaming(self)
        self.streamingWorker.moveToThread(self.streamingThread)
        self.streamingThread.started.connect(self.streamingWorker.do_work)
        self.streamingWorker.finished.connect(self.streamingThread.quit)

    def closeEvent(self, *args, **kwargs):
        # Make sure servers are killed when application exits
        if self.agent_server is not None:
            print("Closing agent server")
            self.agent_server.terminate()
        if self.llm_server is not None:
            print("Closing LLM server")
            self.llm_server.terminate()

    def make_chat_visible(self, visible):
        if (visible and widget.ui.chat.isVisible()) or (
            not visible and not widget.ui.chat.isVisible()
        ):
            # Skip if we are already at the visibility we desire
            return
        if visible:
            widget.ui.loadingLabel.setVisible(False)
            widget.ui.loading.setVisible(False)
            widget.ui.chat.setVisible(True)
            widget.ui.sampleCard_1.setVisible(False)
            widget.ui.sampleCard_2.setVisible(False)
            widget.ui.mainLayout.removeItem(widget.ui.welcomeSpacerTop)
            widget.ui.mainLayout.removeItem(widget.ui.welcomeSpacerBottom)
        else:
            widget.ui.loadingLabel.setVisible(True)
            widget.ui.loading.setVisible(True)
            widget.ui.chat.setVisible(False)
            widget.ui.mainLayout.insertItem(
                widget.top_spacer_index, widget.ui.welcomeSpacerTop
            )
            widget.ui.mainLayout.insertItem(
                widget.bottom_spacer_index, widget.ui.welcomeSpacerBottom
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
                and widget.ui.ask.isEnabled()
            ):
                # Send message and consume the event, preventing return from being added to prompt box
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    def deployment_changed(self):
        self.restart_conversation()
        self.setupThread.quit()
        self.setupThread.wait()
        self.setupThread.start()

    # Request LLM server to also restart
    async def request_restart(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://127.0.0.1:8001/restart", json={}
            ) as response:
                await response.json()

    def restart_conversation(self):
        # Disable chat
        self.make_chat_visible(False)

        # Let server know that we are restarting the conversation
        asyncio.run(self.request_restart())

        # Delete existing cards
        for card in self.cards:
            # Remove the frame from its parent layout if it has one
            card_frame, _, _, _ = self.cards[card]
            if card_frame.parent():
                card_frame.setParent(None)
            # Delete the frame
            card_frame.deleteLater()
        self.cards = {}

    def send_message(self):
        prompt = self.ui.prompt.toPlainText()
        self.ui.prompt.clear()
        if prompt:
            # Disable send and restart buttons
            self.ui.ask.setEnabled(False)
            self.ui.restart.setEnabled(False)

            # Send message
            self.add_card(prompt, from_user=True)
            self.add_card("...", from_user=False)

            # Stream inputs from LLM
            self.streamingThread.start()
            self.make_chat_visible(True)

    def split_into_chunks(self, message):
        return "\n".join(textwrap.wrap(message, width=75))

    def add_card(self, message, from_user=True):
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
        chuncked_message = self.split_into_chunks(message)
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
            firstTokenMovie = QMovie("img\waiting_token.gif")
            firstTokenMovie.setScaledSize(QSize(50, 50))
            firstTokenAnimation.setFixedSize(QSize(50, 50))
            firstTokenAnimation.setMovie(firstTokenMovie)
            firstTokenMovie.start()
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
            message_frame.setVisible(False)
        label = QLabel(datetime.now().strftime("%H:%M:%S"))
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
        card_id = str(self.card_count)
        self.cards[card_id] = (card, message_frame, label, firstTokenAnimation)
        self.card_count = self.card_count + 1

        return card_id

    def update_card(self, card_id, message):
        _, message_frame, label, firstTokenAnimation = self.cards[card_id]
        chuncked_message = self.split_into_chunks(message)
        message_frame.setText(chuncked_message)
        label.setText(datetime.now().strftime("%H:%M:%S"))
        firstTokenAnimation.setVisible(False)
        message_frame.setVisible(True)

    def scrollToBottom(self, minVal=None, maxVal=None):
        # Additional params 'minVal' and 'maxVal' are declared because
        # rangeChanged signal sends them, but we set it to optional
        self.ui.scrollArea.verticalScrollBar().setValue(
            self.ui.scrollArea.verticalScrollBar().maximum()
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = Widget()
    widget.show()
    sys.exit(app.exec())
