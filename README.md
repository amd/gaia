# <img src="src/gaia/img/gaia.ico" alt="GAIA Logo" width="64" height="64" style="vertical-align: middle;"> Introducing GAIA by AMD: Generative AI Is Awesome!

[![GAIA Build Installer](https://github.com/amd/gaia/actions/workflows/build_installer.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our build")
[![GAIA Installer Test](https://github.com/amd/gaia/actions/workflows/test_installer.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our installer tests")
[![GAIA CLI Tests](https://github.com/amd/gaia/actions/workflows/test_gaia_cli.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our cli tests")
[![Latest Release](https://img.shields.io/github/v/release/amd/gaia?include_prereleases)](https://github.com/amd/gaia/releases/latest "Download the latest release")
[![OS - Windows](https://img.shields.io/badge/OS-Windows-blue)](https://github.com/amd/gaia/blob/main/docs/installer.md "Windows installer")
[![OS - Linux](https://img.shields.io/badge/OS-Linux-green)](https://github.com/amd/gaia/blob/main/README.md#linux-installation "Linux support")
[![Made with Python](https://img.shields.io/badge/Python-3.10-blue?logo=python&logoColor=white)](https://github.com/amd/gaia/blob/main/docs/install.md "Check out our instructions")
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://makeapullrequest.com)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub issues](https://img.shields.io/github/issues/amd/gaia)](https://github.com/amd/gaia/issues)
[![GitHub downloads](https://img.shields.io/github/downloads/amd/gaia/total.svg)](https://tooomm.github.io/github-release-stats/?username=aigdat&repository=gaia)
[![Star History Chart](https://img.shields.io/badge/Star%20History-View-brightgreen)](https://star-history.com/#amd/gaia)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-7289DA?logo=discord&logoColor=white)](https://discord.com/channels/1392562559122407535/1402013282495102997 "Join our Discord community")

<img src="https://img.youtube.com/vi/_PORHv_-atI/maxresdefault.jpg" style="display: block; margin: auto;" />

**GAIA is an open-source solution designed for the quick setup and execution of generative AI applications on local PC hardware.** It enables fast and efficient execution of LLM-based applications using a hybrid hardware approach that combines the AMD Neural Processing Unit (NPU) and Integrated Graphics Processing Unit (iGPU) in the Ryzen-AI PC. GAIA provides the following key features:

**Platform Support:**
- **Windows 11 Home/Pro**: Full GUI and CLI support with installer
- **Linux (Ubuntu/Debian)**: Full GUI and CLI support via source installation

- 🏠 **Local LLM Processing**: Easily run powerful language models directly on your device without cloud dependencies
- ⚡ **Direct LLM Access**: Query models instantly with the new `gaia llm` command - no server setup required
- 🎯 **Specialized Agents**: Includes Blender agent for 3D content creation and workflow automation
- ⚡ **Optimized Performance**: GAIA uses Lemonade Server for hardware-optimized model execution on AMD NPU and iGPU
- 🖥️ **Easy-to-Use Interface**: Provides both a command-line interface (CLI) and a graphical user interface (GUI) option for easy interaction with models and agents
- 🔧 **Extensible Architecture**: Easily build and integrate your own agents and use cases

For more details, see our [GAIA Blog Article](https://www.amd.com/en/developer/resources/technical-articles/gaia-an-open-source-project-from-amd-for-running-local-llms-on-ryzen-ai.html) or [Frequently Asked Questions](docs/faq.md).
For Ryzen AI LLM app development similar to GAIA, see [this developer guide](https://ryzenai.docs.amd.com/en/latest/llm/overview.html).

⚠️ **IMPORTANT**: GAIA is specifically designed for **AMD Ryzen AI systems** and uses Lemonade Server for optimal hardware utilization. For more details, see [here](https://www.amd.com/en/products/software/ryzen-ai-software.html#tabs-2733982b05-item-7720bb7a69-tab).

## Optional Web Interface: GAIA UI (RAUX)

GAIA UI is an optional, modern web-based interface for GAIA, built on the RAUX ([Open-WebUI](https://openwebui.com/) fork) platform. It offers a feature-rich, extensible, and user-friendly experience for interacting with GAIA's AI capabilities. GAIA UI is currently in beta and is being actively integrated with new features and improvements.

> **Note:** GAIA UI is referred to as "RAUX" internally in some technical documentation and code. For most users, it is presented as "GAIA UI".

For more details and setup instructions, see the [UI Documentation](docs/ui.md).

## Contents:

- [ Introducing GAIA by AMD: Generative AI Is Awesome!](#-introducing-gaia-by-amd-generative-ai-is-awesome)
  - [Optional Web Interface: GAIA UI (RAUX)](#optional-web-interface-gaia-beta-raux)
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

## Prerequisites

**System Requirements:**

**Windows (Full Support):**
- **Windows 11 Home/Pro** 
- **16GB RAM minimum** (32GB recommended)
- **AMD Ryzen processor** (any generation)

**Linux (CLI Only):**
- **Ubuntu 20.04+** or **Debian 11+**
- **16GB RAM minimum** (32GB recommended)
- **x86_64 architecture**

**Performance Tiers:**
- **🚀 Hybrid Mode** (NPU + iGPU): Requires AMD Ryzen AI 9 HX 300 series or newer
- **⚡ Vulkan Mode**: Older Ryzen processors use llama.cpp with Vulkan acceleration via Lemonade
- **🔧 CPU Mode**: Fallback for any system without GPU acceleration

## Installation

![image](./data/img/gaia-setup.png)

**Quick Install:**
1. Download the [latest release](https://github.com/amd/gaia/releases) installer from the "Assets" section
2. Unzip and double-click `gaia-windows-setup.exe`

   ⚠️ **NOTE**: If Windows shows a security warning, click *"More info"* then *"Run anyway"*

3. Follow the installer prompts (5-10 minutes depending on internet speed)
4. The installer includes:
   - GAIA CLI and GUI applications
   - Lemonade LLM server (handles all model acceleration automatically)
   - Required models and dependencies

## Verify Installation

Once installation completes, verify everything works:

1. Double-click the **GAIA-CLI** desktop icon
2. In the command prompt, run:
   ```bash
   gaia -v
   ```
3. You should see the GAIA version number displayed

## Your First GAIA Experience

**Option 1: Quick Chat (Recommended)**
```bash
gaia chat
```
Start an interactive conversation with the AI assistant.

**Option 2: Voice Conversation**
```bash
gaia talk
```
Have a voice-based conversation with AI (includes speech recognition and text-to-speech).

**Option 3: Web Interface**
Double-click the **GAIA-GUI** desktop icon to launch the modern web interface in your browser.

**Option 4: Direct Questions**
```bash
gaia llm "What can you help me with?"
```

The first time you run GAIA, it may take a few minutes to download and load models. Subsequent uses will be much faster.

### Command-Line Installation

If you prefer to use the command-line or for CI/CD environments, you can run the installer with parameters:

```
gaia-windows-setup.exe /S
```

Available parameters:
- `/S` - Silent installation (no UI)
- `/D=<path>` - Set installation directory (must be last parameter)

### Linux Installation

For Linux systems, GAIA provides both GUI and CLI support:

**GUI Installation:**
For GAIA UI (graphical interface) installation on Linux, see the [UI Documentation](docs/ui.md#ubuntu-deb) for detailed instructions including .deb package installation.

**CLI Installation from Source:**

**Prerequisites:**
- Python 3.10+
- pip package manager
- git

**Installation Steps:**
1. Clone the repository:
   ```bash
   git clone https://github.com/amd/gaia.git
   cd gaia
   ```

2. Install GAIA CLI:
   ```bash
   pip install -e .
   ```

3. Install Lemonade server (for model serving):
   ```bash
   # Download and install Lemonade server
   # Visit https://www.lemonade-server.ai for latest Linux release
   # Or build from source following their documentation
   ```

4. Verify installation:
   ```bash
   gaia -v
   ```

**Note:** Both GUI (.deb packages) and CLI (source installation) are fully supported on Linux. 

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

## Installing Lemonade Server (Required for CLI Commands)

Most GAIA CLI commands require the Lemonade server to be running. If you installed GAIA with the installer, Lemonade server should already be included. However, you can also install it separately:

### Option 1: Standalone Installation
1. Visit [www.lemonade-server.ai](https://www.lemonade-server.ai) to download the latest release
2. Download and install `Lemonade_Server_Installer.exe` from the latest release
3. Ensure your system has the recommended Ryzen AI drivers installed (NPU Driver `32.0.203.237` or `32.0.203.240`)
4. Launch the server by double-clicking the `lemonade_server` desktop shortcut created during installation

### Option 2: Already Included with GAIA Installer
If you installed GAIA using our unified installer, Lemonade server is already included. Simply:
1. Double-click the GAIA-CLI desktop shortcut
2. Run `lemonade-server serve` in the command prompt to start the server

**Note**: The Lemonade server provides OpenAI-compatible REST API endpoints and enables hybrid NPU/iGPU acceleration on Ryzen AI systems. For more details, see the [AMD Ryzen AI documentation](https://ryzenai.docs.amd.com/en/latest/llm/server_interface.html).

## Running the GAIA CLI

To quickly get started with GAIA via the command line, you can use the GAIA CLI (`gaia`) tool. Double click on the GAIA-CLI icon to launch the command-line shell with the GAIA environment activated, then run `gaia --help` for help details.

### Quick Start Examples

**Direct LLM Queries** (fastest option, no server management required):
```bash
gaia llm "What is artificial intelligence?"
gaia llm "Explain machine learning" --model llama3.2:3b --max-tokens 200
```

**Interactive Chat Sessions**:
```bash
gaia chat                        # Start text chat with default agent
gaia chat --agent-name Blender   # Chat with Blender agent for 3D tasks
```

**Single Prompts to Agents**:
```bash
gaia prompt "Create a red cube" --agent-name Blender
gaia prompt "What's the weather?" --stats
```

**Voice Interaction**:
```bash
gaia talk  # Start voice-based conversation
```

**3D Scene Creation with Blender Agent**:
```bash
gaia blender                                    # Run all Blender examples
gaia blender --interactive                      # Interactive 3D scene creation
gaia blender --query "Create a red cube and blue sphere"  # Custom 3D scene query
gaia blender --example 2                        # Run specific example
```

### Available Commands

- `llm` - Direct LLM queries (requires Lemonade server)
- `prompt` - Send single message to an agent
- `chat` - Interactive text conversation
- `talk` - Voice-based conversation
- `blender` - Create and modify 3D scenes using the Blender agent
- `stats` - View performance statistics
- `groundtruth` - Generate evaluation data with Claude
- `test` - Run audio/speech tests
- `youtube` - Download YouTube transcripts
- `kill` - Kill processes on specific ports

**Note**: Most commands require the Lemonade server to be running. Start it by double-clicking the desktop shortcut or running `lemonade-server serve`.

**Blender Command**: The `blender` command additionally requires a Blender MCP server. See [CLI documentation](docs/cli.md#blender-command) for setup instructions.

For comprehensive information and examples, please refer to the [gaia documentation](docs/cli.md).

## Building from Source

To get started building from source, please follow the latest instructions [here](./docs/dev.md). These instructions will setup the [Onnx Runtime GenAI](https://github.com/microsoft/onnxruntime-genai) through the [Lemonade Server](https://lemonade-server.ai/) tool targeting the Ryzen AI SoC.


# Features

For a list of features and supported LLMs, please refer to the [Features](docs/features.md) page.

# Contributing

This is a new project with a codebase that is actively being developed. If you decide to contribute, please:
- Submit your contributions via a Pull Request.
- Ensure your code follows the same style as the rest of the repository.

The best way to contribute is by adding a new agent that covers a unique use-case. You can use the existing agents under the `./src/gaia/agents/` folder as a starting point. If you prefer to avoid UI development, you can add a feature to the CLI tool first. For adding features to the GUI, please refer to our [UI Development Guide](docs/ui.md).

# System Requirements

GAIA with Ryzen AI Hybrid NPU/iGPU execution has been tested on the following system below. Any system that has the AMD Ryzen AI 9 300 series processor with NPU Driver 32.0.203.237 on Windows 11 or newer with minimum of 16GB of main memory should work. For more details on what is supported, see [here](https://www.amd.com/en/products/software/ryzen-ai-software.html#tabs-2733982b05-item-7720bb7a69-tab).

⚠️ **NOTE**: 
- **Windows**: Full GUI and CLI support with installer
- **Linux**: Full GUI and CLI support via source installation
- **macOS**: Not supported at this time

GAIA has been tested on the following system:

- Systems: Asus ProArt PX13/P16, HP Omnibook with Ryzen AI 9 HX 370 series processor
- OS: Windows 11 Pro
- Processor: AMD Ryzen AI 9 HX 370 w/ Radeon 890M
- AMD Radeon 890M iGPU drivers: `32.0.12010.8007` and `32.0.12033.1030`
- AMD NPU drivers: `32.0.203.240` and newer
- AMD Adrenalin Software: Install the latest version from [AMD's official website](https://www.amd.com/en/products/software/adrenalin.html)
- Physical Memory: 32GB
- Recommended: AMD Ryzen AI 9 HX 370 with NPU Driver `32.0.203.240` and newer

⚠️ **NOTE**: Use NPU Driver `32.0.203.240` and newer. You can check your driver version by going to *Device Manager -> Neural Processors -> NPU Compute Accelerator Device -> Right-Click Properties -> Driver Tab -> Driver Version*.


## Dependencies

The GAIA installer will automatically set up most dependencies, including:
- Python 3.10
- Miniconda (if not already installed)
- FFmpeg
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
GAIA UI (RAUX) is made possible through the exceptional hard work, dedication, and innovative vision of [Tim Jaeryang Baek](https://github.com/tjbck) and the [Open-WebUI](https://openwebui.com/) team! We are deeply grateful for their outstanding open-source contributions that have enabled us to build upon their robust foundation. Their commitment to creating accessible, user-friendly AI interfaces has been instrumental in bringing GAIA UI to life. We extend our heartfelt appreciation to the entire Open-WebUI community for their continued support, collaboration, and the incredible platform they've developed that makes modern AI interactions seamless and intuitive.