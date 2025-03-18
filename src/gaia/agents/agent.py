# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import time
import asyncio
import importlib.util
import requests
from websocket import create_connection
from websocket._exceptions import WebSocketTimeoutException
from aiohttp import web

from gaia.logger import get_logger
from gaia.interface.util import UIMessage


class Agent:
    def __init__(self, model=None, host="127.0.0.1", port=8001, cli_mode=False):
        # Placeholder for LLM Server Websocket and others
        self.llm_server_uri = "ws://localhost:8000/ws"
        self.llm_server_websocket = None
        self.latest_prompt_request = None
        self.host = host
        self.port = port
        self.model = model
        self.app = None
        self.last_chunk = False
        self.log = get_logger(__name__)

        # last chunk in response
        self.last = False
        self.cli_mode = cli_mode

    def get_host_port(self):
        return self.host, self.port

    def set_cli_mode(self, mode: bool):
        self.log.debug(f"Setting `cli_mode` to {mode}.")
        self.cli_mode = mode

    async def create_app(self):
        app = web.Application()
        app.router.add_post("/prompt", self._on_prompt_received)
        app.router.add_post("/restart", self._on_chat_restarted)
        app.router.add_post("/welcome", self._on_welcome_message)
        app.router.add_get("/health", self._on_health_check)
        app.router.add_get("/ws", self._on_websocket_connect)
        return app

    async def _on_websocket_connect(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    if msg.data == "done":
                        break

                    self.log.info(
                        f"Processing request: {msg.data[:50]}..."
                    )  # Show first 50 chars
                    response = self.prompt_stream(msg.data)

                    # Stream each chunk immediately
                    chunk_count = 0
                    for chunk in response:
                        chunk_count += 1
                        await ws.send_str(chunk)
                        await ws.drain()  # Ensure the chunk is sent immediately
                        await asyncio.sleep(0)  # Yield to allow network IO

                    self.log.info(
                        f"Completed streaming response ({chunk_count} chunks)"
                    )
                    await ws.send_str("</s>")
                    await ws.drain()

                elif msg.type == web.WSMsgType.ERROR:
                    self.log.error(
                        f"WebSocket connection closed with exception {ws.exception()}"
                    )

        except Exception as e:
            self.log.error(f"Error in websocket connection: {str(e)}")
        finally:
            await ws.close()
        return ws

    def __del__(self):
        # Ensure websocket gets closed when agent is deleted
        if (
            hasattr(self, "llm_server_websocket")
            and self.llm_server_websocket is not None
        ):
            if self.llm_server_websocket.connected:
                self.llm_server_websocket.close()

    def initialize_server(self):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self.app = loop.run_until_complete(self.create_app())
                web.run_app(self.app, host=self.host, port=self.port)
                break
            except OSError as e:
                if e.errno == 10048:  # Port is in use
                    if attempt == max_retries - 1:
                        error_msg = f"Port {self.port} is in use. Please ensure no other services are running on this port before starting GAIA."
                        UIMessage.error(error_msg, cli_mode=self.cli_mode)
                        raise RuntimeError(error_msg)
                    self.log.warning(
                        f"Port {self.port} is in use, attempt {attempt + 1} of {max_retries}"
                    )
                else:
                    error_msg = f"Failed to start server: {str(e)}"
                    UIMessage.error(error_msg, cli_mode=self.cli_mode)
                    raise RuntimeError(error_msg)
            finally:
                loop.close()

    def prompt_llm_server(self, prompt, stream_to_ui=True):
        try:
            ws = create_connection(self.llm_server_uri, timeout=None)
        except Exception as e:
            self.print_ui(f"My brain is not working:```{e}```")
            return

        try:
            self.log.debug(f"Sending prompt to LLM server:\n{prompt}")
            ws.send(prompt)

            first_chunk = True
            new_card = True
            self.last_chunk = False
            full_response = ""

            self.last = False

            while True:
                try:
                    if first_chunk:
                        ws.sock.settimeout(None)  # No timeout for first chunk
                    else:
                        ws.sock.settimeout(5)  # 5 second timeout after first chunk

                    chunk = ws.recv()
                    if first_chunk:
                        first_chunk = False

                    if chunk:
                        if "</s>" in chunk:
                            chunk = chunk.replace("</s>", "")
                            full_response += chunk
                            self.last = True

                        if stream_to_ui:
                            self.stream_to_ui(
                                chunk, new_card=new_card, is_llm_response=True
                            )
                            new_card = False

                        full_response += chunk
                        yield chunk

                        if self.last:
                            break

                except WebSocketTimeoutException:
                    break
                except Exception as e:
                    UIMessage.error(str(e), cli_mode=self.cli_mode)
                    return

        finally:
            ws.close()

    def prompt_received(self, prompt):
        return f"Function prompt_received() not implemented. prompt: {prompt}"

    def chat_restarted(self):
        """Clear the agent's conversation history"""
        self.log.debug("Client requested chat to restart")

    def welcome_message(self):
        """Send the agent's welcome message"""
        self.log.debug("Client requested welcome message")
        return "Welcome! How can I help you today?"

    def print_ui(self, input_str: str):
        self.log.debug(input_str)
        input_lst = input_str.split(" ")
        input_len = len(input_lst)
        for i, word in enumerate(input_lst):
            new_card = i == 0
            self.last = i == (input_len - 1)
            self.stream_to_ui(f"{word} ", new_card=new_card)
            time.sleep(0.05)

    async def _on_prompt_received(self, ui_request):
        data = await ui_request.json()
        self.latest_prompt_request = ui_request
        response = self.prompt_received(data["prompt"])
        json_response = {"status": "success", "response": response}
        return web.json_response(json_response)

    async def _on_chat_restarted(self, _):
        """Handle chat restart request - just clear history"""
        self.chat_restarted()
        return web.Response()

    async def _on_welcome_message(self, _):
        """Handle welcome message request"""
        response = self.welcome_message()
        self.print_ui(response)
        return web.Response()

    async def _on_health_check(self, _):
        return web.json_response({"status": "ok"})

    def stream_to_ui(self, chunk, new_card=True, is_llm_response: bool = False):
        if self.cli_mode:
            return chunk
        else:
            data = {
                "chunk": chunk,
                "new_card": new_card,
                "last": self.last,
                "is_llm_response": is_llm_response,
            }
            url = "http://127.0.0.1:8002/stream_to_ui"
            try:
                requests.post(url, json=data)
            except requests.exceptions.ConnectionError:
                self.log.warning(
                    "Unable to connect to UI server. Falling back to console output."
                )

    def run(self):
        self.log.info("Launching Agent Server...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.app = loop.run_until_complete(self.create_app())
        web.run_app(self.app, host=self.host, port=self.port)

    def prompt_stream(self, prompt):
        """Stream responses from the LLM server.

        Args:
            prompt (str): The input prompt to send to the LLM

        Yields:
            str: Response chunks from the LLM server
        """
        self.log.debug(f"Streaming prompt to LLM server:\n{prompt}")
        return self.prompt_llm_server(prompt)


def launch_agent_server(
    model, agent_name="Chaty", host="127.0.0.1", port=8001, cli_mode=False
):
    try:
        # Add assertion to check if agent_name exists
        agent_path = f"gaia.agents.{agent_name}.app"
        spec = importlib.util.find_spec(agent_path)
        assert (
            spec is not None
        ), f"Agent '{agent_name}' not found. Please check the agent name and try again."

        agent_module = __import__(agent_path, fromlist=["MyAgent"])
        MyAgent = getattr(agent_module, "MyAgent")
        agent = MyAgent(model=model, host=host, port=port, cli_mode=cli_mode)
        agent.run()
        return agent
    except Exception as e:
        UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}", cli_mode=cli_mode)
        raise
