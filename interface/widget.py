# This Python file uses the following encoding: utf-8
import sys
from datetime import datetime

from PySide6.QtWidgets import QApplication, QWidget, QApplication, QMainWindow, QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSpacerItem, QSizePolicy
from PySide6.QtCore import Qt

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

        # Connect ask to send_message
        self.ui.ask.clicked.connect(self.send_message)

    def send_message(self):
        print("Button pressed!")
        self.add_card("Hi there! it is a pleasure to have you here! Super happy to be around you!", from_user = True)
        self.add_card("Hello dear user! It is also a pleasure to be around someone like youuuuuuu", from_user = False)

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
        button = QPushButton(message)
        if from_user:
            button.setStyleSheet("""
                    font-size: 12pt;
                    border-radius: 3px;
                    border: 1px solid #0A819A;
                    background-color: #0A819A;
                    color: rgb(255, 255, 255);
                    padding: 8px 8px;
                """)
        else:
            button.setStyleSheet("""
                    font-size: 12pt;
                    border-radius: 3px;
                    border: 1px solid rgb(0, 0, 0);
                    background-color:rgb(77, 77, 77);
                    color: rgb(255, 255, 255);
                    padding: 8px 8px;
                """)
        label = QLabel(datetime.now().strftime("%H:%M:%S"))
        label.setStyleSheet("color: rgb(255, 255, 255);")
        card_message_layout.addWidget(button)
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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = Widget()
    widget.show()
    sys.exit(app.exec())
