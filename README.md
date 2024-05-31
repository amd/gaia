# GAIA: The GenAI Sandbox

Welcome to the GAIA (Generative AI Is Awesome!) project! This repository serves as a R&D center for new agentic workflows.

<img src="https://github.com/aigdat/gaia/assets/4722733/0db60b9b-05d5-4732-a74e-f67bc9bdb61b" alt="gaia" width="500">

## Contents:

1. [Getting Started](#getting-started)
1. [Install Agents](#install-specialized-tools)
1. [Contributing](#contributing)

# Getting Started

## Install AIG Demo Hub

AIG demo hub is the interface that allows you to interface with GAIA agents (e.g. Clipy, Datalin, Neo, Picasso, etc.).

To install it, run the `AIG-Demo-Hub-4.14.1-windows-setup.exe` setup file in the repo first.

## Install GAIA backend

Install instructions below are for Microsoft Windows 10 OS and [Miniconda 24+](https://docs.anaconda.com/free/miniconda/).

1. Clone repo: `git clone https://github.com/aigdat/gaia.git`
1. Go to the root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaia python=3.10`
    1. `conda activate gaia`
1. Install gaia: `pip install -e .`
1. Start gaia webserver and choose agent to run: `python run.py`
1. Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:<port>/api/messages`
* NOTE: each agent is hosted on a separate port, connect the desired agent by modifying the target port above.

## Install GAIA Agents

To install a specific agent, make sure to add the `[<agent>]` prefix, for example: `pip install -e .[neo]`

# Running the Ryzen AI NPU Web Server

GAIA requires a NPU web server to run properly. Setup the Ryzen AI NPU web server by following the directions below.
1. Clone the lemonade repo, which is used for hosting LLMs via a web server: `git clone https://github.com/aigdat/genai.git`
1. Follow directions for setup and running the Ryzen AI NPU described (here)[https://github.com/aigdat/genai/blob/main/docs/easy_ryzenai_npu.md]
1. Activate virtual environment: `conda activate ryzenai-transformers`
1. Run: `setup.bat`
1. Run: `start_npu_server`
1. You should see an output similar to the one below:
```
Info: Running tool: serve
INFO:     Started server process [18836]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     ('::1', 50649) - "WebSocket /ws" [accepted]
INFO:     connection open
```
NOTE: use command shell only, not powershell.

# Running RyzenAI iGPU Web Server

To get setup initially, you will need to setup the Ryzen AI iGPU web server by following the directions below.
1. Clone the lemonade repo, which is used for hosting LLMs via a web server: `git clone https://github.com/aigdat/genai.git`
1. Follow directions for setup and running the Ryzen AI iGPU described (here)[https://github.com/aigdat/genai/blob/main/docs/ort_genai.md]
1. Run: `start_igpu_server`

# Contributing

This is a very new project whose codebase is under heavy development.

If you decide to contribute, please:

- do so via a pull request.
- write your code in keeping with the same style as the rest of this repo's code.
- add a test under `./tests` that provides coverage of your new feature.

The best way to contribute is to add a new agent that covers a unique use-case. You can use any of the agents such as Neo under ./agents folder as a starting point.

### Create your own agent
You can learn how to create your own agent by following [these instructions](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-create-bot).