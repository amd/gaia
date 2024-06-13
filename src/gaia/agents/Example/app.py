from gaia.agents.agent import Agent


class MyAgent(Agent):
    def __init__(self, host, port):
        super().__init__(host, port)

    async def prompt_received(self, prompt):
        print("Message received:", prompt)

        # Prompt LLM server and stream results directly to UI
        response = await self.prompt_llm_server(prompt, stream_to_ui=True)
        print(f"Message streamed: {response}")

    async def chat_restarted(self):
        print("Client requested chat to restart")


if __name__ == "__main__":
    agent = MyAgent(host="127.0.0.1", port=8001)
