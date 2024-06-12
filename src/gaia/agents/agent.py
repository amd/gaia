from aiohttp import web
import asyncio
import websockets


class Agent:
    def __init__(self, host, port):
        # Initialize Agent Server
        self.app = web.Application()
        self.app.router.add_post("/message", self.on_message_received)
        self.app.router.add_post("/restart", self.on_chat_restarted)
        self.app.router.add_post("/load_llm", self.on_load_llm)
        web.run_app(self.app, host="127.0.0.1", port=8001)

        # Placeholder for LLM Server Websocket
        self.llm_server_websocket = None

    def __del__(self):
        # Ensure websocket gets closed when agent is deleted
        if self.llm_server_websocket:
            if not self.llm_server_websocket.closed:
                asyncio.ensure_future(self.llm_server_websocket.close())

    async def prompt_llm_server(self, prompt, ui_request, stream_to_ui=True):

        # Send prompt to LLM server
        await self.llm_server_websocket.send(prompt)

        # Prepare stream
        if stream_to_ui:
            ui_response = web.StreamResponse()
            await ui_response.prepare(ui_request)

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

    async def on_message_received(self, ui_request):
        data = await ui_request.json()
        print("Message received:", data)

    async def on_chat_restarted(self, ui_request):
        print("Client requested chat to restart")
        response = {"status": "Success"}
        return web.json_response(response)

    async def on_load_llm(self, ui_request):
        data = await ui_request.json()
        print(f"Client requested to load LLM ({data['model']})")

        # Create socket to talk to LLM server
        uri = "ws://localhost:8000/ws"
        self.llm_server_websocket = await websockets.connect(uri)

        response = {"status": "Success"}
        return web.json_response(response)
