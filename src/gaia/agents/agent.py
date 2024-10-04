# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import time
import logging
from collections import OrderedDict
from typing import Any, Callable
from transformers import LlamaTokenizer
from huggingface_hub import HfFolder, HfApi
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

from gaia.interface.util import UIMessage

class Agent:
    def __init__(self, host="127.0.0.1", port=8001):
        # Placeholder for LLM Server Websocket and others
        self.llm_server_uri = "ws://localhost:8000/ws"
        self.llm_server_websocket = None
        self.latest_prompt_request = None
        self.host = host
        self.port = port
        self.last_chunk = False

        # Set up logging
        logging.basicConfig(level=logging.DEBUG)
        self.log = logging.getLogger(__name__)

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
        # last chunk in response
        self.last = False

        # Load the LLaMA tokenizer
        self.tokenizer = self._initialize_tokenizer()

        # Initialize Agent Server
        self.app = web.Application()
        self.app.router.add_post("/prompt", self._on_prompt_received)
        self.app.router.add_post("/restart", self._on_chat_restarted)
        self.app.router.add_post("/load_llm", self._on_load_llm)

    def _initialize_tokenizer(self):
        try:
            # Check if the user is logged in to Hugging Face
            token = HfFolder.get_token()
            if not token:
                raise EnvironmentError("No Hugging Face token found. Please log in to Hugging Face.")

            # Verify the token
            api = HfApi()
            try:
                api.whoami(token)
            except Exception:
                raise EnvironmentError("Invalid Hugging Face token. Please provide a valid token.")

            # Attempt to load the tokenizer
            tokenizer = LlamaTokenizer.from_pretrained("meta-llama/Llama-2-7b-hf")
            return tokenizer
        except EnvironmentError as e:
            UIMessage.error(str(e))
            from gaia.interface.huggingface import get_huggingface_token
            token = get_huggingface_token()
            if token:
                # Try to initialize the tokenizer again after getting the token
                return self._initialize_tokenizer()
            else:
                UIMessage.error("No token provided. Tokenizer initialization failed.")
                return None
        except Exception as e: # pylint:disable=W0718
            UIMessage.error(f"An unexpected error occurred: {e}")
            return None

    def __del__(self):
        # Ensure websocket gets closed when agent is deleted
        if hasattr(self, 'llm_server_websocket'):
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
        try:
            ws = create_connection(self.llm_server_uri)
        except Exception as e: # pylint:disable=W0718
            self.print(f"My brain is not working:```{e}```")
            return

        try:
            self.log.debug(f"Sending prompt to LLM server:\n{prompt}")
            prompt_tokens = self.count_tokens(prompt)
            start_time = time.perf_counter()
            ws.send(prompt)

            first_chunk = True
            self.last_chunk = False
            full_response = ""

            self._clear_stats()
            self.last = False

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
                        print(chunk)
                        if "</s>" in chunk:
                            chunk = chunk.replace("</s>", "")
                            full_response += chunk

                            total_time = current_time - start_time
                            total_tokens = self.count_tokens(full_response)
                            self.stats['OUT'] = total_tokens
                            self.stats['TPS'] = (total_tokens-1) / total_time if total_time > 0 and total_tokens > 1 else 0.0
                            self.last = True

                            yield chunk
                            break
                        full_response += chunk
                        yield chunk

                except WebSocketTimeoutException:
                    break
                except Exception as e: # pylint:disable=W0718
                    UIMessage.error(str(e))
                    return

        finally:
            self._clear_stats()
            ws.close()

    def prompt_received(self, prompt):
        self.log.debug("Message received:", prompt)

    def chat_restarted(self):
        self.log.debug("Client requested chat to restart")

    def print(self, input_str: str):
        self.log.debug(input_str)
        input_lst = input_str.split(" ")
        input_len = len(input_lst)
        for i, word in enumerate(input_lst):
            new_card = i == 0
            self.last = i == (input_len-1)
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
        self.log.debug(f"Client requested to load LLM ({data['model']})")

        response = {"status": "Success"}
        return web.json_response(response)

    def stream_to_ui(self, chunk, new_card=True):
        data = {"chunk": chunk, "new_card": new_card, "stats": self.stats, "last":self.last}
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
