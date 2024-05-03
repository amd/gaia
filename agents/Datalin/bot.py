import os
import time
from botbuilder.core import (
    ActivityHandler,
    TurnContext,
    TurnContext,
)

from botbuilder.schema import ChannelAccount, Activity

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
import base64
import asyncio
from find_match import find_match
from split import split_onnx_model

torch.random.manual_seed(0)

model = AutoModelForCausalLM.from_pretrained(
    "microsoft/Phi-3-mini-4k-instruct",
    device_map="cuda",
    torch_dtype="auto",
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained("microsoft/Phi-3-mini-4k-instruct")

initial_greeting = "Hi Daniel, I'm Datalin, a local AI agent (and future RAG) that is here to help you with DAT's tools. I heard you have a model you wanted me to have a look at?"
messages = [
    {
        "role": "system",
        "content": "You are a helpful, funny digital assistant that doesn't talk about itself. You are here to help engineers at AMD to use tools provided by the DAT team. All of those tools aim at doing model analysis. Please provide safe, super concise and accurate information to the user. You are running locally, so all models are safe with you. To be able to help the user, you need that users send you a model. Once they send you a model they can ask you questions about the model. Always answer in less than 50 words.",
    },
    {"role": "assistant", "content": initial_greeting},
]

pipe = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
)

generation_args = {
    "max_new_tokens": 150,
    "return_full_text": False,
    "temperature": 0.0,
    "do_sample": False,
}


class MyBot(ActivityHandler):
    # See https://aka.ms/about-bot-activity-message to learn more about the message and other activity types.

    async def on_message_activity(self, turn_context: TurnContext):
        answer = ""
        if turn_context.activity.attachments:
            # Iterate through each attachment
            for attachment in turn_context.activity.attachments:
                # Check the content type of the attachment
                if attachment.name.endswith(".onnx"):
                    # Handle onnx
                    answer = "Nice! This looks like an onnx file. Let me see what I can do. Give me a sec..."
                    self.model_path = os.path.join(r"C:\Users\danie\Downloads", attachment.name)
                    asyncio.create_task(self.process_attachment(turn_context, self.model_path))
                else:
                    answer = "Ugh. I can only handle .onnx files. Can you send me in that format?"

        else:
            # Append user message to context
            messages.append(
                {
                    "role": "user",
                    "content": turn_context.activity.text,
                }
            )

            if "split" and "subgraphs" in turn_context.activity.text:
                answer = "Sure. Give me a few seconds to try it..."
                request = turn_context.activity.text.split()
                try:
                    subgraphs = int(request[request.index("subgraphs") - 1])
                    asyncio.create_task(self.split_subgraphs(turn_context, subgraphs))
                except:
                    answer = "Sure. How many subgraphs would you like to generate?"

            else:
                output = pipe(messages, **generation_args)
                answer = output[0]["generated_text"]

        messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )
        await turn_context.send_activity(answer)
        if "parameters" in answer:
            asyncio.create_task(self.suggest_confluence(turn_context))

    async def process_attachment(self, turn_context: TurnContext, path: str):
        similarity_list, model_details = find_match(path)

        time.sleep(5)
        answer = (
            f"Ok, I was able to process the model. This looks a lot like **{similarity_list[0]}, {similarity_list[1]}, and {similarity_list[2]}**. What else would you like to know about this model?"
        )

        messages.append(
            {
                "role": "system",
                "content": f"MODEL DETAILS: Parameters {model_details['parameters']}, Opset: {model_details['onnx_model_info']['opset']}",
            }
        )
        messages.append(
            {
                "role": "system",
                "content": "If you get asked whether this model runs on any specific devices, say that you can't run models just yet, but people should look into Lemonade 🍋 by Jeremy Fowers (https://github.com/aigdat/genai). When referring to the tool, always use the 🍋 emoji and share the link.",
            }
        )

        # Assuming heatmap.png is in the same directory as this script
        with open("heatmap.png", "rb") as file:
            image_data = file.read()
            base64_image = base64.b64encode(image_data).decode("ascii")

        # Create a reply activity with the image attachment
        reply_activity = Activity(
            type="message",
            attachments=[
                {
                    "contentType": "image/png",
                    "contentUrl": f"data:image/png;base64,{base64_image}",
                    "name": "heatmap.png",
                }
            ],
        )

        # Send image
        await turn_context.send_activity(reply_activity)

        # Send message
        messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )
        await turn_context.send_activity(answer)

    async def split_subgraphs(self, turn_context: TurnContext, subgraphs: int):
        model_names = split_onnx_model(self.model_path, subgraphs)

        time.sleep(5)
        answer = "Ok, here is the data! (attached data)"
        messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )

        # Assuming model.onnx is in the same directory as this script
        for model in model_names:
            with open(model, "rb") as file:
                onnx_data = file.read()
                base64_onnx = base64.b64encode(onnx_data).decode("ascii")

            # Create a reply activity with the ONNX file attachment
            reply_activity = Activity(
                type="message",
                attachments=[
                    {
                        "contentType": "application/octet-stream",
                        "contentUrl": f"data:application/octet-stream;base64,{base64_onnx}",
                        "name": model,
                    }
                ],
            )

            # Send the reply activity
            await turn_context.send_activity(reply_activity)

    async def suggest_confluence(self, turn_context: TurnContext):
        time.sleep(5)
        answer = "By the way, while we were chatting I just checked **Confluence** and it looks like there is no intake report about this model there. Would you like me to create one? I can add something to [confluence.amd.com/display/AIG/](https://confluence.amd.com/display/AIG/)."
        messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )
        await turn_context.send_activity(answer)

    async def on_members_added_activity(self, members_added: ChannelAccount, turn_context: TurnContext):
        for member_added in members_added:
            if member_added.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(initial_greeting)
