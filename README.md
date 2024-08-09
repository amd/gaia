# GAIA: The GenAI Sandbox

Welcome to the GAIA (Generative AI Is Awesome!) project! This repository serves as a repository of local LLM agentic workflows demos and reference designs.

<img src="https://github.com/aigdat/gaia/assets/4722733/0db60b9b-05d5-4732-a74e-f67bc9bdb61b" alt="gaia" width="500">

Currently, we support the following agents:

| Agent Name | Function                     |
| ---------- | ---------------------------- |
|   Maven    | Online research assistant    |
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

### Strix Machines
For strix machines, plase make sure to:
- use the latest driver found [here](https://mkmartifactory.amd.com/artifactory/atg-cvml-generic-local/builds/ipu/Release/NPU_MCDM_RAI_1.2_R24.06.26_RC4_188/jenkins-CVML-IPU_Driver-ipu-windows-release-188/Release/npu_mcdm_stack_prod.zip):
- set `set MLADF=2x4x4` in your command terminal when running on a strix machine.

### Validation of install
Run this to validate the LLM and transformers libary is running properly on NPU:
`transformers\models\llm>python run_awq.py --model_name llama-2-7b --task profilemodel1k --target aie`

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

## Create your own agent

TBD

# Packaging GAIA.exe

The section explains how to create a redistributable GAIA.exe binary.

> Note: the `pip` commands in this section do not use the `-e` option on purpose. This is because all packages must be in `site-packages` to facilitate packaging. If you are running into problems, try `pip list` and make sure that all packages are installed into site-packages and were not installed in-place via `-e`. 

Pre-requisites:
1. `conda create -n gaia-exe python=3.11`
1. `conda activate gaia-exe`
1. Install lemonade with ort-genai support (clone genai, cd into genai): `pip install .[og]`
1. Install gaia (clone this repo, cd into gaia): `pip install .`
1. `pip install pyinstaller`
1. Copy your `genai\src\lemonade\tools\ort_genai\models\Phi-3-mini-4k-instruct-onnx_int4_awq_block-128` folder into your `gaia-exe` environment, e.g., `miniconda3\envs\gaia-exe\Lib\site-packages\lemonade\tools\ort_genai\models\Phi-3-mini-4k-instruct-onnx_int4_awq_block-128`
1. On `gaia\src\gaia\interface\settings.json` set `dev_mode` to `false`

> Note: if you make any changes to `gaia` or `lemonade` you need to `pip install .` that package again to include the changes in your next build.

Build executable:
1. Go to the repo root: `cd gaia`
1. `conda activate gaia-exe`
1. To build a basic executable for development purposes:
`pyinstaller src\gaia\interface\widget.py -n gaia --collect-all lemonade --collect-all onnxruntime_directml --collect-all onnxruntime_genai_directml --collect-all onnxruntime_genai --collect-all gaia  --hidden-import=tiktoken_ext.openai_public --hidden-import=tiktoken_ext --noconfirm --icon src\gaia\interface\img\gaia.ico --add-data "src/gaia/interface/settings.json;gaia/interface" --onefile`

1. This creates an executable at `dist/gaia/gaia.exe`

> Note: the entire `dist/gaia` folder is required to run this basic .exe. See explanation of options below for more details.

Let's break down some of the options in use there:
- The `-n gaia` is the name of the resulting executable.
- The positional `src\gaia\interface\widget.py` points to the the script that the executable should run. This is the "entry point" for the application.
- The `--collect-all` options make sure that non-Python dependencies from specific projects come in. These could likely be refined to point to specific data files, but who has the time?
- The `--hidden-import` options are required for `llama-index` to work.
- The `--no-confirm` makes it so that if you re-build, it wont ask your permission to overwrite the contents of the `dist` and `build` directories halfway through.
- The `--icon` option sets the app's icon in File Explorer.

> Note: all of these options can be encoded into a `gaia.spec` file that can be built with `pyinstaller gaia.spec`. That would save people from needing to copy-paste the whole command, however it also provides for less flexibility. When the project becomes more mature, we recommend committing a `gaia.spec` file with the exact desired options encoded.

There are some additional options that should be considered:
- `--windowed` makes it so that a command prompt isn't launched alongside the GUI when `gaia.exe` is clicked. This is better for deployment, but worse for development.
- `--onefile` builds the entire application into a monolithic `gaia.exe` binary, instead of a `gaia` folder that contains `gaia.exe` and `_internal`. However, this monolithic binary has to be unpacked every time the user runs it, which takes about a minute. We recommend creating an installer instead, but this will do in a pinch.

To create an installer: TBD.