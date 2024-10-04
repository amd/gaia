#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

## Contents:

1. [Complete Installation of GAIA](#complete-installation-to-setup-gaia-app)
1. [Prerequisites](#prerequisites)
1. [Download the Necessary Files](#download-the-necessary-files)
1. [Install Transformers Repo and Run Lemonade LLM Web Server](#install-transformers-repo-and-run-lemonade-llm-web-server)
1. [Setup GAIA Interface](#setup-gaia-interface)
1. [Running GAIA on Ryzen AI NPU](#running-gaia-on-ryzen-ai-npu)
1. [Easy Start of GAIA App and NPU Web Server](#easy-start-of-gaia-app-and-npu-web-server)
1. [Manual Start of GAIA App and NPU Web Server](#manual-start-of-gaia-app-and-npu-web-server)
1. [Complete Installation of Transformers](#complete-installation-of-transformers)

NOTE: These instructions apply to the [AIG-DAT/gaia_internal](https://gitenterprise.xilinx.com/AIG-DAT/gaia_internal.git) repo, however the same instructions follow for [aig-dat/gaia](https://github.com/aigdat/gaia.git) repo as well, just replace the paths accordingly.

To get started, please follow the installation instructions below. If you've already completed the installation and just want to run GAIA, see [Running GAIA](#running-gaia-on-ryzen-ai-npu).

# Complete Installation to Setup GAIA App

The following was confirmed working with the following setup:

* Windows 11, 24H2
* Miniconda 24+
* NPU Driver Version: `32.0.201.204`, `32.0.201.156`
* ASUS ProArt (HN7306W) Laptop with Ryzen AI 9 HX 370 (STX)

## Prerequisites

* [Miniconda 24+](https://docs.anaconda.com/free/miniconda/)

### Download the Necessary Files:
The last known working and semi-performant transformers repo files can be found [here](https://amdcloud.sharepoint.com/:f:/s/AIM/Ent2aChXKxJEhii0rtLp76QBJesG90InAgdI0Vmco8-puw?e=ph8WiF). Download the following files:

* transformers.zip <- this is the working drop from engineering
* ryzenai-transformers.tar.gz <- this is the environment with the above pre-installed

## Install Transformers Repo and Run Lemonade LLM Web Server:

We recommend putting the `transformers.zip` and `ryzenai-transformers.tar.gz` files at the folder path: `C:\work\llm_npu` and the instructions that follow assume this.
Open a new anaconda command terminal, unzip the transformers project and unpack the environment using the following instructions:

1. `cd C:\work\llm_npu`
1. `mkdir ryzenai-transformers`
1. `tar -xf transformers.zip`  --> If the tar command doesn't work on transformers.zip, use Windows to extract the folders contents.
1. `tar -xf ryzenai-transformers.tar.gz -C ryzenai-transformers`
1. `conda activate C:\work\llm_npu\ryzenai-transformers`
1. `conda-unpack`

If conda-unpack is not installed, use: `conda install conda-unpack`

Start the lemonade LLM web server:

1. `cd C:\work\llm_npu\transformers`
1. `setup_stx.bat`
1. `set MLADF=2x4x4`
1. `lemonade ryzenai-npu-load --device stx -c meta-llama/Llama-2-7b-chat-hf serve --max-new-tokens 600`

## Setup GAIA Interface

Clone this repo:
`https://gitenterprise.xilinx.com/AIG-DAT/gaia_internal.git`

In a new command terminal:

1. Go to the GAIA root: `cd ./gaia_internal/gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaiaenv python=3.11`
    1. `conda activate gaiaenv`
1. Install GAIA package and dependencies:
    1. `pip install .`
    NOTE: If actively developing, use `pip install -e .` to enable editable mode and create links to sources instead.
1. If you want to use Clip (YouTube Assistant), you'll need a YouTube API Key:
`set YOUTUBE_API_KEY=AIzaSyDW-S32jBmwBBNnVD5Nrr8ad2AVLDcIJtI`
1. Run `gaia` and a UI should pop-up initializing the application.


# Running GAIA on Ryzen AI NPU

The follow instructions are for running the GAIA application after you've completed the installation steps provided [here](#complete-installation-to-setup-gaia-app)

## Easy Start of GAIA App and NPU Web Server

1. `run.bat`

## Manual Start of GAIA App and NPU Web Server

1. Ensure you've completed the full installation detailed [below](#complete-installation-to-setup-gaia-app)
1. Open a new command shell and go to the root <transformers> folder (for ex: `C:\work\llm_npu\transformers\`)
1. Activate the conda environment: `conda activate C:\work\llm_npu\transformers\`
1. Enable a performance option: `set MLADF=2x4x4`
1. For STX machines, run: `setup_stx.bat`. For PHX machines, run: `setup_phx.bat`.
1. Change to the gaia_internal directory: `C:\work\llm_npu\gaia_internal\gaia\`
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
    1. NOTE: If the environment does not exist, proceed to the manual installation steps [here](#complete-installation-to-setup-gaia-app)
1. Start the application: `gaia`

NOTE: Always use Anaconda command shell only, NOT powershell. Do not use administrative mode.


# Complete Installation of Transformers

If you're interested in using a different version of the transformers repo, you'll need to do the full installation (versus using a pre-installed transformers environment). These instructions are derived from the transformers repo instructions but may be out-of-date. Additionally, you'll need to make sure you have Visual Studio with Developer C++ Tools installed.

1. Go to the root of <transformers> library.
1. `conda env create --file=env.yaml`
1. `conda activate ryzenai-transformers`
1. `setup_stx.bat`
1. `set MLADF=2x4x4`
1. `build_dependencies.bat`
1. `pip install ops\cpp --force-reinstall`
1. `pip install ops\torch_cpp --force-reinstall`

1. Copy the llama2 weights from here: llama-2-7b  into models\llm
1. cd into models\llm and quantize the model
1. AWQ:  `python run_awq.py --model_name llama-2-7b --task quantize`
1. AWQPlus: `python run_awq.py --model_name llama-2-7b --task quantize --algorithm awqplus`
1. 
1. The checkpoints are created in models\llm\quantized_models\
    1. Decode the model and run some example prompts: Go to models\llm\ folder
1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task decode --fast_attention --fast_mlp --fast_norm`

1. Run profiling for a specific lengths prompt
1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel2k --fast_attention --fast_mlp --fast_norm`
1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel1k --fast_attention --fast_mlp --fast_norm`
1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel512 --fast_attention --fast_mlp --fast_norm`
1. `python run_awq.py --model_name llama-2-7b --algorithm awqplus --task profilemodel256 --fast_attention --fast_mlp --fast_norm`

1. Run assisted generation
1. `cd models\llm_assisted_generation\`
1. `python assisted_generation.py --model_name llama-2-7b --task decode --assisted_generation`
