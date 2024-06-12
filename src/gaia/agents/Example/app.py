from aiohttp import web
from gaia.agents.agent import Agent


class MyAgent(Agent):
    def __init__(self):
        super().__init__()

    async def on_message_received(self, ui_request):
        data = await ui_request.json()
        print("Message received:", data)

        # Prompt LLM server
        response = await self.prompt_llm_server(
            data["prompt"], ui_request, stream_to_ui=True
        )
        print(f"Message streamed: {response}")

    async def on_chat_restarted(self, ui_request):
        print("Client requested chat to restart")
        response = {"status": "Success"}
        return web.json_response(response)


if __name__ == "__main__":
    agent = MyAgent()
    web.run_app(agent.app, host="127.0.0.1", port=8001)
