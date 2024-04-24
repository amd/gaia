from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

torch.random.manual_seed(0)

model = AutoModelForCausalLM.from_pretrained(
    "microsoft/Phi-3-mini-4k-instruct",
    device_map="cuda",
    torch_dtype="auto",
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained("microsoft/Phi-3-mini-4k-instruct")

initial_greeting = "Hi, I'm Datalin. How can I help?"
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
