from typing import Any, Callable
import asyncio
import websockets
from aiohttp import web
from llama_index.core.llms import LLMMetadata


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
            if not self.llm_server_websocket.closed:
                asyncio.ensure_future(self.llm_server_websocket.close())

    def initialize_server(self):
        web.run_app(self.app, host=self.host, port=self.port)

    async def prompt_llm_server(self, prompt, stream_to_ui=True):

        # Send prompt to LLM server
        await self.llm_server_websocket.send(prompt)

        # Prepare stream
        if stream_to_ui:
            ui_response = web.StreamResponse()
            await ui_response.prepare(self.latest_prompt_request)

        # Listen to LLM server
        response = ""
        timeout_duration = 2
        while True:
            try:
                # Receive messages
                token = await asyncio.wait_for(
                    self.llm_server_websocket.recv(), timeout=timeout_duration
                )
                if token:
                    if token == "</s>":
                        break
                    if stream_to_ui:
                        encoded_token = (token.replace("\n", "\u0000") + "\n").encode(
                            "utf-8"
                        )
                        await ui_response.write(encoded_token)
                    response += token
            except asyncio.TimeoutError:
                # No token received for a while. Ending communication
                break

        if stream_to_ui:
            # Signal the end of the stream
            await ui_response.write_eof()
        return response

    async def prompt_received(self, prompt):
        print("Message received:", prompt)

    async def chat_restarted(self):
        print("Client requested chat to restart")

    async def _on_prompt_received(self, ui_request):
        data = await ui_request.json()
        self.latest_prompt_request = ui_request
        await self.prompt_received(data["prompt"])

    async def _on_chat_restarted(self, _):
        await self.chat_restarted()
        return web.Response()

    async def _on_load_llm(self, ui_request):
        data = await ui_request.json()
        print(f"Client requested to load LLM ({data['model']})")

        # Create socket to talk to LLM server
        uri = "ws://localhost:8000/ws"
        self.llm_server_websocket = await websockets.connect(uri)

        response = {"status": "Success"}
        return web.json_response(response)


class LocalLLM:
    prompt_llm_server: Callable = None

    def __init__(self, prompt_llm_server: Callable = None):
        self.prompt_llm_server = prompt_llm_server

    async def astream(
        self,
        prompt: Any,
        **prompt_args: Any,
    ) -> str:

        # Format prompt
        prompt.conditionals[0] = (lambda _: False,) + prompt.conditionals[0][1:]
        formatted_prompt = prompt.format(prompt, **prompt_args)

        # Prompt LLM and steam content to UI
        return await self.prompt_llm_server(prompt=formatted_prompt, stream_to_ui=True)

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata()
