import re
import time
import sys
import openai
import os
from dotenv import load_dotenv
from io import StringIO
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI
from llama_index.core.agent import ReActAgent
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity


# define sample Tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers and returns the result integer"""
    return a * b


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
    agent.query(query)
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


multiply_tool = FunctionTool.from_defaults(fn=multiply)

# initialize llm
load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')
llm = OpenAI(model="gpt-3.5-turbo-0613")

# initialize ReAct agent
agent = ReActAgent.from_tools([multiply_tool], llm=llm, verbose=True)


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

    async def on_members_added_activity(
        self, members_added: ChannelAccount, turn_context: TurnContext
    ):
        pass
