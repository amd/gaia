import re
import time
import sys
import openai
import os
import subprocess
import base64
from dotenv import load_dotenv
from io import StringIO

from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI
from llama_index.core.agent import ReActAgent
from llama_index.core import PromptTemplate
from llama_index.core import Document, VectorStoreIndex

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity

sdxl_prompts = """
1. Warm portrait: portrait of a pretty blonde woman, a flower crown, earthy makeup, flowing maxi dress with colorful patterns and fringe, a sunset or nature scene, green and gold color scheme
2. Old man portrait: photorealistic, visionary portrait of a dignified older man with weather-worn features, digitally enhanced, high contrast, chiaroscuro lighting technique, intimate, close-up, detailed, steady gaze, rendered in sepia tones, evoking rembrandt, timeless, expressive, highly detailed, sharp focus, high resolution
3. Interior: a living room, bright modern Scandinavian style house, large windows, magazine photoshoot, 8k, studio lighting
4. Closeup portrait: closeup portrait photo of beautiful goth woman, makeup, 8k uhd, high quality, dramatic, cinematic
5. Animal photo: close up photo of a rabbit, forest in spring, haze, halation, bloom, dramatic atmosphere, centred, rule of thirds, 200mm 1.4f macro shot
6. Kodak portrait: happy indian girl, portrait photography, beautiful, morning sunlight, smooth light, shot on kodak portra 200, film grain, nostalgic mood
7. Luxury product: breathtaking shot of a bag, luxury product style, elegant, sophisticated, high-end, luxurious, professional, highly detailed
8. Noir: johnny depp photo portrait, film noir style, monochrome, high contrast, dramatic shadows, 1940s style, mysterious, cinematic
9. Animal photo: a cat under the snow with blue eyes, covered by snow, cinematic style, medium shot, professional photo, animal
10. Long exposure: long exposure photo of tokyo street, blurred motion, streaks of light, surreal, dreamy, ghosting effect, highly detailed
11. Cyberpunk photoshoot: a glamorous digital magazine photoshoot, a fashionable model wearing avant-garde clothing, set in a futuristic cyberpunk roof-top environment, with a neon-lit city background, intricate high fashion details, backlit by vibrant city glow, Vogue fashion photography
12. Drink photography: freshly made hot floral tea in glass kettle on the table, angled shot, midday warm, Nikon D850 105mm, close-up
13. Happy portrait: masterpiece, best quality, girl, collarbone, wavy hair, looking at viewer, blurry foreground, upper body, necklace, contemporary, plain pants, intricate, print, pattern, ponytail, freckles, red hair, dappled sunlight, smile, happy
14. Neon symbol: symbol of a stylized pink cat head with sunglasses, glowing, neon, logo for a game, cyberpunk, vector, dark background with black and blue abstract shadows, cartoon, simple
15. Comicbook: a girl sitting in the cafe, comic, graphic illustration, comic art, graphic novel art, vibrant, highly detailed, colored, 2d minimalistic
16. Pixel: haunted house, pixel-art, low-res, blocky, pixel art style, 8-bit graphics, colorful
17. Pixar: batman, cute modern disney style, Pixar 3d portrait, ultra detailed, gorgeous, 3d zbrush, trending on dribbble, 8k render
18. Watercolor: cinnamon bun on the plate, watercolor painting, detailed, brush strokes, light palette, light, cozy
19. Clipart: clipart style, cute, playful scene, playful dog chasing a frisbee, with a bright, happy color palette, simple shapes, and thick, bold lines, hand-drawn digital illustration, highly detailed, perfect for children’s book, colorful, whimsical, Artstation HQ, digital art
20. Anime astronaut: a girl astronaut exploring the cosmos, floating among planets and stars, high quality detail, , anime screencap, studio ghibli style, illustration, high contrast, masterpiece, best quality
21. Psychedelic: autumn forest landscape, psychedelic style, vibrant colors, swirling patterns, abstract forms, surreal, trippy, colorful
22. Double exposure effect: double exposure portrait of a beautiful woman with brown hair and a snowy tree under the bright moonlight by Dave White, Conrad Roset, Brandon Kidwell, Andreas Lie, Dan Mountford, Agnes Cecile, splash art, winter colours, gouache, triadic colours, thick opaque strokes, brocade, depth of field, hyperdetailed, whimsimcal, amazing depth, dynamic, dreamy masterwork
23. Vaporwave: girl with pink hair, vaporwave style, retro aesthetic, cyberpunk, vibrant, neon colors, vintage 80s and 90s style, highly detailed
24. Lowpoly: a lion, colorful, low-poly, cyan and orange eyes, poly-hd, 3d, low-poly game art, polygon mesh, jagged, blocky, wireframe edges, centered composition
25. Flat illustration: flat vector illustration of a house, clear off background, minimalistic, clean lines, adobe illustrator
26. Sticker: vibrant and dynamic die cut sticker design, portraying a wolfs head interlaced with cosmic galaxies, AI, stickers, high contrast, bright neon colors, top-view, high resolution, vector art, detailed stylization, modern graphic art, unique, opaque, weather resistant, UV laminated, white background
27. Product prototype: a sleek, ultra-thin, high resolution bezel-less monitor mockup, realistic, modern, detailed, vibrant colors, glossy finish, floating design, lowlight, art by peter mohrbacher and donato giancola, digital illustration, trending on Artstation, high-tech, smooth, minimalist workstation background, crisp reflection on screen, soft lighting
28. Logo: logo of mountain, hike, modern, colorful, rounded, 2d concept, white off background
29. Icon: a guitar, 2d minimalistic icon, flat vector illustration, digital, smooth shadows, design asset
30. Tattoo design: a tattoo design, a small bird, minimalistic, black and white drawing, detailed, 8k
31. Fashion design: extravagant high fashion show concept featuring elaborate costumes with feathered details and sparkling jewels, runway, fashion designer inspiration, style of gaultier and gianni versace, deep vibrant colors, strong directional light sources, catwalk, heavy diffusion of light, highly detailed, top trend in vogue, art by lois van baarle and loish and ross tran and rossdraws, Artstation, front row view, full scene, elegant, glamorous, intricate, sharp focus, haute couture
32. Gradient: gradient background, pastel colors, background reference, empty, smooth transition, horizontal layout, visually pleasing, calming, relaxing
33. Fantasy elf: ethereal fantasy concept art of an elf, magnificent, celestial, ethereal, painterly, epic, majestic, magical, fantasy art, cover art, dreamy
34. Post apocalypse: abandoned city with ruined buildings, long deserted streets, cars aged by time, trees, flowers, scattered leaves, empty street, vibrant colors, lineart
35. Dragon: a fantasy illustration of a majestic, ancient dragon with an opalescent scales, amidst glowing enchanted forest, illuminated by magical moonlight, intricate, highly detailed, rich textures, mystical ambiance, digital painting, vivid colors, ethereal, artstation trending, detailed matte background, sharp focus, smooth, majestic, by Todd Lockwood, Donato Giancola, Frank Frazetta, and Brom
36. Pianist: pianist playing somber music, abstract style, non-representational, colors and shapes, expression of feelings, imaginative, highly detailed
37. Griffon: a highly detailed, full body depiction of a griffin, showcasing a mix of lion’s body, eagle’s head and wings in a dramatic forest setting under a warm evening sky, smooth, vibrant, digital painting, matte, sharp focus, by artgerm, greg rutkowski and zdislav beksinski, with a hint of magical realism, exquisite detailing, including feathers, fur, and talons, where the griffin is poised to leap into flight, trending on Artstation, saving the image in 4K UHD quality
38. Jedi cat: a master jedi cat in star wars holding a lightsaber, wearing a jedi cloak hood, dramatic, cinematic lighting
39. Wellness and calm: uplifting wellness-inspired illustration showing a serene yoga session at sunrise on a misty mountain peak, warm color palette, tranquil, soft focus, smooth gradients, digital painting, highly detailed, calming, positive energy flow, nature elements, mindfulness theme, art by Leonid Afremov, DeviantArt
40. Frozen rose: a frozen cosmic rose, the petals glitter with a crystalline shimmer, swirling nebulas, 8k unreal engine photorealism, ethereal lighting, red, nighttime, darkness, surreal art
41. RPG character: full body, cat dressed as a Viking, with weapon in his paws, battle coloring, glow hyper-detail, hyper-realism, cinematic
42. Plush toy: adorable concept illustration of a plush animal peacefully sitting on a child’s bed, soft lighting, gentle texture, dreamy atmosphere, pastel tones, matte finish, wide shot, by Yuko Shimizu, by Marc Brunet, by Joshua Middleton, children illustration, highly detailed, trending on artstation, fluffy, smooth, digital art, sharp focus
"""
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

sdxl_doc = Document(text=sdxl_prompts)
sdxl_index = VectorStoreIndex.from_documents([sdxl_doc], show_progress=True)
sdxl_query_engine = sdxl_index.as_query_engine()


def sdxl_prompt_generate(query: str) -> str:
    """A function that receives a query from a user and produces a prompt that is used for SDXL image generation"""
    return sdxl_query_engine.query(query)


def sdxl_image_generate(prompt: str):
    """A function that generates an SDXL image given an input prompt"""
    with open("./img/bunny.png", "rb") as file:
        image_data = file.read()
        base64_image = base64.b64encode(image_data).decode("ascii")
    return base64_image


def remove_color_formatting(text):
    # ANSI escape codes for color formatting
    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_escape.sub("", text)


def custom_query(agent, query):

    # Redirect stdout to a variable
    original_stdout = sys.stdout
    captured_output = StringIO()
    sys.stdout = captured_output

    # Query agent
    start_time = time.time()
    agent.chat(query)
    elapsed_time = time.time() - start_time

    # Restore the original stdout
    sys.stdout = original_stdout

    # Parse response
    messages = []
    response = remove_color_formatting(captured_output.getvalue())
    tps = len(response.split()) / elapsed_time
    valid_message_types = ["thought", "action input", "observation", "answer"]
    for line in response.split("\n"):
        message_type = line.split(":")[0].lower()
        if message_type in valid_message_types:
            # Only messages of type "message" appear in the main chat window
            if message_type == "answer":
                message_type = "message"
            message_content = " ".join(line.split(":")[1:])
            messages.append([message_type, message_content, tps])

    # Print captured message
    print(captured_output.getvalue())

    return messages


sdxl_prompt_tool = FunctionTool.from_defaults(fn=sdxl_prompt_generate)
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
agent = ReActAgent.from_tools([sdxl_prompt_tool], llm=llm, verbose=True)
agent.update_prompts({"agent_worker:system_prompt": react_system_prompt})


class MyBot(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext):
        # Send message to agent and get response
        agent_response = custom_query(agent, turn_context.activity.text)

        # Send message to Demo Hub
        for message in agent_response:
            message_type, message_content, message_tps = message
            act = Activity(
                type=message_type,
                text=message_content,
                channel_data={"tokens_per_second": message_tps},
            )
            await turn_context.send_activity(act)

            if "message" in message[0].lower() and ("prompt" in message[1].lower() or "image" in message[1].lower() or "photo" in message[1].lower()):
                base64_image = sdxl_image_generate(message)
                act = Activity(
                    type="message",
                    attachments=[
                        {
                            "contentType": "image/png",
                            "contentUrl": f"data:image/png;base64,{base64_image}",
                            "name": "bunny.png",
                        }
                    ],
                )
                await turn_context.send_activity(act)

    async def on_members_added_activity(self, members_added: ChannelAccount, turn_context: TurnContext):
        pass
