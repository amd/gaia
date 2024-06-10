# GAIA: The GenAI Sandbox

Welcome to the GAIA (Generative AI Is Awesome!) project! This repository serves as a repository of local LLM agentic workflows demos and reference designs.

<img src="https://github.com/aigdat/gaia/assets/4722733/0db60b9b-05d5-4732-a74e-f67bc9bdb61b" alt="gaia" width="500">

Currently, we support the following agents:

| Agent Name | Function                     |
| ---------- | ---------------------------- |
|   Clipy    | Chat with youtube transcript |
|   Datalin  | Onnx model visualizer        |
|   Joker    | Simple joke generator        |
|    Neo     | Chat with public GitHub repo |
|  Picasso*  | AI art creator               |

\* Picasso agent is currently under development and exists only as a mockup.

# Getting Started

To get started, please follow the instructions below. If you have a new machine and need to install all dependencies, start [here](#).

## Contents:

1. [Getting Started](#getting-started)
1. [Install AIG Demo Hub](#install-aig-demo-hub)
1. [Install GAIA Backend](#install-gaia-backend)
1. [Install GAIA Agents](#install-gaia-agents)
1. [Contributing](#contributing)


# Installation

## Install AIG Demo Hub

AIG demo hub is the interface that allows you to interface with GAIA agents (e.g. Clipy, Datalin, Neo, Picasso, etc.).

To install it, run the `AIG-Demo-Hub-4.14.1-windows-setup.exe` setup file in the repo first.

## Install GAIA Backend

Install instructions below are for Microsoft Windows OS and [Miniconda 24+](https://docs.anaconda.com/free/miniconda/).

1. Clone repo: `git clone https://github.com/aigdat/gaia.git`
1. Go to the root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaia python=3.11`
    1. `conda activate gaia`
1. Install GAIA package and dependencies, note this command will install dependencies for all agents:
    1. `pip install -e .`
1. To install dependencies with cuda support, run:
    1. `pip install -e .[cuda]`

## Install Lemonade Web Server

GAIA requires the Lemonade Web Server to run, follow the install directions below.
1. Open a new command-line terminal.
1. Follow the directions for setup (here)[https://github.com/aigdat/genai/blob/main/docs/easy_ryzenai_npu.md]

# Running the Ryzen AI NPU Web Server

1. Open a new command-line terminal
1. Activate the virtual environment described [here](#install-lemonade-web-server).
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

# Running the Neo Agent on NPU

1. Make sure to include a github token in your `.env` file.
`GITHUB_TOKEN=github_pat_abc123etc`
1. Start the NPU web server by following the instructions [here](#running-the-ryzen-ai-npu-web-server)
1. Start gaia webserver:  `python run.py`
1. Choose the Neo agent to run:
`Enter the agent you want to run (Clipy, Datalin, Joker, Neo, Picasso, All) [Default: Neo]: `
1. Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:<port>/api/messages`
* NOTE: each agent is hosted on a separate port, connect to the desired agent by modifying the target port above. See gaia web server shell for port details.

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