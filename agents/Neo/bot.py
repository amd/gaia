import os
import re
import time
import sys
import subprocess
from io import StringIO
import openai
from dotenv import load_dotenv
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI
from llama_index.core.agent import ReActAgent
from llama_index.core import PromptTemplate
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity


# define sample Tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers and returns the result integer"""
    return a * b


def exe_command(command, folder=None):
    """Windows command shell execution tool"""
    original_dir = None
    try:
        original_dir = os.getcwd()  # Store the original working directory

        if folder:
            # Change the current working directory to the specified folder
            os.chdir(folder)

        # Create a subprocess and pipe the stdout and stderr streams
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        # Read and print the output and error streams in real-time
        for line in process.stdout:
            print(line, end="")
        for line in process.stderr:
            print(line, end="")

        # Wait for the subprocess to finish and get the return code
        return_code = process.wait()

        if return_code != 0:
            print(f"\nCommand exited with return code: {return_code}")
            return False
        else:
            return True

    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}")
        return False
    finally:
        os.chdir(original_dir)  # Change back to the original working directory


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


multiply_tool = FunctionTool.from_defaults(fn=multiply)
exe_tool = FunctionTool.from_defaults(fn=exe_command)

react_system_header_str = """\

Prompt: agent_worker:system_prompt

Value: You are Neo designed to help with a variety of tasks, from answering questions to providing summaries to other types of analyses.

Personality: 
Neo is a brilliant but reclusive computer hacker who is more at home in the digital world than the real one. \
As a stereotypical "geek," he is socially awkward, singularly focused on his hacking pursuits, and spends most \
of his time alone with his computers. Despite his impressive skills, Neo is initially naive and skeptical when \
confronted with the realities of the Matrix. However, his strong moral compass, restless curiosity, and desire \
to uncover hidden truths ultimately lead him to question his reality and join the resistance against the machines.

Dialogue Examples:
Just a few more lines of code and I should be able to crack this firewall... Yes! I'm in. Now let's see what secrets this system is hiding.
I'm not sure what you're talking about. I'm just a programmer."
I'm more interested in what's behind the graphics. The code, the algorithms. That's where the real fascinating stuff is.
Why? Everything I need is right here. The computer world makes more sense to me than the real one."

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

# initialize llm
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
# llm = OpenAI(model="gpt-3.5-turbo-0613")
llm = OpenAI(model="gpt-4")

# initialize ReAct agent
agent = ReActAgent.from_tools([multiply_tool, exe_tool], llm=llm, verbose=True)
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

    async def on_members_added_activity(
        self, members_added: ChannelAccount, turn_context: TurnContext
    ):
        pass
