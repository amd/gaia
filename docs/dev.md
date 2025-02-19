# GAIA Development Guide

**Table of Contents**
- [Introduction](#introduction)
- [Before you start](#before-you-start)
  - [System Requirements](#system-requirements)
    - [Hybrid Mode (Ryzen AI Systems)](#hybrid-mode-ryzen-ai-systems)
    - [Generic Mode](#generic-mode)
    - [Software](#software)
  - [Software Requirements](#software-requirements)
- [Prerequisites](#prerequisites)
- [Setup and Installation](#setup-and-installation)
- [Troubleshooting](#troubleshooting)
  - [Common Issues](#common-issues)
    - [NPU Driver Installation](#npu-driver-installation)
    - [pip Installation Errors](#pip-installation-errors)
    - [Model Loading Issues](#model-loading-issues)
- [Support](#support)
- [License](#license)

# Introduction

GAIA is an open-source framework that runs generative AI applications on AMD hardware. GAIA uses the [ONNX Runtime GenAI (aka OGA)](https://github.com/microsoft/onnxruntime-genai/tree/main?tab=readme-ov-file) backend via [Lemonade](https://github.com/onnx/turnkeyml/blob/main/docs/lemonade/getting_started.md) web serve tool for running Large Language Models (LLMs).

GAIA supports two modes:
- **Hybrid Mode**: Utilizes both NPU and iGPU on Ryzen AI systems for optimal performance
- **Generic Mode**: Runs on non-Ryzen AI systems using standard GPU acceleration

⚠️ NOTE: To install generic mode, skip the steps with the *(Hybrid only)* label.

# Before you start

## System Requirements

### Hybrid Mode (Ryzen AI Systems)
- OS: Windows 11 Pro, 24H2
- RAM: Minimum 32GB
- CPU: Ryzen AI 9 HX 370 (STX) or compatible
- NPU Driver Versions: `32.0.203.237` or `32.0.203.240`
- Storage: 20GB free space
- Tested Configuration: ASUS ProArt (HN7306W) Laptop

### Generic Mode
- OS: Windows 11 Pro
- RAM: Minimum 16GB
- GPU: Any DirectML compatible GPU
- Storage: 20GB free space

### Software
- [Miniconda 24+](https://docs.anaconda.com/free/miniconda/)
- [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe)

# Prerequisites

1. Download and install [miniconda](https://docs.anaconda.com/miniconda/)
1. Download and install [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe).
    1. During installation, make sure to select "Desktop development with C++" workload.
    1. After installation, you may need to restart your computer.
1. *(Hybrid only)* Install the Ryzen AI NPU software drivers from [here](https://ryzenai.docs.amd.com/en/latest/inst.html)

# Setup and Installation
1. Clone GAIA repo: `git clone https://github.com/amd/gaia.git`
1. Open a powershell prompt and go to the GAIA root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaiaenv python=3.10`
    1. `conda activate gaiaenv`
1. Install GAIA dependencies:
    1. *(Hybrid only)* Recommended mode for Ryzen AI systems:
        ```bash
        pip install -e .[hybrid,joker,clip,talk,dev]
        ```
    1. *(Hybrid only)* Install Ryzen AI dependencies:
        ```bash
        lemonade-install --ryzenai hybrid -y
        ```
    1. *(Generic only)* Recommended mode for non-Ryzen AI systems:
        ```bash
        pip install -e .[dml,clip,joker,talk,dev]
        ```
    ⚠️ NOTE: If actively developing, use `-e` switch to enable editable mode and create links to sources instead.

    ⚠️ NOTE: Make sure you are in the correct virtual environment when installing dependencies. If not, run `conda activate gaiaenv`.
1. Run `gaia-cli -v` in the terminal to verify the installation. You should see a similar output:
    ```bash
    amd/v0.7.1+cda0f5d5
    ```
1. Run `gaia` to start the GAIA-UI or `gaia-cli -h` to see CLI options.

# Troubleshooting

## Common Issues

### NPU Driver Installation

If you encounter issues with NPU driver installation:
1. Ensure Windows is fully updated
2. Try uninstalling and reinstalling the NPU drivers
3. Verify your system meets the minimum requirements

### pip Installation Errors

If you encounter pip installation errors:
1. Ensure you're using the correct Python version (3.10)
2. Try running: `pip install --upgrade pip`

### Model Loading Issues

1. Check available system memory
2. Verify model compatibility with your hardware
3. Ensure all dependencies are correctly installed

# Support

Report any issues to the GAIA team at `gaia@amd.com` or create an [issue](https://github.com/amd/gaia/issues) on the [GAIA GitHub repo](https://github.com/amd/gaia.git).

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT