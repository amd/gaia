from aiohttp import web
import asyncio
import websockets
from threading import Thread
import lemonade
from lemonade.tools.general import Serve
from lemonade.tools.ort_genai.dml_og import DmlOgLoad


def launch_llm_server():
    state = lemonade.initialize_state(
        eval_id=f"gaiaexe_server",
    )
    state = DmlOgLoad().run(
        state,
        checkpoint="microsoft/Phi-3-mini-4k-instruct",
        device="igpu",
        dtype="int4",
    )
    state = Serve().run(state, max_new_tokens=60)


class MyAgent:
    def __init__(self):
        self.llm_server_websocket = None

    def __del__(self):
        if self.llm_server_websocket is not None:
            self.llm_server_websocket.close()

    async def prompt_llm_server(self, prompt, ui_request):

        await self.llm_server_websocket.send(prompt)

        timeout_duration = 2

        response = web.StreamResponse()
        await response.prepare(ui_request)
        while True:
            try:
                # Receive messages
                token = await asyncio.wait_for(
                    self.llm_server_websocket.recv(), timeout=timeout_duration
                )
                if token:
                    print(token)
                    if token == "</s>":
                        break
                    await response.write(f"{token}\n".encode("utf-8"))
            except asyncio.TimeoutError:
                # No token received for a while. Ending communication
                break
        await response.write_eof()
        return response

    async def on_message_received(self, ui_request):
        data = await ui_request.json()
        print("Message received:", data)

        # Prompt llm
        await self.prompt_llm_server(data["prompt"], ui_request)

    async def on_chat_restarted(self, ui_request):
        print("Client requested chat to restart")
        response = {"status": "Success"}
        return web.json_response(response)

    async def on_load_llm(self, ui_request):
        data = await ui_request.json()
        print(f"Client requested to load LLM ({data['model']})")
        llm_thread = Thread(target=launch_llm_server)
        llm_thread.daemon = True
        llm_thread.start()

        uri = "ws://localhost:8000/ws"

        # async with websockets.connect(uri) as llm_server_websocket:
        self.llm_server_websocket = await websockets.connect(uri)
        response = {"status": "Success"}
        return web.json_response(response)


app = web.Application()
agent = MyAgent()
app.router.add_post("/message", agent.on_message_received)
app.router.add_post("/restart", agent.on_chat_restarted)
app.router.add_post("/load_llm", agent.on_load_llm)


def run():
    try:
        web.run_app(app, host="localhost", port=8001)
    except Exception as error:
        raise error


if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8001)
