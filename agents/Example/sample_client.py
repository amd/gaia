import aiohttp
import asyncio


async def prompt_llm(prompt):
    complete_response = ""
    async with aiohttp.ClientSession() as session:
        async with session.post("http://127.0.0.1:8001/message", json={"prompt": prompt}) as response:
            async for token in response.content:
                complete_response = complete_response + token.decode().strip() + " "
                print(complete_response)


async def request_restart():
    async with aiohttp.ClientSession() as session:
        async with session.post("http://127.0.0.1:8001/restart", json={}) as response:
            await response.json()


async def main():
    await request_restart()
    await prompt_llm("Hello, how are you today?")


if __name__ == "__main__":
    asyncio.run(main())
