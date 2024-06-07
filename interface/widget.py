# This Python file uses the following encoding: utf-8
import sys
import time
from datetime import datetime
import textwrap
from transformers import pipeline

from PySide6.QtWidgets import QApplication, QWidget, QApplication, QMainWindow, QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSpacerItem, QSizePolicy
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

    @Slot()
    def do_work(self):
        # Download model and setup server
        widget.ui.loading.setVisible(True);
        widget.ui.loadingLabel.setText("Downloading Phi 3 Mini...");
        widget.pipe = pipeline("text2text-generation", model="facebook/blenderbot-400M-distill")
        widget.ui.loadingLabel.setText("Initializing Server...");
        time.sleep(3);
        widget.ui.loadingLabel.setText("Finishing up...");
        time.sleep(3);
        widget.ui.loading.setVisible(False);
        widget.llm_setup_completed = True

        # Enable chat interface
        widget.make_chat_visible(True)

        # Start streaming
        widget.streamingThread.start()

        self.finished.emit()

class LLMStreaming(QObject):
    finished = Signal()

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    @Slot()
    def do_work(self):
        # Prompt LLM
        _, message_frame, _ = widget.cards[str(widget.card_count-2)]
        prompt = message_frame.text()
        response = self.widget.pipe(prompt, max_new_tokens=32)
        response = response[0]["generated_text"]

        # Show streamed message
        last_card = str(widget.card_count-1)
        words = response.split()
        streaming_message = ""
        for word in words:
            streaming_message += word + ' '
            time.sleep(0.15)
            widget.update_card(last_card, streaming_message)

        # Reenable send and restart buttons
        widget.ui.ask.setEnabled(True);
        widget.ui.restart.setEnabled(True);

        self.finished.emit()

class Widget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_Widget()
        self.ui.setupUi(self)
        self.setStyleSheet("background-color: black;")
        self.setWindowTitle("RyzenAI GAIA")
        self.card_count = 0
        self.cards = {}
        self.llm_setup_completed = False

        # Connect buttons
        self.ui.ask.clicked.connect(self.send_message)
        self.ui.restart.clicked.connect(self.restart_conversation)

        # Ensure that we are scrolling to the bottom every time the range
        # of the card scroll area changes
        self.ui.scrollArea.verticalScrollBar().rangeChanged.connect(
            self.scrollToBottom,
        )

        # Install event filter to prompt text box
        self.ui.prompt.installEventFilter(self)

        # Hide some of the components initially
        self.ui.chat.setVisible(False);
        self.ui.loading.setVisible(False);

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
        self.setupWorker.finished.connect(self.setupWorker.deleteLater)
        self.setupThread.finished.connect(self.setupThread.deleteLater)

        # Create LLM streaming thread
        self.streamingThread = QThread()
        self.streamingWorker = LLMStreaming(self)
        self.streamingWorker.moveToThread(self.streamingThread)
        self.streamingThread.started.connect(self.streamingWorker.do_work)
        self.streamingWorker.finished.connect(self.streamingThread.quit)

    def make_chat_visible(self,visible):
        if visible:
            widget.ui.chat.setVisible(True);
            widget.ui.sampleCard_1.setVisible(False);
            widget.ui.sampleCard_2.setVisible(False);
            widget.ui.mainLayout.setStretch(widget.ui.mainLayout.indexOf(widget.ui.welcomeSpacerTop), 0)
            widget.ui.mainLayout.setStretch(widget.ui.mainLayout.indexOf(widget.ui.welcomeSpacerBottom), 0)
        else:
            widget.ui.chat.setVisible(False);
            widget.ui.mainLayout.setStretch(widget.ui.mainLayout.indexOf(widget.ui.welcomeSpacerTop), 1)
            widget.ui.mainLayout.setStretch(widget.ui.mainLayout.indexOf(widget.ui.welcomeSpacerBottom), 1)

    def eventFilter(self, obj, event):
        """
        Event filter used to send message when enter is pressed inside the prompt box
        """
        if event.type() == QEvent.KeyPress and not (event.modifiers() & Qt.ShiftModifier) and obj is self.ui.prompt:
            if event.key() == Qt.Key_Return and self.ui.prompt.hasFocus():
                # Send message and consume the event, preventing return from being added to prompt box
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    def restart_conversation(self):
        self.make_chat_visible(False)

        # Delete existing cards
        for card in self.cards:
            # Remove the frame from its parent layout if it has one
            card_frame, _, _ = self.cards[card]
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
            self.ui.ask.setEnabled(False);
            self.ui.restart.setEnabled(False);

            # Download model, setup server, and enable chat interface
            if not self.llm_setup_completed:
                self.setupThread.start()

            # Send message
            self.add_card(prompt, from_user = True)
            self.add_card("...", from_user = False)

            if not self.ui.loading.isVisible():
                self.streamingThread.start()
                self.make_chat_visible(True)


    def split_into_chunks(self,message):
        return "\n".join(textwrap.wrap(message, width=75))

    def add_card(self, message, from_user = True):
        # Create the main card frame
        card = QFrame()
        card.setFrameShape(QFrame.NoFrame)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(9, 0, 9, 0)
        card_layout.setSpacing(0)

        # Create the card message frame
        card_message = QFrame()
        card_message_layout = QVBoxLayout(card_message)
        card_message_layout.setContentsMargins(9,0,9,0)
        card_message_layout.setSpacing(0)

        # Create and add the push button and label to the card message frame
        chuncked_message = self.split_into_chunks(message)
        message_frame = QPushButton(chuncked_message)
        if from_user:
            message_frame.setStyleSheet("""
                    font-size: 12pt;
                    border-radius: 3px;
                    border: 1px solid #0A819A;
                    background-color: #0A819A;
                    color: rgb(255, 255, 255);
                    padding: 8px 8px;
                    text-align: left;
                """)
        else:
            message_frame.setStyleSheet("""
                    font-size: 12pt;
                    border-radius: 3px;
                    border: 1px solid rgb(0, 0, 0);
                    background-color:rgb(77, 77, 77);
                    color: rgb(255, 255, 255);
                    padding: 8px 8px;
                    text-align: left;
                """)
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
        self.cards[card_id] = (card, message_frame, label)
        self.card_count = self.card_count+1

        return card_id

    def update_card(self,card_id, message):
        _, message_frame, label = self.cards[card_id]
        chuncked_message = self.split_into_chunks(message)
        message_frame.setText(chuncked_message)
        label.setText(datetime.now().strftime("%H:%M:%S"))


    def scrollToBottom (self, minVal=None, maxVal=None):
        # Additional params 'minVal' and 'maxVal' are declared because
        # rangeChanged signal sends them, but we set it to optional
        self.ui.scrollArea.verticalScrollBar().setValue(self.ui.scrollArea.verticalScrollBar().maximum())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = Widget()
    widget.show()
    sys.exit(app.exec())
