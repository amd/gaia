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
- [Miniforge](https://conda-forge.org/download/) (conda-forge's recommended installer)
- [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe)

# Prerequisites

1. Download and install Windows installer from [Miniforge](https://conda-forge.org/download/)
   1. Check _"Add Miniforge3 to my PATH environment variables"_ if you want it accessible in all terminals
1. Download and install [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe).
    1. During installation, make sure to select "Desktop development with C++" workload.
    1. After installation, you may need to restart your computer.
1. *(Hybrid only)* Install the Ryzen AI NPU software drivers from [here](https://ryzenai.docs.amd.com/en/latest/inst.html)
   1. NOTE: In many cases, your NPU drivers may already be installed
      - Check via _"Device Manager -> Neural Processors -> NPU Compute Accelerator Device -> Properties -> Driver Tab"_

# Setup and Installation
1. Clone GAIA repo: `git clone https://github.com/amd/gaia.git`
1. Open a powershell prompt and go to the GAIA root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaiaenv python=3.10 -y`
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
        pip install -e .[clip,joker,talk,dev]
        ```
    ⚠️ NOTE: If actively developing, use `-e` switch to enable editable mode and create links to sources instead.

    ⚠️ NOTE: Make sure you are in the correct virtual environment when installing dependencies. If not, run `conda activate gaiaenv`.

# Environment Configuration

GAIA requires setting the `GAIA_MODE` environment variable based on your hardware and installation type. This determines which mode the application will use.

1. *(Hybrid only)* For Ryzen AI systems with NPU + iGPU:
   ```bash
   set_hybrid_mode.bat
   ```

2. *(Generic only)* For systems using DirectML:
   ```bash
   set_generic_mode.bat
   ```

3. *(NPU only)* For NPU-only configurations:
   ```bash
   set_npu_mode.bat
   ```

Each script above performs the following actions:
- Sets the environment variable for the current session
- Makes the setting persistent using `setx`
- Creates a conda environment activation script so the variable is set whenever your conda environment is activated

If you need to manually configure these settings, you can:

1. Add it to your system environment variables through Windows settings:
   - Search for "Edit environment variables" in Windows
   - Click "Environment Variables"
   - Add GAIA_MODE as a user or system variable with your desired value (HYBRID, GENERIC, or NPU)

2. Or manually create the conda activation script:
   ```bash
   mkdir -p %CONDA_PREFIX%\etc\conda\activate.d
   echo @echo off > "%CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat"
   echo set "GAIA_MODE=HYBRID" >> "%CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat"
   ```

   ⚠️ **NOTE**: Use exact quote formatting as shown when creating environment variables. Incorrect quotes or extra spaces can break variables and cause application errors.

# Running GAIA

Once the installation and environment variables are set, run the following:

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
3. Try deleting pip cache typically under: _C:\Users\<username>\AppData\Local\pip\cache_

### Model Loading Issues

1. Check available system memory
2. Verify model compatibility with your hardware
3. Ensure all dependencies are correctly installed

### Environment Variable Issues

If the GAIA application is not detecting the correct mode:

1. Verify the environment variable is set:
   ```bash
   echo %GAIA_MODE%
   ```

2. If not set, run the appropriate script:
   ```bash
   set_hybrid_mode.bat
   # or
   set_generic_mode.bat
   # or
   set_npu_mode.bat
   ```

3. Check that the conda environment activation script was created properly:
   ```bash
   type %CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat
   ```

4. Ensure you're using the conda environment where the activation script was created:
   ```bash
   conda activate gaiaenv
   ```

5. If the above steps don't resolve the issue, manually set the environment variable:
   ```bash
   set GAIA_MODE=HYBRID
   ```

# Support

Report any issues to the GAIA team at `gaia@amd.com` or create an [issue](https://github.com/amd/gaia/issues) on the [GAIA GitHub repo](https://github.com/amd/gaia.git).

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT