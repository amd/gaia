# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import time
import websocket

def on_message(ws, message):  # pylint: disable=W0613
    print(message, end="", flush=True)

def on_error(ws, error): # pylint: disable=W0613
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg): # pylint: disable=W0613
    print("WebSocket connection closed")

def on_open(ws, query):
    ws.send(query)

def run_query(query):
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(
        "ws://localhost:8000/ws",
        on_open=lambda ws: on_open(ws, query),
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    print(f"Query: {query}")
    start = time.time()
    print("Response: ")
    ws.run_forever()
    latency = time.time() - start
    print(f"\n{latency} secs")
    print('-------------------------------------------------------------------')

if __name__ == "__main__":
    queries = [
#         """\
# <s>[INST] <<SYS>>
# You are a helpful, respectful and honest assistant. Always answer as helpfully as possible, while being safe.  Your answers should not include any harmful, unethical, racist, sexist, toxic, dangerous, or illegal content. Please ensure that your responses are socially unbiased and positive in nature.

# If a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information.
# <</SYS>>

# There's a llama in my garden 😱 What should I do? [/INST]
# """,
#         """\
# <s>[INST] <<SYS>>
# You are a helpful, respectful and honest assistant. Always answer as helpfully as possible, while being safe.  Your answers should not include any harmful, unethical, racist, sexist, toxic, dangerous, or illegal content. Please ensure that your responses are socially unbiased and positive in nature.

# If a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information.
# <</SYS>>

# How can I make a delicious pizza from scratch? [/INST]
# """,
#         """\
# <s>[INST] <<SYS>>
# You are a helpful, respectful and honest assistant. Always answer as helpfully as possible, while being safe.  Your answers should not include any harmful, unethical, racist, sexist, toxic, dangerous, or illegal content. Please ensure that your responses are socially unbiased and positive in nature.

# If a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information.
# <</SYS>>

# What are the main themes in Shakespeare's play Romeo and Juliet? [/INST]
# """
    "Tell me a joke about a bee"
    ]
    for query in queries:
        run_query(query)
