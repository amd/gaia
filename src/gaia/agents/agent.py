# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import time
from collections import OrderedDict
from typing import Any, Callable
from transformers import LlamaTokenizer
from websocket import create_connection
from websocket._exceptions import WebSocketTimeoutException
from aiohttp import web
import requests
from llama_index.core.llms import (
    LLMMetadata,
    CustomLLM,
)
from llama_index.core.llms.callbacks import llm_completion_callback

from llama_index.llms.llama_cpp.llama_utils import (
    messages_to_prompt,
)
from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    CompletionResponse,
    CompletionResponseGen,
    MessageRole,
)


class Agent:
    def __init__(self, host, port):
        # Placeholder for LLM Server Websocket and others
        self.llm_server_uri = "ws://localhost:8000/ws"
        self.llm_server_websocket = None
        self.latest_prompt_request = None
        self.host = host
        self.port = port

        # performance stats
        # in = input tokens
        # ttft = time-to-first-token
        # out = output tokens
        # tps = tokens-per-second
        self.stats = OrderedDict([
            ('in', None),
            ('ttft', None),
            ('out', None),
            ('tps', None)
        ])
        # Load the LLaMA tokenizer
        self.tokenizer = LlamaTokenizer.from_pretrained("meta-llama/Llama-2-7b-hf")

        # Initialize Agent Server
        self.app = web.Application()
        self.app.router.add_post("/prompt", self._on_prompt_received)
        self.app.router.add_post("/restart", self._on_chat_restarted)
        self.app.router.add_post("/load_llm", self._on_load_llm)

    def __del__(self):
        # Ensure websocket gets closed when agent is deleted
        if self.llm_server_websocket:
            if self.llm_server_websocket.connected:
                self.llm_server_websocket.close()

    def _clear_stats(self):
        for key, _ in self.stats.items():
            self.stats[key] = None

    def count_tokens(self, text):
        return len(self.tokenizer.encode(text))

    def get_time_to_first_token(self):
        return self.stats['ttft']

    def get_tokens_per_second(self):
        return self.stats['tps']

    def initialize_server(self):
        web.run_app(self.app, host=self.host, port=self.port)

    def prompt_llm_server(self, prompt):
        ws = create_connection(self.llm_server_uri)
        try:
            print(f"Sending prompt to LLM server:\n{prompt}")
            prompt_tokens = self.count_tokens(prompt)
            start_time = time.perf_counter()
            ws.send(prompt)

            first_chunk = True
            full_response = ""

            self._clear_stats()

            while True:
                try:
                    if first_chunk:
                        ws.sock.settimeout(None)  # No timeout for first chunk
                    else:
                        ws.sock.settimeout(5)  # 5 second timeout after first chunk

                    chunk = ws.recv()
                    current_time = time.perf_counter()

                    if first_chunk:
                        self.stats['IN'] = prompt_tokens
                        self.stats['TTFT'] = current_time - start_time
                        start_time = time.perf_counter()
                        first_chunk = False

                    if chunk:
                        if "</s>" in chunk:
                            chunk = chunk.replace("</s>", "")
                            full_response += chunk

                            total_time = current_time - start_time
                            total_tokens = self.count_tokens(full_response)
                            self.stats['OUT'] = total_tokens
                            self.stats['TPS'] = (total_tokens-1) / total_time if total_time > 0 and total_tokens > 1 else 0.0

                            yield chunk
                            break
                        full_response += chunk
                        yield chunk

                except WebSocketTimeoutException:
                    break

        finally:
            self._clear_stats()
            ws.close()

    def prompt_received(self, prompt):
        print("Message received:", prompt)

    def chat_restarted(self):
        print("Client requested chat to restart")

    def print(self, input_str: str):
        print(input_str)
        for i, word in enumerate(input_str.split(" ")):
            new_card = i == 0
            self.stream_to_ui(f"{word} ", new_card=new_card)
            time.sleep(0.1)

    async def _on_prompt_received(self, ui_request):
        data = await ui_request.json()
        self.latest_prompt_request = ui_request
        self.prompt_received(data["prompt"])
        return web.Response()

    async def _on_chat_restarted(self, _):
        self.chat_restarted()
        return web.Response()

    async def _on_load_llm(self, ui_request):
        data = await ui_request.json()
        print(f"Client requested to load LLM ({data['model']})")

        response = {"status": "Success"}
        return web.json_response(response)

    def stream_to_ui(self, chunk, new_card=True):
        data = {"chunk": chunk, "new_card": new_card, "stats": self.stats}
        url = "http://127.0.0.1:8002/stream_to_ui"
        requests.post(url, json=data)


class LocalLLM(CustomLLM):
    prompt_llm_server: Callable = None
    stream_to_ui: Callable = None
    context_window: int = 3900
    num_output: int = 256
    model_name: str = "custom"

    async def achat(
        self,
        messages: Any,
        **kwargs: Any,
    ) -> str:

        formatted_message = messages_to_prompt(messages)

        # Prompt LLM and steam content to UI
        text_response = await self.prompt_llm_server(
            prompt=formatted_message, stream_to_ui=True
        )

        response = ChatResponse(
            message=ChatMessage(
                role=MessageRole.ASSISTANT,
                content=text_response,
                additional_kwargs={},
            ),
            raw={"text": text_response},
        )
        return response

    @property
    def metadata(self) -> LLMMetadata:
        """Get LLM metadata."""
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.num_output,
            model_name=self.model_name,
        )

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
        response = self.prompt_llm_server(prompt=prompt)
        self.stream_to_ui(response, new_card=True)
        return CompletionResponse(text=response)

    @llm_completion_callback()
    def stream_complete(self, prompt: str, **kwargs: Any) -> CompletionResponseGen:
        response = ""
        new_card = True
        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
            yield CompletionResponse(text=response, delta=chunk)
