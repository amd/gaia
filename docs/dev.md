# GAIA Development Guide

**Table of Contents**
- [Introduction](#introduction)
- [Before you start](#before-you-start)
  - [System Requirements](#system-requirements)
    - [Ryzen AI Systems](#ryzen-ai-systems)
    - [Software](#software)
  - [Software Requirements](#software-requirements)
- [Prerequisites](#prerequisites)
- [Setup and Installation](#setup-and-installation)
- [Environment Configuration](#environment-configuration)
- [Troubleshooting](#troubleshooting)
  - [Common Issues](#common-issues)
    - [NPU Driver Installation](#npu-driver-installation)
    - [pip Installation Errors](#pip-installation-errors)
    - [Model Loading Issues](#model-loading-issues)
    - [Environment Variable Issues](#environment-variable-issues)
- [Support](#support)
- [License](#license)

# Introduction

GAIA is an open-source framework that runs generative AI applications on AMD hardware. GAIA uses the [ONNX Runtime GenAI (aka OGA)](https://github.com/microsoft/onnxruntime-genai/tree/main?tab=readme-ov-file) backend via [Lemonade Server](https://lemonade-server.ai/) tool for running Large Language Models (LLMs).

GAIA utilizes both NPU and iGPU on Ryzen AI systems for optimal performance on 300 series processors or above.

# Before you start

## System Requirements

- OS: Windows 11 Pro, 24H2 or Ubuntu 22.04 LTS / 24.04 LTS (64-bit)
- RAM: Minimum 16GB
- CPU: Ryzen AI 300-series processor (e.g., Ryzen AI 9 HX 370)
- NPU Driver Versions: `32.0.203.240` and newer
- Storage: 20GB free space
- Tested Configuration: ASUS ProArt (HN7306W) Laptop

### Software
- [Miniforge](https://conda-forge.org/download/) (conda-forge's recommended installer)
- [Lemonade Server](https://lemonade-server.ai/) (LLM backend server for GAIA)

# Windows Prerequisites

1. Download and install Windows installer from [Miniforge](https://conda-forge.org/download/)
   1. Check _"Add Miniforge3 to my PATH environment variables"_ if you want it accessible in all terminals
2. Download and install [Lemonade Server](https://lemonade-server.ai/)
   1. Go to https://lemonade-server.ai/ and download the appropriate installer for your system
   2. Follow the installation instructions provided on the website
   3. Lemonade Server will be used as the backend for running LLMs with GAIA

# Setup and Installation
1. Clone GAIA repo: `git clone https://github.com/amd/gaia.git`
1. Open a powershell prompt and go to the GAIA root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaiaenv python=3.10 -y`
    1. `conda activate gaiaenv`
1. Install GAIA dependencies:
    ```bash
    pip install -e .[dev]
    ```
    ⚠️ NOTE: If actively developing, use `-e` switch to enable editable mode and create links to sources instead.

    ⚠️ NOTE: Make sure you are in the correct virtual environment when installing dependencies. If not, run `conda activate gaiaenv`.

    ⚠️ NOTE: Check `./setup.py` for additional packages that support extra features in the CLI tool, e.g. `pip install -e .[dev,eval,talk]`

5. For detailed information about using the Chat SDK and CLI chat features, see the [Chat SDK Documentation](./chat.md).

## Dependency Management

For information on managing dependencies and configuring Dependabot for automated updates, see the [Dependency Management Guide](./dependency-management.md).

# Running GAIA

Once the installation and environment variables are set, run the following:

1. Start the Lemonade server (required for LLM operations):
    ```bash
    lemonade-server serve
    ```
    Keep this running in a separate terminal window.

1. Run `gaia -v` in the terminal to verify the installation. You should see a similar output:
    ```bash
    0.10.0
    ```
1. Run `gaia -h` to see CLI options.
1. Try the chat feature with a simple query:
    ```bash
    gaia chat "What is artificial intelligence?"
    ```
    Or start an interactive chat session:
    ```bash
    gaia chat
    ```

## Running Electron Applications

GAIA includes Electron-based GUI applications. To run the JAX (Jira Agent Experience) app locally:

```bash
# Linux/Mac
./src/gaia/apps/jira/webui/run.sh

# Windows
.\src\gaia\apps\jira\webui\run.ps1
```

This will build the installer and launch the JAX application in development mode. The installer will be created in `src/gaia/apps/jira/webui/out/make/`.

# Troubleshooting

## Common Issues

### pip Installation Errors

If you encounter pip installation errors:
1. Ensure you're using the correct Python version (3.10)
2. Try running: `pip install --upgrade pip`
3. Try deleting pip cache typically under: _C:\Users\<username>\AppData\Local\pip\cache_
4. Make sure there are no spaces in your project or or pip file cache paths

### Model Loading Issues

1. Check available system memory
2. Verify model compatibility with your hardware
3. Ensure all dependencies are correctly installed

### Environment Variable Issues

If GAIA is not working correctly:

1. Verify the installation completed successfully by checking the log files
2. Ensure all dependencies are installed correctly
3. Check that you're using the correct conda environment:
   ```bash
   conda activate gaiaenv
   ```
4. Try restarting your terminal or command prompt

# Support

Report any issues to the GAIA team at `gaia@amd.com` or create an [issue](https://github.com/amd/gaia/issues) on the [GAIA GitHub repo](https://github.com/amd/gaia.git).

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT