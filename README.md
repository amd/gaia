# GAIA: The GenAI Sandbox

Welcome to the GAIA (Generative AI Is Awesome!) project! This repository serves as a repository of AI PC demos. Primarily, it consists of local LLM chatbot and agent demos running on the RyzenAI platform.

Currently, the following are supported:

| Agent Name | Function                     |
| ---------- | ---------------------------- |
|   Chaty    | Vanilla LLM chatbot          |
|   Joker    | Simple joke generator        |
|   Clip     | YouTube search and Q&A agent |
|   Maven*   | Online research assistant    |
|    Neo*    | Chat with public GitHub repo |
|  Datalin*  | Onnx model visualizer        |
|  Picasso*  | AI art creator               |

\* bot or agent is currently under development.

## Contents:

1. [Getting Started](#getting-started)
1. [Install AIG Demo Hub](#install-aig-demo-hub)
1. [Install GAIA Backend](#install-gaia-backend)
1. [Install GAIA Agents](#install-gaia-agents)
1. [Contributing](#contributing)


# Getting Started

To get started, please follow the instructions below. If you have a new machine and need to install all dependencies from scratch, start [here](#Complete-Installation-of-GAIA) instead.

1. Download and unzip the pre-installed package found [here](TBD).
1. Run `gaia.exe` and follow the steps.
1. A quick tutorial can be found [here](TBD).

# Running GAIA on Ryzen AI NPU

### Strix Machines
For strix machines, plase make sure to:
- use the latest driver found [here](https://mkmartifactory.amd.com/artifactory/atg-cvml-generic-local/builds/ipu/Release/NPU_MCDM_RAI_1.2_R24.06.26_RC4_188/jenkins-CVML-IPU_Driver-ipu-windows-release-188/Release/npu_mcdm_stack_prod.zip):
- set `set MLADF=2x4x4` in your command terminal when running on a strix machine.

### Validation of install
Run this to validate the LLM and transformers libary is running properly on NPU:
`transformers\models\llm>python run_awq.py --model_name llama-2-7b --task profilemodel1k --target aie`

## Install Lemonade Web Server

## Manual Start of GAIA App and NPU Web Server

1. These instructions assume you have followed the directions in the ryzenai-npu drop `easy_ryzenai_npu_7-14.md`.
1. Open a new command shell and go to the root <transformers> folder.
1. Activate the conda environment: `conda activate ryzenai-transformers`
1. Enable a performance option: `set MLADF=2x4x4`
1. For STX machines, run: `setup_stx.bat`. For PHX machines, run: `setup_phx.bat`.
1. Run the LLM web server: `start_npu_server.bat`
1. You should see an output similar to the one following:

```
Info: Running tool: serve
INFO:     Started server process [18836]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     ('::1', 50649) - "WebSocket /ws" [accepted]
INFO:     connection open
```
1. Open a new command shell and go to the root of the gaia project (`./gaia_internal/gaia`)
1. Activate the GAIA virtual environment: `conda activate gaiaenv`
    1. NOTE: If the environment does not exist, proceed to the manual installation steps [here](#Installation-of-GAIA-Environment)
1. Start the application: `gaia`

NOTE: Always use command shell only, NOT powershell. Do not use administrative mode.


# Complete Installation of GAIA

The following instructions can be used to do the full installation of GAIA from source and has been tested on Microsoft Windows OS and [Miniconda 24+](https://docs.anaconda.com/free/miniconda/).
These instructions should only be used if other steps fail, otherwise most users should be following [these instructions](#Automatically-Running-the-Ryzen-AI-NPU-Web-Server-and-GAIA).


## Installing third-party tools

1. Download and install [miniconda](https://docs.anaconda.com/miniconda/)
1. Download and install [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe).
    1. During installation, make sure to select "Desktop development with C++" workload.
    1. After installation, you may need to restart your computer.


## Installing GAIA tools

1. Clone GAIA repo from [here](TBD)
1. In the GAIA command window, do the following:
    1. Go to the GAIA root: `cd ./gaia`
    1. Create and activate a conda environment:
        1. `conda create -n gaiaenv python=3.11`
        1. `conda activate gaiaenv`
    1. Install GAIA package and dependencies:
        1. `pip install .`
        NOTE: If actively developing, use `pip install -e .` to enable editable mode and create links to sources instead.
    1. Run `gaia` and a UI should pop-up initializing the application.


## Installing Lemonade web server

1. In a new command terminal, do the following:
    1. Follow instruction to setup the `ryzenai-transformers` environment [here](#Installation-of-Transformers)
    1. Follow instructions to setup the `lemonade` tool [here](https://github.com/aigdat/genai/blob/main/docs/easy_ryzenai_npu.md)
    1. Execute `<transformers>/setup_stx.py` 
    1. Set environment variable: `set MLADF=2x4x4`
    1. Start lemonade web server: `lemonade ryzenai-npu-load --device stx -c meta-llama/Llama-2-7b-chat-hf serve --max-new-tokens 600`


## Installing AMD Transformers library

1. Go to the root of <transformers> library.
1. `conda env create --file=env.yaml`
1. `conda activate ryzenai-transformers`
1. `setup_stx.bat`
1. `set MLADF=2x4x4`
1. `build_dependencies.bat`
1. `pip install ops\cpp --force-reinstall`
1. `pip install ops\torch_cpp --force-reinstall`

1. Copy the llama2 weights from here: llama-2-7b  into models\llm
1. cd into `models\llm` and quantize the model
    1. AWQ:  `python run_awq.py --model_name llama-2-7b --task quantize`
    1. AWQPlus: `python run_awq.py --model_name llama-2-7b --task quantize --algorithm awqplus`

1. The checkpoints are created in `models\llm\quantized_models\`
1. Decode the model and run some example prompts: Go to `models\llm\` folder and run:
    1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task decode --fast_attention --fast_mlp --fast_norm`

1. Run profiling for a specific prompt lengths:
    1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel2k --fast_attention --fast_mlp --fast_norm`
    1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel1k --fast_attention --fast_mlp --fast_norm`
    1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel512 --fast_attention --fast_mlp --fast_norm`
    1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel256 --fast_attention --fast_mlp --fast_norm`

1. Run assisted generation
    1. `cd models\llm_assisted_generation\`
    1. `python assisted_generation.py --model_name llama-2-7b --task decode --assisted_generation`

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

The best way to contribute is to add a new agent that covers a unique use-case. You can use any of the agents/bots under ./agents folder as a starting point.

## Create your own agent

TBD

# Packaging gaia.exe

The section explains how to create a redistributable gaia.exe binary.

> Note: the `pip` commands in this section do not use the `-e` option on purpose. This is because all packages must be in `site-packages` to facilitate packaging. If you are running into problems, try `pip list` and make sure that all packages are installed into site-packages and were not installed in-place via `-e`. 

Pre-requisites:
1. `conda create -n gaia-exe python=3.11`
1. `conda activate gaia-exe`
1. Install lemonade with ort-genai support (clone genai, cd into genai): `pip install .[og]`
1. Install gaia (clone this repo, cd into gaia): `pip install .`
1. `pip install pyinstaller`
<!-- 1. Copy your `genai\src\lemonade\tools\ort_genai\models\Phi-3-mini-4k-instruct-onnx_int4_awq_block-128` folder into your `gaia-exe` environment, e.g., `miniconda3\envs\gaia-exe\Lib\site-packages\lemonade\tools\ort_genai\models\Phi-3-mini-4k-instruct-onnx_int4_awq_block-128` -->
<!-- 1. On `gaia\src\gaia\interface\settings.json` set `dev_mode` to `false` -->

> Note: if you make any changes to `gaia` you need to `pip install .` that package again to include the changes in your next build.

Build executable:
1. Go to the repo root: `cd gaia`
1. `conda activate gaia-exe`
1. To build a basic executable for development purposes: `build_exe.bat`

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