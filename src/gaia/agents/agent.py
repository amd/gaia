from typing import Any, Callable
from websocket import create_connection
from websocket._exceptions import WebSocketTimeoutException
from aiohttp import web
import requests
from llama_index.core.llms import (
    LLMMetadata,
    CustomLLM,
    CompletionResponse,
    CompletionResponseGen,
)
from llama_index.core.llms.callbacks import llm_completion_callback


class Agent:
    def __init__(self, host, port):
        # Placeholder for LLM Server Websocket and others
        self.llm_server_websocket = None
        self.latest_prompt_request = None
        self.host = host
        self.port = port

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

    def initialize_server(self):
        web.run_app(self.app, host=self.host, port=self.port)

    def prompt_llm_server(self, prompt):

        # Create socket to talk to LLM server
        uri = "ws://localhost:8000/ws"
        self.llm_server_websocket = create_connection(uri)

        # Send prompt to LLM server
        print(f"Sending prompt to LLM server: {prompt}")
        self.llm_server_websocket.send(prompt)

        # Listen to LLM server until we receive </s> or no new
        # tokens have been received in a while
        while True:
            try:
                token = self.llm_server_websocket.recv()

                # Set timeout after first token:
                self.llm_server_websocket.sock.settimeout(5)

                if token:
                    if token == "</s>":
                        return
                    yield token
            except WebSocketTimeoutException:
                break

        if self.llm_server_websocket.connected:
            self.llm_server_websocket.close()

    def prompt_received(self, prompt):
        print("Message received:", prompt)

    def chat_restarted(self):
        print("Client requested chat to restart")

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

    def stream_to_ui(self, token, new_card=False):
        data = {"token": token, "new_card": new_card}
        url = "http://127.0.0.1:8002/stream_to_ui"
        requests.post(url, json=data)


class LocalLLM(CustomLLM):
    prompt_llm_server: Callable = None
    stream_to_ui: Callable = None
    context_window: int = 3900
    num_output: int = 256
    model_name: str = "custom"

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
        for token in self.prompt_llm_server(prompt=prompt):

            # Stream token to UI
            self.stream_to_ui(token, new_card=new_card)
            new_card = False

            response += token
            yield CompletionResponse(text=response, delta=token)
