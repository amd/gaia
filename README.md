# <img src="src/gaia/img/gaia.ico" alt="GAIA Logo" width="64" height="64" style="vertical-align: middle;"> Introducing GAIA by AMD: Generative AI Is Awesome!

[![GAIA Build Installer](https://github.com/amd/gaia/actions/workflows/build_installer.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our build")
[![GAIA Hybrid Installer Test](https://github.com/amd/gaia/actions/workflows/test_installer_hybrid.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our hybrid tests")
[![GAIA Generic Installer Test](https://github.com/amd/gaia/actions/workflows/test_installer_generic.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our generic tests")
[![GAIA CLI Tests](https://github.com/amd/gaia/actions/workflows/test_gaia_cli.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our cli tests")
[![Latest Release](https://img.shields.io/github/v/release/amd/gaia?include_prereleases)](https://github.com/amd/gaia/releases/latest "Download the latest release")
[![OS - Windows](https://img.shields.io/badge/OS-windows-blue)](https://github.com/amd/gaia/blob/main/docs/install.md "Check out our instructions")
[![Made with Python](https://img.shields.io/badge/Python-3.10-blue?logo=python&logoColor=white)](https://github.com/amd/gaia/blob/main/docs/install.md "Check out our instructions")
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://makeapullrequest.com)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub issues](https://img.shields.io/github/issues/amd/gaia)](https://github.com/amd/gaia/issues)
[![GitHub downloads](https://img.shields.io/github/downloads/amd/gaia/total.svg)](https://tooomm.github.io/github-release-stats/?username=aigdat&repository=gaia)
[![Star History Chart](https://img.shields.io/badge/Star%20History-View-brightgreen)](https://star-history.com/#amd/gaia)

<img src="https://img.youtube.com/vi/_PORHv_-atI/maxresdefault.jpg" style="display: block; margin: auto;" />

**GAIA is an open-source solution designed for the quick setup and execution of generative AI applications on local PC hardware.** It enables fast and efficient execution of LLM-based applications using a hybrid hardware approach that combines the AMD Neural Processing Unit (NPU) and Integrated Graphics Processing Unit (iGPU) in the Ryzen-AI PC. GAIA provides the following key features:

**Currently supports Windows 11 Home/Pro**

- 🏠 **Local LLM Processing**: Easily run powerful language models directly on your Windows device without cloud dependencies
- 🎯 **Multiple Use Cases**: From basic chat to RAG-enhanced applications and specialized agents
- ⚡ **Optimized Performance**: Leverages the AMD NPU and iGPU for hybrid acceleration to get fast and efficient AI processing
- 🖥️ **Easy-to-Use Interface**: Provides both a command-line interface (CLI) and a graphical user interface (GUI) option for easy interaction with models and agents
- 🔧 **Extensible Architecture**: Easily build and integrate your own agents and use cases
- 🔄 **Multiple Installation Modes**: GAIA can be installed in three modes:
   - **Hybrid Mode**: Optimized for Ryzen AI PCs, combining AMD Neural Processing Unit (NPU) and Integrated Graphics Processing Unit (iGPU) for maximum performance
   - **NPU Mode**: Optimized for power efficiency, using only the NPU (coming soo)
   - **Generic Mode**: Compatible with any Windows PC, using Ollama as the backend

For more details, see our [GAIA Blog Article](https://www.amd.com/en/developer/resources/technical-articles/gaia-an-open-source-project-from-amd-for-running-local-llms-on-ryzen-ai.html) or [Frequently Asked Questions](docs/faq.md).
For Ryzen AI LLM app development similar to GAIA, see [this developer guide](https://ryzenai.docs.amd.com/en/latest/llm/overview.html).

⚠️ **IMPORTANT**: GAIA's Hybrid mode **only supports AMD Ryzen AI HX 300 series or newer processors**. For older AMD processors or non-AMD systems, the installer will install the generic mode with ollama. For more details, see [here](https://www.amd.com/en/products/software/ryzen-ai-software.html#tabs-2733982b05-item-7720bb7a69-tab).

## Optional Web Interface: GAIA BETA (RAUX)

GAIA BETA is an optional, modern web-based interface for GAIA, built on the RAUX ([Open-WebUI](https://openwebui.com/) fork) platform. It offers a feature-rich, extensible, and user-friendly experience for interacting with GAIA's AI capabilities. GAIA BETA is currently in beta and is being actively integrated with new features and improvements. 

> **Note:** GAIA BETA is referred to as "RAUX" internally in some technical documentation and code. For most users, it is presented as "GAIA BETA".

For more details and setup instructions, see the [UI Documentation](docs/ui.md).

## Contents:

- [ Introducing GAIA by AMD: Generative AI Is Awesome!](#-introducing-gaia-by-amd-generative-ai-is-awesome)
  - [Optional Web Interface: GAIA BETA (RAUX)](#optional-web-interface-gaia-beta-raux)
  - [Contents:](#contents)
- [Getting Started Guide](#getting-started-guide)
  - [Installation Steps](#installation-steps)
    - [Command-line Installation](#command-line-installation)
  - [Uninstallation Steps](#uninstallation-steps)
  - [Running the GAIA GUI](#running-the-gaia-gui)
  - [Running the GAIA CLI](#running-the-gaia-cli)
  - [Building from Source](#building-from-source)
- [Features](#features)
- [Contributing](#contributing)
- [System Requirements](#system-requirements)
  - [Dependencies](#dependencies)
- [Troubleshooting](#troubleshooting)
  - [Driver Issues](#driver-issues)
  - [Installation Problems](#installation-problems)
  - [Model Download Issues](#model-download-issues)
  - [Performance Problems](#performance-problems)
- [FAQ](#faq)
- [Contact](#contact)
- [License](#license)
- [Acknowledgments](#acknowledgments)

# Getting Started Guide

The quickest way to get started with GAIA is by using our unified installer that supports all modes:

**gaia-windows-setup.exe**: A single installer that supports all three installation modes:
- **Hybrid Mode**: Offers best performance with NPU+iGPU on Ryzen AI processors, recommended option.
- **NPU Mode**: Optimized for power efficiency on Ryzen AI processors *(coming soon)*
- **Generic Mode**: Works on any Windows PC, using Ollama as the backend

The installer includes both a CLI tool and a GUI. The installation process typically takes about 5-10 minutes, depending on your Wi-Fi connection, and provides everything you need to start working with LLMs.

⚠️ **NOTE**: When running GAIA using the Hybrid mode, please make sure to disable any discrete third-party GPUs in Device Manager.

## Installation Steps

![image](./data/img/gaia-setup.png)

To install the GAIA application, please follow these steps:
1. Make sure you meet the minimum [system requirements](#system-requirements) - **For Hybrid mode, you must have AMD Ryzen AI 9 HX 300 or newer processor**
1. Download the [latest release](https://github.com/amd/gaia/releases) of the GAIA installer from the "Assets" section
1. Unzip the downloaded file and run the installer by double-clicking the .exe file.

   ⚠️ **NOTE**: If you get a Windows Security warning, you can verify the application by clicking *"More info"* and then *"Run anyway"*. This warning appears because the application is not digitally signed.

   ⚠️ **NOTE**: The installer will attempt to write to the same directory by default and may overwrite a previous installation of GAIA. Change the target directory if you want to avoid this.

1. Follow the on-screen instructions to complete the installation:
   1. Choose your installation mode based on your hardware:
      - **Hybrid Mode**: Best performance, requires Ryzen AI processor
      - **NPU Mode**: Power-efficient, requires Ryzen AI processor *(coming soon)*
      - **Generic Mode**: Compatible with any PC
   1. The installer will automatically detect your CPU and only enable compatible modes
   1. The process takes about 5-10 minutes depending on your Wi-Fi connection

1. Once installation is complete, two desktop icons will be created.
   1. GAIA-CLI - Double click this icon to launch the CLI tool.
   1. GAIA-GUI - Double click this icon to launch the GUI tool.

### Command-line Installation

If you prefer to use the command-line or for CI/CD environments, you can run the installer with parameters:

```
gaia-windows-setup.exe /S /MODE=HYBRID
```

Available parameters:
- `/S` - Silent installation (no UI)
- `/MODE=X` - Set installation mode (GENERIC, NPU, or HYBRID)
- `/D=<path>` - Set installation directory (must be last parameter)

## Uninstallation Steps

⚠️ **NOTE**: There is currently no automatic uninstaller available for GAIA, but one is coming soon. For now, you must manually remove GAIA from your system. Note that newer installations of GAIA will automatically remove older versions.

To completely uninstall GAIA from your system, follow these steps:

1. Close all running instances of GAIA (both CLI and GUI).

2. Remove the GAIA folder from AppData:
   1. Press `Win + R` to open the Run dialog
   2. Type `%localappdata%` and press Enter
   3. Find and delete the `GAIA` folder

3. Remove model files from the cache folder:
   1. Press `Win + R` to open the Run dialog
   2. Type `%userprofile%\.cache` and press Enter
   3. Delete any GAIA-related model folders (such as `huggingface` and `lemonade`)

4. Remove desktop shortcuts:
   1. Delete the GAIA-CLI and GAIA-GUI shortcuts from your desktop

## Running the GAIA GUI

Check your desktop for the GAIA-GUI icon and double-click it to launch the GUI. The first time you launch GAIA, it may take a few minutes to start. Subsequent launches will be faster. You may also need to download the latest LLM models from Hugging Face. GAIA will handle this automatically but may request a Hugging Face token for access. If you encounter any issues with model downloads or the GUI application, please refer to the [Troubleshooting](#troubleshooting) section or contact the [AMD GAIA team](mailto:gaia@amd.com).

## Running the GAIA CLI

To quickly get started with GAIA via the command line, you can use the GAIA CLI (`gaia-cli`) tool. Double click on the GAIA-CLI icon and run `gaia-cli -h` for help details. For more information and examples, please refer to the [gaia-cli documentation](docs/cli.md).

## Building from Source

To get started building from source, please follow the latest instructions [here](./docs/dev.md). These instructions will setup the [Onnx Runtime GenAI](https://github.com/microsoft/onnxruntime-genai) through the [Lemonade Server](https://lemonade-server.ai/) tool targeting the Ryzen AI SoC.

⚠️ **NOTE**: You may need to install Ollama from [here](https://ollama.com/download) if you plan to use GAIA in Generic mode and run models with the Ollama backend.

# Features

For a list of features and supported LLMs, please refer to the [Features](docs/features.md) page.

# Contributing

This is a new project with a codebase that is actively being developed. If you decide to contribute, please:
- Submit your contributions via a Pull Request.
- Ensure your code follows the same style as the rest of the repository.

The best way to contribute is by adding a new agent that covers a unique use-case. You can use any of the agents/bots under the ./agents folder as a starting point. If you prefer to avoid UI development, you can add a feature to the CLI tool first. For adding features to the GUI, please refer to our [UI Development Guide](docs/ui.md).

# System Requirements

GAIA with Ryzen AI Hybrid NPU/iGPU execution has been tested on the following system below. Any system that has the AMD Ryzen AI 9 300 series processor with NPU Driver 32.0.203.237 on Windows 11 or newer with minimum of 16GB of main memory should work. For more details on what is supported, see [here](https://www.amd.com/en/products/software/ryzen-ai-software.html#tabs-2733982b05-item-7720bb7a69-tab).

⚠️ **NOTE**: GAIA works on Windows 11 Pro/Home and does not support macOS or Linux at this time.

GAIA has been tested on the following system:

- Systems: Asus ProArt PX13/P16, HP Omnibook with Ryzen AI 9 HX 370 series processor
- OS: Windows 11 Pro
- Processor: AMD Ryzen AI 9 HX 370 w/ Radeon 890M
- AMD Radeon 890M iGPU drivers: `32.0.12010.8007` and `32.0.12033.1030`
- AMD NPU drivers: `32.0.203.237` or `32.0.203.240`
- AMD Adrenalin Software: Install the latest version from [AMD's official website](https://www.amd.com/en/products/software/adrenalin.html)
- Physical Memory: 32GB
- For Hybrid mode: AMD Ryzen AI 9 HX 370 with NPU Driver `32.0.203.237` or `32.0.203.240`
- For Generic mode: Any Windows PC meeting Ollama's system requirements

⚠️ **NOTE**: For Hybrid mode, you may experience issues with NPU Driver `32.0.203.242`, it is recommended to revert to driver `32.0.203.240`. You can check your driver version by going to *Device Manager -> Neural Processors -> NPU Compute Accelerator Device -> Right-Click Properties -> Driver Tab -> Driver Version*.

⚠️ **NOTE**: If you do not have access to a Ryzen AI system, you can still use GAIA by installing the generic option.

## Dependencies

The GAIA installer will automatically set up most dependencies, including:
- Python 3.10
- Miniconda (if not already installed)
- FFmpeg
- Ollama *(Generic mode only)*
- All required Python packages

# Troubleshooting

If you encounter issues with GAIA, please check the following common solutions:

## Driver Issues
- **NPU Driver Incompatibility**: For Hybrid mode, ensure you have NPU Driver `32.0.203.237` or `32.0.203.240`. Driver `32.0.203.242` may experience issues.
- **iGPU Driver Issues**: Make sure your AMD Radeon 890M iGPU drivers are `32.0.12010.8007` or `32.0.12033.1030`.
- **Driver Updates**: Install the latest AMD Adrenalin Software from [AMD's official website](https://www.amd.com/en/products/software/adrenalin.html).

## Installation Problems
- **Windows Security Warning**: If you get a Windows Security warning, click *"More info"* and then *"Run anyway"*.
- **Installation Fails**: Make sure you have administrator rights and sufficient disk space.
- **Previous Installation**: If prompted to delete an existing installation, it's recommended to allow this to avoid conflicts.

## Model Download Issues
- **Hugging Face Token**: If requested, provide a valid Hugging Face token to download models.
- **Slow Downloads**: Check your internet connection and be patient during the initial setup.

## Performance Problems
- **Disable Discrete GPUs**: When using Hybrid mode, disable any discrete third-party GPUs in Device Manager.
- **Low Memory**: Ensure you have at least 16GB of RAM (32GB recommended).

For additional troubleshooting assistance, please contact the [AMD GAIA team](mailto:gaia@amd.com).

# FAQ

For frequently asked questions, please refer to the [FAQ](docs/faq.md).

# Contact

Contact [AMD GAIA Team](mailto:gaia@amd.com) for any questions, feature requests, access or troubleshooting issues.

# License

[MIT License](./LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT

# Acknowledgments
GAIA BETA is made possible through the exceptional hard work, dedication, and innovative vision of [Tim Jaeryang Baek](https://github.com/tjbck) and the [Open-WebUI](https://openwebui.com/) team! We are deeply grateful for their outstanding open-source contributions that have enabled us to build upon their robust foundation. Their commitment to creating accessible, user-friendly AI interfaces has been instrumental in bringing GAIA BETA to life. We extend our heartfelt appreciation to the entire Open-WebUI community for their continued support, collaboration, and the incredible platform they've developed that makes modern AI interactions seamless and intuitive.