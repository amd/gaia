#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
#### SPDX-License-Identifier: MIT

# Introduction

onnxruntime-genai (aka OGA) is a new framework created by Microsoft for running ONNX LLMs: https://github.com/microsoft/onnxruntime-genai/tree/main?tab=readme-ov-file

# Complete Installation of GAIA using Onnx Runtime Gen-AI (ORT-GenAI) on the NPU

## Before you start
The following instructions can be used to do the full installation of GAIA from source and has been tested on Microsoft Windows OS and [Miniconda 24+](https://docs.anaconda.com/free/miniconda/).

1. Install the RyzenAI NPU software drivers [here](https://ryzenai.docs.amd.com/en/latest/inst.html)
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

## Installing and running ORT-GenAI
1. NOTE: ⚠️ DO THESE STEPS IN EXACTLY THIS ORDER USING THE SAME COMMAND SHELL AND CONDA VIRTUAL ENVIRONMENT ⚠️
1. Install GAIA [here](#installing-gaia)
1. Install Lemonade [here](#installing-lemonade)
1. Download and install models [here](#download-and-install-models)
1. Install wheels [here](#install-ort-genai-wheels)
1. Run a quick test to verify [here](#run-a-quick-lemonade-test)
1. Run `gaia`

### Installing GAIA
1. Clone GAIA repo
1. In the GAIA command window, do the following:
    1. Go to the GAIA root: `cd ./gaia`
    1. Create and activate a conda environment:
        1. `conda create -n gaiaenv python=3.10`
        1. `conda activate gaiaenv`
    1. Install GAIA package and dependencies:
        1. `pip install .`
        NOTE: If actively developing, use `pip install -e .` to enable editable mode and create links to sources instead.

### Installing the Lemonade LLM Web serve tool
Follow the instructions outlined [here](https://github.com/onnx/turnkeyml/blob/main/docs/ort_genai_npu.md).
