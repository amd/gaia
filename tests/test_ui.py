# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import sys, re, time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from gaia.interface.widget import Widget


class MainWindow(Widget):
    def __init__(self):
        super().__init__(server=False)

        # Set window properties
        self.setWindowTitle("Chat Widget Test")
        self.setGeometry(100, 100, 800, 600)
        self.agent_card_count = 0
        self.complete_message = ""

        # Schedule messages to be sent
        QTimer.singleShot(10000, self.send_test_messages)

    def stream_message(self, msg: str, from_user: bool = False):

        chunks = re.split(r"(\s+)", msg)
        chunks = [chunk for chunk in chunks if chunk.strip()]

        print(f"Total chunks: {len(chunks)}")

        message_type = "User" if from_user else "AI"
        stats = {"INFO": f"Testing {message_type} message"}

        if from_user == False:  # streaming from agent
            for i, chunk in enumerate(chunks):
                new_card = i == 0
                final_update = i == len(chunks) - 1  # True for the last chunk
                if new_card:
                    self.complete_message = chunk + " "
                    self.agent_card_count += 1
                    print(f"Adding new card with content: {self.complete_message}")
                    self.add_card(self.complete_message, "0", from_user, stats)
                else:
                    self.complete_message += chunk + " "
                    print(f"Updating card with content: {self.complete_message}")
                    self.update_card(
                        self.complete_message, "0", stats, final_update, from_user
                    )
                time.sleep(0.1)
        else:  # one time update from user
            self.add_card(msg, "0", from_user, stats)

        print("Finished sending all chunks")

    def process_chunk(self, chunk):
        self.complete_message += " " + chunk if self.complete_message else chunk
        self.update_card(self.complete_message, self.current_card_id)

        # Ensure the UI updates
        QTimer.singleShot(0, self.scrollToBottom)

    def send_test_messages(self):

        # Test regular text message
        # self.stream_message("Hello! This is a test message from AI.", from_user=False)
        # self.stream_message("Hello! This is a test message from user.", from_user=True)

        # Error message
        e = "[WinError 10061] No connection could be made because the target machine actively refused it"
        msg = f"Having trouble connecting to the LLM server, got:\n```{str(e)}```"
        self.stream_message(msg, from_user=False)
        self.stream_message(msg, from_user=True)

        # Test code block
        code_message = """ Here's a code example:
```python
def hello_world():
    print("Hello, World!")
```
"""
        self.stream_message(code_message, from_user=False)
        self.stream_message(code_message, from_user=True)

        # Test YouTube link
        youtube_message = (
            "Check out this cool video: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.stream_message(youtube_message, from_user=False)

        youtube_message = (
            "Can you index this video? https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.stream_message(youtube_message, from_user=True)

        # Test web link
        web_message = "Here's an interesting article: https://fal.ai/models"
        self.stream_message(web_message, from_user=False)

        web_message = "Summarize this article: https://medium.com/ai-advances/why-you-should-be-cautious-about-using-langchain-even-after-its-latest-updates-b84dae6639a4"
        self.stream_message(web_message, from_user=True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(r":/img\gaia.ico"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
