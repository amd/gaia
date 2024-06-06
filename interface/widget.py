# This Python file uses the following encoding: utf-8
import sys
from datetime import datetime
import textwrap

from PySide6.QtWidgets import QApplication, QWidget, QApplication, QMainWindow, QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSpacerItem, QSizePolicy
from PySide6.QtCore import Qt, QEvent

# Important:
# You need to run the following command to generate the ui_form.py file
#     pyside6-uic form.ui -o ui_form.py, or
#     pyside2-uic form.ui -o ui_form.py
from ui_form import Ui_Widget

class Widget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_Widget()
        self.ui.setupUi(self)
        self.setStyleSheet("background-color: black;")
        self.setWindowTitle("RyzenAI GAIA")
        self.card_count = 0
        self.cards = {}

        # Connect ask to send_message
        self.ui.ask.clicked.connect(self.send_message)

        # Ensure that we are scrolling to the bottom every time the range
        # of the card scroll area changes
        self.ui.scrollArea.verticalScrollBar().rangeChanged.connect(
            self.scrollToBottom,
        )

        # Install event filter to prompt text box
        self.ui.prompt.installEventFilter(self)

        # Hide chat window initially
        self.ui.chat.setVisible(False);

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

    def send_message(self):
        prompt = self.ui.prompt.toPlainText()
        self.ui.prompt.clear()
        if prompt:
            # Enable chat interface
            if not self.ui.chat.isVisible():
                self.ui.chat.setVisible(True);
                self.ui.sampleCard_1.setVisible(False);
                self.ui.sampleCard_2.setVisible(False);
                self.ui.mainLayout.removeItem(self.ui.welcomeSpacerTop)
                self.ui.mainLayout.removeItem(self.ui.welcomeSpacerBottom)

            # Send message
            self.add_card(prompt, from_user = True)
            self.add_card("Hello dear user! It is also a pleasure to be around someone like youuuuuuu", from_user = False)

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
        self.cards[card_id] = (message_frame, label)
        self.card_count = self.card_count+1

        return card_id

    def update_card(self,card_id, message):
        message_frame, label = self.cards[card_id]
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
