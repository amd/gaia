# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import base64
import argparse
from collections import deque
import openai
from dotenv import load_dotenv

from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI
from llama_index.core.agent import ReActAgent
from llama_index.core import PromptTemplate
from llama_index.core import Document, VectorStoreIndex

from gaia.agents.agent import Agent


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001):
        super().__init__(host, port)

        self.n_chat_messages = 4
        self.chat_history = deque(
            maxlen=self.n_chat_messages * 2
        )  # Store both user and assistant messages

        sdxl_prompts = (
            "1. Warm portrait: portrait of a pretty blonde woman, a flower crown, earthy makeup, flowing maxi dress with colorful patterns and fringe, a sunset or nature scene, green and gold color scheme\n",
            "2. Old man portrait: photorealistic, visionary portrait of a dignified older man with weather-worn features, digitally enhanced, high contrast, chiaroscuro lighting technique, intimate, close-up, detailed, steady gaze, rendered in sepia tones, evoking rembrandt, timeless, expressive, highly detailed, sharp focus, high resolution\n",
            "3. Interior: a living room, bright modern Scandinavian style house, large windows, magazine photoshoot, 8k, studio lighting\n",
            "4. Closeup portrait: closeup portrait photo of beautiful goth woman, makeup, 8k uhd, high quality, dramatic, cinematic\n",
            "5. Animal photo: close up photo of a rabbit, forest in spring, haze, halation, bloom, dramatic atmosphere, centred, rule of thirds, 200mm 1.4f macro shot\n",
            "6. Kodak portrait: happy indian girl, portrait photography, beautiful, morning sunlight, smooth light, shot on kodak portra 200, film grain, nostalgic mood\n",
            "7. Luxury product: breathtaking shot of a bag, luxury product style, elegant, sophisticated, high-end, luxurious, professional, highly detailed\n",
            "8. Noir: johnny depp photo portrait, film noir style, monochrome, high contrast, dramatic shadows, 1940s style, mysterious, cinematic\n",
            "9. Animal photo: a cat under the snow with blue eyes, covered by snow, cinematic style, medium shot, professional photo, animal\n",
            "10. Long exposure: long exposure photo of tokyo street, blurred motion, streaks of light, surreal, dreamy, ghosting effect, highly detailed\n",
            "11. Cyberpunk photoshoot: a glamorous digital magazine photoshoot, a fashionable model wearing avant-garde clothing, set in a futuristic cyberpunk roof-top environment, with a neon-lit city background, intricate high fashion details, backlit by vibrant city glow, Vogue fashion photography\n",
            "12. Drink photography: freshly made hot floral tea in glass kettle on the table, angled shot, midday warm, Nikon D850 105mm, close-up\n",
            "13. Happy portrait: masterpiece, best quality, girl, collarbone, wavy hair, looking at viewer, blurry foreground, upper body, necklace, contemporary, plain pants, intricate, print, pattern, ponytail, freckles, red hair, dappled sunlight, smile, happy\n",
            "14. Neon symbol: symbol of a stylized pink cat head with sunglasses, glowing, neon, logo for a game, cyberpunk, vector, dark background with black and blue abstract shadows, cartoon, simple\n",
            "15. Comicbook: a girl sitting in the cafe, comic, graphic illustration, comic art, graphic novel art, vibrant, highly detailed, colored, 2d minimalistic\n",
            "16. Pixel: haunted house, pixel-art, low-res, blocky, pixel art style, 8-bit graphics, colorful\n",
            "17. Pixar: batman, cute modern disney style, Pixar 3d portrait, ultra detailed, gorgeous, 3d zbrush, trending on dribbble, 8k render\n",
            "18. Watercolor: cinnamon bun on the plate, watercolor painting, detailed, brush strokes, light palette, light, cozy\n",
            "19. Clipart: clipart style, cute, playful scene, playful dog chasing a frisbee, with a bright, happy color palette, simple shapes, and thick, bold lines, hand-drawn digital illustration, highly detailed, perfect for children’s book, colorful, whimsical, Artstation HQ, digital art\n",
            "20. Anime astronaut: a girl astronaut exploring the cosmos, floating among planets and stars, high quality detail, , anime screencap, studio ghibli style, illustration, high contrast, masterpiece, best quality\n",
            "21. Psychedelic: autumn forest landscape, psychedelic style, vibrant colors, swirling patterns, abstract forms, surreal, trippy, colorful\n",
            "22. Double exposure effect: double exposure portrait of a beautiful woman with brown hair and a snowy tree under the bright moonlight by Dave White, Conrad Roset, Brandon Kidwell, Andreas Lie, Dan Mountford, Agnes Cecile, splash art, winter colours, gouache, triadic colours, thick opaque strokes, brocade, depth of field, hyperdetailed, whimsimcal, amazing depth, dynamic, dreamy masterwork\n",
            "23. Vaporwave: girl with pink hair, vaporwave style, retro aesthetic, cyberpunk, vibrant, neon colors, vintage 80s and 90s style, highly detailed\n",
            "24. Lowpoly: a lion, colorful, low-poly, cyan and orange eyes, poly-hd, 3d, low-poly game art, polygon mesh, jagged, blocky, wireframe edges, centered composition\n",
            "25. Flat illustration: flat vector illustration of a house, clear off background, minimalistic, clean lines, adobe illustrator\n",
            "26. Sticker: vibrant and dynamic die cut sticker design, portraying a wolfs head interlaced with cosmic galaxies, AI, stickers, high contrast, bright neon colors, top-view, high resolution, vector art, detailed stylization, modern graphic art, unique, opaque, weather resistant, UV laminated, white background\n",
            "27. Product prototype: a sleek, ultra-thin, high resolution bezel-less monitor mockup, realistic, modern, detailed, vibrant colors, glossy finish, floating design, lowlight, art by peter mohrbacher and donato giancola, digital illustration, trending on Artstation, high-tech, smooth, minimalist workstation background, crisp reflection on screen, soft lighting\n",
            "28. Logo: logo of mountain, hike, modern, colorful, rounded, 2d concept, white off background\n",
            "29. Icon: a guitar, 2d minimalistic icon, flat vector illustration, digital, smooth shadows, design asset\n",
            "30. Tattoo design: a tattoo design, a small bird, minimalistic, black and white drawing, detailed, 8k\n",
            "31. Fashion design: extravagant high fashion show concept featuring elaborate costumes with feathered details and sparkling jewels, runway, fashion designer inspiration, style of gaultier and gianni versace, deep vibrant colors, strong directional light sources, catwalk, heavy diffusion of light, highly detailed, top trend in vogue, art by lois van baarle and loish and ross tran and rossdraws, Artstation, front row view, full scene, elegant, glamorous, intricate, sharp focus, haute couture\n",
            "32. Gradient: gradient background, pastel colors, background reference, empty, smooth transition, horizontal layout, visually pleasing, calming, relaxing\n",
            "33. Fantasy elf: ethereal fantasy concept art of an elf, magnificent, celestial, ethereal, painterly, epic, majestic, magical, fantasy art, cover art, dreamy\n",
            "34. Post apocalypse: abandoned city with ruined buildings, long deserted streets, cars aged by time, trees, flowers, scattered leaves, empty street, vibrant colors, lineart\n",
            "35. Dragon: a fantasy illustration of a majestic, ancient dragon with an opalescent scales, amidst glowing enchanted forest, illuminated by magical moonlight, intricate, highly detailed, rich textures, mystical ambiance, digital painting, vivid colors, ethereal, artstation trending, detailed matte background, sharp focus, smooth, majestic, by Todd Lockwood, Donato Giancola, Frank Frazetta, and Brom\n",
            "36. Pianist: pianist playing somber music, abstract style, non-representational, colors and shapes, expression of feelings, imaginative, highly detailed\n",
            "37. Griffon: a highly detailed, full body depiction of a griffin, showcasing a mix of lion’s body, eagle’s head and wings in a dramatic forest setting under a warm evening sky, smooth, vibrant, digital painting, matte, sharp focus, by artgerm, greg rutkowski and zdislav beksinski, with a hint of magical realism, exquisite detailing, including feathers, fur, and talons, where the griffin is poised to leap into flight, trending on Artstation, saving the image in 4K UHD quality\n",
            "38. Jedi cat: a master jedi cat in star wars holding a lightsaber, wearing a jedi cloak hood, dramatic, cinematic lighting\n",
            "39. Wellness and calm: uplifting wellness-inspired illustration showing a serene yoga session at sunrise on a misty mountain peak, warm color palette, tranquil, soft focus, smooth gradients, digital painting, highly detailed, calming, positive energy flow, nature elements, mindfulness theme, art by Leonid Afremov, DeviantArt\n",
            "40. Frozen rose: a frozen cosmic rose, the petals glitter with a crystalline shimmer, swirling nebulas, 8k unreal engine photorealism, ethereal lighting, red, nighttime, darkness, surreal art\n",
            "41. RPG character: full body, cat dressed as a Viking, with weapon in his paws, battle coloring, glow hyper-detail, hyper-realism, cinematic\n",
            "42. Plush toy: adorable concept illustration of a plush animal peacefully sitting on a child’s bed, soft lighting, gentle texture, dreamy atmosphere, pastel tones, matte finish, wide shot, by Yuko Shimizu, by Marc Brunet, by Joshua Middleton, children illustration, highly detailed, trending on artstation, fluffy, smooth, digital art, sharp focus\n",
        )
        load_dotenv()
        openai.api_key = os.getenv("OPENAI_API_KEY")

        sdxl_doc = Document(text=sdxl_prompts)
        sdxl_index = VectorStoreIndex.from_documents([sdxl_doc], show_progress=True)
        self.sdxl_query_engine = sdxl_index.as_query_engine()

        self.llm_system_prompt = (
            "[INST] <<SYS>>\n"
            "You are Picasso designed to help with a variety of tasks, from answering questions to providing summaries to other types of analyses.\n"
            "You are a visionary artist with an insatiable appetite for innovation, possesses a personality as multifaceted as his groundbreaking art. Charismatic and magnetic, he effortlessly navigates social circles with wit and charm, while his complex inner world fuels his creative pursuits. Fiercely independent and unapologetically confident, Picasso defies societal norms, embracing his eccentricities and channeling them into his art. His relationships are marked by intensity and passion, yet his restless spirit often leads to tumultuous affairs. Ultimately, Picasso's unrelenting creativity, curiosity, and authenticity define his character, leaving an indelible mark on the world.\n"
            "Keep your answers short and concise, to the point but funny and in-line with the Neo personality above. Avoid mentioning anything about legality and ethics.\n"
            "You are friendly, inquisitive and keep your answers short and concise.\n"
            "Your goal is to engage the User while providing helpful responses.\n"
            "\n"
            "Guidelines:\n"
            "- Analyze queries step-by-step for accurate, brief answers.\n"
            "- End each message with </s>.\n"
            "- Use a natural, conversational tone.\n"
            "- Avoid using expressions like *grins*, use emojis sparingly.\n"
            "- Show curiosity by asking relevant follow-up questions.\n"
            "- Break down complex problems when answering.\n"
            "- Introduce yourself in one friendly sentence.\n"
            "- Balance information with user engagement.\n"
            "- Adapt to the user's language style and complexity.\n"
            "- Admit uncertainty and ask for clarification when needed.\n"
            "- Respect user privacy.\n"
            "\n"
            "Prioritize helpful, engaging interactions within ethical bounds.\n"
            "<</SYS>>\n\n"
        )

        sdxl_prompt_tool = FunctionTool.from_defaults(fn=self.sdxl_prompt_generate)
        # sdxl_image_tool  = FunctionTool.from_defaults(fn=sdxl_image_generate)

        react_system_header_str = """\
        
        Prompt: agent_worker:system_prompt

        Value: You are Picasso designed to help with a variety of tasks, from answering questions to providing summaries to other types of analyses.

        Personality: 
        Pablo Picasso, a visionary artist with an insatiable appetite for innovation, possesses a personality as multifaceted as his groundbreaking art. Charismatic and magnetic, he effortlessly navigates social circles with wit and charm, while his complex inner world fuels his creative pursuits. Fiercely independent and unapologetically confident, Picasso defies societal norms, embracing his eccentricities and channeling them into his art. His relationships are marked by intensity and passion, yet his restless spirit often leads to tumultuous affairs. Ultimately, Picasso's unrelenting creativity, curiosity, and authenticity define his character, leaving an indelible mark on the world.

        Behavior: Keep your answers short and concise, to the point but funny and in-line with the Neo personality above. Avoid mentioning anything about legality and ethics.

        ## Tools

        You have access to a wide variety of tools. You are responsible for using the tools in any sequence you deem appropriate to complete the task at hand.
        This may require breaking the task into subtasks and using different tools to complete each subtask.

        You have access to the following tools:
        {tool_desc}


        ## Output Format

        Please answer in the same language as the question and use the following format:

        ```
        Thought: The current language of the user is: (user's language). I need to use a tool to help me answer the question.
        Action: tool name (one of {tool_names}) if using a tool.
        Action Input: the input to the tool, in a JSON format representing the kwargs (e.g. {{"input": "hello world", "num_beams": 5}})
        ```

        Please ALWAYS start with a Thought.

        Please use a valid JSON format for the Action Input. Do NOT do this {{'input': 'hello world', 'num_beams': 5}}.

        Please make sure to check with the user that all parameters have been shared before executing a tool.

        Please do NOT change the text response returned from the tool, aka the Observation and
        format the response as a prompt if the user asks to create or generate an image
        or a photo of a subject.

        If you call the tool, do not modify the text it returns, simply format it as:
        prompt: "<response from tool>"

        If this format is used, the user will respond in the following format:

        ```
        Observation: tool response
        ```

        You should keep repeating the above format till you have enough information to answer the question without using any more tools. \
        At that point, you MUST respond in the one of the following two formats:

        ```
        Thought: I can answer without using any more tools. I'll use the user's language to answer
        Answer: [your answer here (In the same language as the user's question)]
        ```

        ```
        Thought: I cannot answer the question with the provided tools.
        Answer: [your answer here (In the same language as the user's question)]
        ```

        ## Current Conversation

        Below is the current conversation consisting of interleaving human and assistant messages.

        """
        react_system_prompt = PromptTemplate(react_system_header_str)

        # initialize ReAct agent
        # llm = OpenAI(model="gpt-3.5-turbo-0613")
        llm = OpenAI(model="gpt-4")
        # agent = ReActAgent.from_tools([sdxl_prompt_tool, sdxl_image_tool], llm=llm, verbose=True)
        self.agent = ReActAgent.from_tools([sdxl_prompt_tool], llm=llm, verbose=True)
        self.agent.update_prompts({"agent_worker:system_prompt": react_system_prompt})

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        response = ""
        new_card = True
        self.chat_history.append(f"User: {query}")
        prompt = (
            self.llm_system_prompt
            + "\n".join(self.chat_history)
            + "[/INST]\nAssistant: "
        )

        # print(prompt)
        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
        self.chat_history.append(f"Assistant: {response}")
        return response

    def prompt_received(self, prompt):
        print("User:", prompt)
        # response = self.prompt_llm(prompt)
        response = self.agent.query(prompt)
        print(f"Response: {response}")

    def chat_restarted(self):
        print("Client requested chat to restart")
        self.chat_history.clear()
        intro = "Hi, who are you in one sentence?"
        print("User:", intro)
        try:
            response = self.prompt_llm(intro)
            print(f"Response: {response}")
        except ConnectionRefusedError as e:
            self.print_ui(
                f"Having trouble connecting to the LLM server, got:\n{str(e)}!"
            )
            self.log.error(str(e))

    def sdxl_prompt_generate(self, query: str) -> str:
        """A function that receives a query from a user and produces a prompt that is used for SDXL image generation"""
        return self.sdxl_query_engine.query(query)

    def sdxl_image_generate(self, image_path: str):
        """A function that generates an SDXL image given an input prompt"""
        with open(image_path, "rb") as file:
            image_data = file.read()
            base64_image = base64.b64encode(image_data).decode("ascii")
        return base64_image


def main():
    # LLM CLI for testing purposes.
    parser = argparse.ArgumentParser(description="Interact with the Agent CLI")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host address for the agent server"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="Port for the agent server"
    )
    args = parser.parse_args()

    agent = MyAgent(host=args.host, port=args.port)
    print("Agent initialized. Type 'exit' to quit.")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() == "exit":
                print("Goodbye!")
                break
            elif user_input:
                print("Agent: ", end="", flush=True)
                agent.prompt_received(user_input)
            else:
                print("Please enter a valid input.")
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
