from aiohttp import web
import asyncio

from transformers import pipeline


class MyAgent:
    def __init__(self):
        self.pipe = None

    async def on_message_received(self, request):
        data = await request.json()
        print("Message received:", data)

        # Prompt llm
        llm_response = self.pipe(data["prompt"], max_new_tokens=32)[0]["generated_text"]

        # Stream response back
        # Note: if your pipeline enables real streaming, simply send them as they become available
        response = web.StreamResponse()
        await response.prepare(request)
        for token in llm_response.split(" "):
            print(f"Streaming: {token}")
            await response.write(f"{token}\n".encode("utf-8"))
            await asyncio.sleep(0.12)
        await response.write_eof()
        return response

    async def on_chat_restarted(self, request):
        print("Client requested chat to restart")
        response = {"status": "Success"}
        return web.json_response(response)

    async def on_load_llm(self, request):
        data = await request.json()
        print(f"Client requested to load LLM ({data['model']})")
        checkpoint = {"BlenderBot-400M": "facebook/blenderbot-400M-distill", "Phi 3 Mini 4k": "microsoft/Phi-3-mini-4k-instruct"}
        self.pipe = pipeline("text2text-generation", model=checkpoint[data["model"]])
        response = {"status": "Success"}
        return web.json_response(response)


app = web.Application()
agent = MyAgent()
app.router.add_post("/message", agent.on_message_received)
app.router.add_post("/restart", agent.on_chat_restarted)
app.router.add_post("/load_llm", agent.on_load_llm)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8001)
