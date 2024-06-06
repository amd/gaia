import time, sys
from openai import OpenAI

# Point to the local server
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

prompt = """\
Context information is below.\n---------------------\nfile_path: README.md\n\nGAIA\n\nfile_path: README.md\n\nAIG Demo Hub\n\r\nAIG demo hub is the interface that allows you to interface with GAIA agents (e.g. Neo and Datalin).\r\n\r\nTo install it, download and run the `AIG-Demo-Hub-4.14.1-windows-setup.exe` setup file.\n\nfile_path: README.md\n\nAgents\n\nfile_path: README.md\n\nNeo\n\r\nNeo is an agent designed to help you setup and play with GitHub repos.\r\n\r\nTo run Neo simply run the steps below:\r\n* `conda create -n gaia python=3.9`\r\n* `conda activate gaia`\r\n* `pip install -r agents/Neo/requirements.txt --upgrade`\r\n* `python agents/Neo/app.py`\r\n* Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:3978/api/messages`\n\nfile_path: README.md\n\nDatalin\n\r\nDatalin is an agent designed to help you reason about ML models using some of DAT tools.\r\n\r\nTo run Datalin simply run the steps below:\r\n* `conda create -n gaia python=3.10`\r\n* `conda activate gaia`\r\n* `pip install -r agents/Datalin/requirements.txt`\r\n* `python agents/Datalin/app.py`\r\n* Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:3978/api/messages`\n\nfile_path: README.md\n\nCreate your own agent\n\r\nYou can learn how to create your own agent by following those instructions.\n---------------------\nGiven the context information and not prior knowledge, answer the query.\nQuery: generate the commands to install dependencies\nAnswer:
"""

print("---------------------------------------------------------")
print("Testing text completion (non-streaming):")
print("---------------------------------------------------------")
start = time.time()
completion = client.chat.completions.create(
    model="LM Studio Community/Meta-Llama-3-8B-Instruct-GGUF",
    messages=[
        # {"role": "system", "content": "Always answer in rhymes."},
        # {"role": "user", "content": "Introduce yourself."}
        {"role": "user", "content": prompt}
    ],
    temperature=0.7,
    stream=False
)
print(completion.choices[0].message.content)
latency = time.time() - start
print(f"\n\n{latency} secs\n")


print("\n\n---------------------------------------------------------")
print("Testing text completion (streaming):")
print("---------------------------------------------------------")
start = time.time()
completion = client.chat.completions.create(
    model="LM Studio Community/Meta-Llama-3-8B-Instruct-GGUF",
    messages=[
        # {"role": "system", "content": "Always answer in rhymes."},
        # {"role": "user", "content": "Introduce yourself."}
        {"role": "user", "content": prompt}
    ],
    temperature=0.7,
    stream=True
)

for chunk in completion:
    text = chunk.choices[0].delta.content
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()
latency = time.time() - start
print(f"\n\n{latency} secs\n")
