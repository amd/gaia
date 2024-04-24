import os
from botbuilder.core import (
    ActivityHandler,
    TurnContext,
    TurnContext,
)

from botbuilder.schema import ChannelAccount, Activity

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
import base64

from find_match import find_match

torch.random.manual_seed(0)

model = AutoModelForCausalLM.from_pretrained(
    "microsoft/Phi-3-mini-4k-instruct",
    device_map="cuda",
    torch_dtype="auto",
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained("microsoft/Phi-3-mini-4k-instruct")

initial_greeting = "Hi, I'm Datalin. I'm an AI agent that is here to help you play with some of DAT's tools. I heard you have a model you wanted me to have a look at?"
messages = [
    {
        "role": "system",
        "content": "You are a helpful, funny digital assistant that doesn't talk about itself. You are here to help engineers at AMD to use tools provided by the DAT team. All of those tools aim at doing model analysis. Please provide safe, super concise and accurate information to the user. To be able to help the user, you need that users send you a model. Once they send you a model they can ask you questions about the model. Always answer in less than 50 words.",
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
                    messages.append(
                        {
                            "role": "assistant",
                            "content": answer,
                        }
                    )
                    await turn_context.send_activity(answer)

                    path = os.path.join(r"C:\Users\danie\Downloads", attachment.name)
                    similarity_list = find_match(path)
                    answer = f"Ok, I was able to process the model. This looks a lot like {similarity_list[0]}, {similarity_list[1]}, and {similarity_list[2]}. What else would you like to know about this model?"

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

                    # Send the reply activity
                    await turn_context.send_activity(reply_activity)

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

            output = pipe(messages, **generation_args)
            answer = output[0]["generated_text"]

        messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )
        await turn_context.send_activity(answer)

    async def on_members_added_activity(
        self, members_added: ChannelAccount, turn_context: TurnContext
    ):
        for member_added in members_added:
            if member_added.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(initial_greeting)
