#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
#### SPDX-License-Identifier: MIT

# Introduction

onnxruntime-genai (aka OGA) is a new framework created by Microsoft for running ONNX LLMs: https://github.com/microsoft/onnxruntime-genai/tree/main?tab=readme-ov-file

# Complete Installation of GAIA using Onnx Runtime Gen-AI (ORT-GenAI) on the NPU

## Before you start
The following instructions can be used to do the full installation of GAIA from source and has been tested on Microsoft Windows OS and [Miniconda 24+](https://docs.anaconda.com/free/miniconda/).

1. Install the Ryzen AI NPU software drivers [here](https://ryzenai.docs.amd.com/en/latest/inst.html)
1. The following was confirmed working with the following setup:
    * Windows 11, 24H2
    * Miniconda 24+
    * NPU Driver Version: `32.0.201.204`, `32.0.201.156`
    * ASUS ProArt (HN7306W) Laptop with Ryzen AI 9 HX 370 (STX)

## Installing third-party tools
1. Download and install [miniconda](https://docs.anaconda.com/miniconda/)
1. Download and install [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe).
    1. During installation, make sure to select "Desktop development with C++" workload.
    1. After installation, you may need to restart your computer.

## Installation and running ORT-GenAI
1. ⚠️ NOTE: Do these steps in exactly this order using the same command shell and conda virtual environment
1. Clone GAIA repo
1. Open a powershell prompt and go to the GAIA root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaiaenv python=3.10`
    1. `conda activate gaiaenv`
1. Install GAIA package and dependencies:
    1. For Hybrid (recommended): `pip install -e .[hybrid,joker,clip]`
    1. For NPU: `pip install -e .[npu,joker,clip]`
    1. For DML: `pip install -e .[dml,joker,clip]`
    ⚠️ NOTE: If actively developing, use `-e` switch to enable editable mode and create links to sources instead.
1. Set the `OGA_TOKEN` environment variable, you will need a token to download the hybrid-llm-artifacts. Contact the GAIA team for a token.
    1. `$env:OGA_TOKEN=<your_token>`
1. Install dependencies and setup environment variables using `.\util\InstallOgaDependencies.ps1` script.
    1. For Hybrid: `.\util\InstallOgaDependencies.ps1 -Mode Hybrid`
    1. For NPU: `.\util\InstallOgaDependencies.ps1 -Mode NPU`
    1. You should see the following message if the script was successful. `Dependencies installed successfully!`
    1. ⚠️ NOTE: Make sure you are in the correct virtual environment when installing dependencies.
        1. If not, run `conda activate gaiaenv`
1. Run `gaia` to start the GAIA app.
1. Report any issues to the GAIA team at `gaia@amd.com` or create an issue on the GAIA GitHub repo.

### Installing the Lemonade LLM Web serve tool
Follow the instructions outlined [here](https://github.com/onnx/turnkeyml/blob/main/docs/ort_genai_npu.md).
