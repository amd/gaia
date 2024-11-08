# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import time
import asyncio
from collections import OrderedDict
from websocket import create_connection
from websocket._exceptions import WebSocketTimeoutException
from aiohttp import web
import requests

from gaia.logger import get_logger
from gaia.interface.util import UIMessage
from gaia.llm.tokenizer import Tokenizer

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

        # performance stats
        # ttft = time-to-first-token
        self.stats = OrderedDict([
            ('in_tokens', None),
            ('ttft', None),
            ('out_tokens', None),
            ('tokens_per_sec', None)
        ])
        # last chunk in response
        self.last = False
        self.cli_mode = cli_mode
        self.tokenizer = Tokenizer("microsoft/Phi-3-mini-4k-instruct", cli_mode=self.cli_mode)

    def get_host_port(self):
        return self.host, self.port

    def set_cli_mode(self, mode: bool):
        self.log.debug(f"Setting `cli_mode` to {mode}.")
        self.cli_mode = mode

    async def create_app(self):
        app = web.Application()
        app.router.add_post("/prompt", self._on_prompt_received)
        app.router.add_post("/restart", self._on_chat_restarted)
        app.router.add_post("/load_llm", self._on_load_llm)
        app.router.add_get("/health", self._on_health_check)
        app.router.add_get("/stats", self._on_get_stats)
        return app

    def __del__(self):
        # Ensure websocket gets closed when agent is deleted
        if hasattr(self, 'llm_server_websocket') and self.llm_server_websocket is not None:
            if self.llm_server_websocket.connected:
                self.llm_server_websocket.close()

    def clear_stats(self):
        for key, _ in self.stats.items():
            self.stats[key] = None

    def count_tokens(self, text):
        return len(self.tokenizer.tokenizer.encode(text))

    def get_time_to_first_token(self):
        return self.stats['ttft']

    def get_tokens_per_second(self):
        return self.stats['tokens_per_sec']

    def initialize_server(self):
        max_retries = 5
        for _ in range(max_retries):
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self.app = loop.run_until_complete(self.create_app())
                web.run_app(self.app, host=self.host, port=self.port)
                break
            except OSError as e:
                if e.errno == 10048:  # Port is in use
                    self.log.warning(f"Port {self.port} is in use, make sure a service is not already running on this port.")
                else:
                    UIMessage.error(str(e), cli_mode=self.cli_mode)
            finally:
                loop.close()
        else:
            UIMessage.error(f"Unable to bind to port ({self.port}) after {max_retries} attempts with ip ({self.host}).\nMake sure to kill any existing services using port {self.port} before running GAIA.", cli_mode=self.cli_mode)


    def prompt_llm_server(self, prompt, stream_to_ui=True):
        try:
            ws = create_connection(self.llm_server_uri, timeout=None)
        except Exception as e:
            self.print(f"My brain is not working:```{e}```")
            return

        try:
            self.log.debug(f"Sending prompt to LLM server:\n{prompt}")
            prompt_tokens = self.count_tokens(prompt)
            start_time = time.perf_counter()
            ws.send(prompt)

            first_chunk = True
            new_card = True
            self.last_chunk = False
            full_response = ""

            self.clear_stats()
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
                        self.stats['in_tokens'] = prompt_tokens
                        self.stats['ttft'] = round(current_time - start_time, 2)
                        start_time = time.perf_counter()
                        first_chunk = False

                    if chunk:
                        if "</s>" in chunk:
                            chunk = chunk.replace("</s>", "")
                            full_response += chunk

                            total_time = current_time - start_time
                            out_tokens = self.count_tokens(full_response)
                            self.stats['out_tokens'] = out_tokens
                            self.stats['tokens_per_sec'] = round((out_tokens-1) / total_time, 2) if total_time > 0 and out_tokens > 1 else 0.00
                            self.last = True

                        if stream_to_ui:
                            self.stream_to_ui(chunk, new_card=new_card)
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
        response = self.prompt_received(data["prompt"])
        json_response = {"status": "success", "response": response, "stats": self.stats}
        return web.json_response(json_response)

    async def _on_chat_restarted(self, _):
        self.chat_restarted()
        return web.Response()

    async def _on_load_llm(self, ui_request):
        data = await ui_request.json()
        self.log.debug(f"Client requested to load LLM ({data['model']})")

        response = {"status": "success"}
        return web.json_response(response)

    async def _on_health_check(self, _):
        return web.json_response({"status": "ok"})

    async def _on_get_stats(self, _):
        response = {**self.stats, "status": "ok"}
        self.log.debug(response)
        return web.json_response(response)

    def stream_to_ui(self, chunk, new_card=True):
        if self.cli_mode:
            return chunk
        else:
            data = {"chunk": chunk, "new_card": new_card, "stats": self.stats, "last":self.last}
            url = "http://127.0.0.1:8002/stream_to_ui"
            try:
                requests.post(url, json=data)
            except requests.exceptions.ConnectionError:
                self.log.warning("Unable to connect to UI server. Falling back to console output.")

    def run(self):
        self.log.info("Launching Agent Server...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.app = loop.run_until_complete(self.create_app())
        web.run_app(self.app, host=self.host, port=self.port)

def launch_agent_server(model, agent_name="Chaty", host="127.0.0.1", port=8001, cli_mode=False):
    try:
        agent_module = __import__(f"gaia.agents.{agent_name}.app", fromlist=["MyAgent"])
        MyAgent = getattr(agent_module, "MyAgent")
        agent = MyAgent(model=model, host=host, port=port, cli_mode=cli_mode)
        agent.run()
        return agent
    except Exception as e:
        UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}", cli_mode=cli_mode)
        return
