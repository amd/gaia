#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
#### SPDX-License-Identifier: MIT

# GAIA: The GenAI Sandbox

GAIA (Generative AI Is Awesome!) is an open-source platform that enables you to run and experiment with various Large Language Models (LLMs) locally on AMD Ryzen AI-powered devices. Whether you're a developer, researcher, or AI enthusiast, GAIA provides:

- **Local LLM Processing**: Run powerful language models directly on your device without cloud dependencies
- **Multiple Use Cases**: From basic chat to RAG-enhanced applications and specialized agents
- **Optimized Performance**: Leveraging AMD's NPU and hybrid acceleration for efficient AI processing
- **Easy-to-Use Interface**: Both CLI and GUI options for interacting with models
- **Extensible Architecture**: Build and integrate your own agents and use cases

For detailed information, see the [Frequently Asked Questions](docs/faq.md).

## Featured Capabilities

| Use-Case Example   | Function                                 | Description |
| ------------------ | ---------------------------------------- | ----------- |
| No Agent           | Test LLM using basic completion          | Direct model interaction for testing and evaluation |
| Chaty              | Vanilla LLM chatbot with message history | Interactive conversational interface with context retention |
| Joker              | Simple RAG joke generator                | Demonstrates retrieval-augmented generation capabilities |
| Clip               | YouTube search and Q&A agent             | Multi-modal agent for video content interaction |

LLMs supported:

| LLM                    | Checkpoint                                                            | Device   | Backend            | Data Type | GAIA Installer                              |
| -----------------------|-----------------------------------------------------------------------|----------|--------------------|-----------|---------------------------------------------|
| Phi-3.5 Mini Instruct  | amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Phi-3 Mini Instruct    | amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp16-onnx-hybrid        | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Llama-2 7B Chat        | amd/Llama-2-7b-chat-hf-awq-g128-int4-asym-fp16-onnx-hybrid            | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Llama-3.2 1B Instruct  | amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Llama-3.2 3B Instruct  | amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Qwen 1.5 7B Chat       | amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp16-onnx-hybrid               | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Mistral 7B Instruct    | amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp16-onnx-hybrid      | Hybrid   | oga                | int4      | GAIA_Hybrid_Installer.exe                   |
| Phi 3.5 Mini instruct  | amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix    | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Phi-3 Mini Instruct    | amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix   | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Llama-2 7B Chat        | amd/Llama2-7b-chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix           | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Mistral 7B Instruct    | amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Qwen-1.5 7B Chat       | amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix          | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Llama 3.2 1B           | llama3.2:1b                                                           | CPU/GPU  | ollama (llama.cpp) | Q8_0      | GAIA_Installer.exe                          |
| Llama 3.2 3B           | llama3.2:3b                                                           | CPU/GPU  | ollama (llama.cpp) | Q4_K_M    | GAIA_Installer.exe                          |
| Llama 3.1 8B           | llama3.2:8b                                                           | CPU/GPU  | ollama (llama.cpp) | Q4_0      | GAIA_Installer.exe                          |

* Hybrid - NPU+iGPU
* NPU - Neural Processing Unit
* oga - Onnx GenAI runtime

## Contents:
1. [Prerequisites](#prerequisites)
1. [Getting Started](#getting-started)
1. [Running the GAIA CLI](#running-the-gaia-cli)
1. [Installation](#installation)
1. [Installation Overview](#installation-overview)
1. [Featured Capabilities](#featured-capabilities)
1. [Contributing](#contributing)
1. [FAQ](#faq)

# Prerequisites
GAIA has been tested on the following system, there are no guarantees that it will work on other systems:

- System: Asus ProArt PX13 and P16
- OS Name: Microsoft Windows 11 Pro
- Processor: AMD Ryzen AI 9 HX 370 w/ Radeon 890M, 2000 Mhz, 12 Core(s), 24 Logical Processor(s)
- Physical Memory: 32.0 GB
- AMD Radeon 890M iGPU driver: 32.0.12010.8007
- AMD NPU driver: 32.0.203.237 or 32.0.203.240

# Getting Started

For a quick and easy setup on a new machine, please install the latest version of the GAIA app from the [releases page](https://github.com/aigdat/gaia/releases) or [AMD sharepoint](https://amdcloud.sharepoint.com/:f:/r/sites/AIM/Shared%20Documents/Developer%20Acceleration/GAIA/Releases?csf=1&web=1&e=RxgwPa).

The installation process takes about 5-10 minutes and includes everything needed to get up and running with GAIA on Ryzen AI NPU. Read below for more details on the installation process.

## Installation Overview

GAIA installers provide complete setup for NPU, GPU, and Hybrid (NPU+iGPU) execution using the ONNX GenAI backend. Each installer includes both CLI (command-line interface) and GUI (graphical user interface) and comes in three variants:

1. **GAIA_Hybrid_Installer.exe** - The recommended installer that offers the best performance, running LLMs on both NPU and iGPU devices.
1. **GAIA_NPU_Installer.exe** - installs the GAIA app for running LLMs on the Ryzen AI NPU optimized for power efficiency.
1. **GAIA_Installer.exe** - installs the GAIA app for running LLMs on the Ryzen AI NPU and a third party backend called Ollama for more generic use  cases.

⚠️ NOTE: When running GAIA using the Hybrid mode, please make sure to disable any discrete third-party GPUs in Device Manager.

The installer performs these key steps:
1. **Environment Check**
   - Verifies/installs Miniconda
   - Checks for existing GAIA installations
   - Validates hardware compatibility

2. **Driver Management** (NPU/Hybrid modes)
   - Checks current Ryzen AI driver version
   - Updates driver if needed

3. **Core Installation**
   - Creates Python environment
   - Installs GAIA framework
   - Sets up mode-specific components

4. **Final Configuration**
   - Downloads LLM artifacts
   - Configures settings
   - Creates shortcuts

For more details, please refer to the [installer documentation](docs/installer.md).

## Installation

To install the GAIA app, please follow these steps:
1. Download the [latest release](https://github.com/aigdat/gaia/releases) of the GAIA installers from the "Assets" section or use [AMD sharepoint](https://amdcloud.sharepoint.com/:f:/r/sites/AIM/Shared%20Documents/Developer%20Acceleration/GAIA/Releases?csf=1&web=1&e=RxgwPa):
   ![image](./data/img/gaia-installer.png)

2. Unzip the downloaded file and run the installer by double-clicking the .exe file.

3. If you get a Windows Security warning, you can verify the application by clicking *"More info"* and then *"Run anyway"*. This warning appears because the application is not yet digitally signed.

4. Follow the on-screen instructions to complete the installation:

   ![image](./data/img/gaia-setup.png)

5. Once installation is complete, a GAIA desktop icon will be created for easy access.

⚠️ NOTE: The installation process may take 10-20 minutes, as it sets up all necessary components for a seamless GAIA experience.

## Building from source
To get started building from source, please follow the latest instructions [here](./docs/ort_genai.md). These instructions will setup the Onnx Runtime GenAI (ORT-GenAI) backend targeting the Ryzen AI Neural Processing Unit (NPU). For legacy support, you can also use the Pytorch Eager Mode flow using the AMD transformers library described [here](./docs/ryzenai_npu.md).

⚠️ NOTE: You may need to install Ollama from [here](https://ollama.com/download) if you plan to run models with the Ollama backend.

# Running the GAIA GUI
Check your desktop for the GAIA icon and double-click it to launch the GUI. It may take a few minutes to start the first time. Subsequent launches are faster. You may also need to download the latest LLM models from Hugging Face, GAIA will do this automatically but may request a Hugging Face token for access. Contact the [GAIA team](mailto:gaia@amd.com) if you are having any issues with model downloads or the GUI application.

# Running the GAIA CLI
To quickly get started with GAIA via the command line, you can use the GAIA CLI (`gaia-cli`) tool. Run `gaia-cli -h` for help details. For more information and examples, please refer to the [gaia-cli documentation](docs/cli.md).

# GAIA-CLI Talk Mode
For detailed instructions on using GAIA-CLI's voice interaction capabilities, including configuration options and voice commands, please refer to our [Talk Mode Guide](docs/talk.md).

## Utility Functions

### Download YouTube Transcripts
You can download transcripts from YouTube videos directly using the CLI:
```bash
gaia-cli --download-transcript "https://www.youtube.com/watch?v=VIDEO_ID"
```

By default, this saves the transcript to `transcript.txt`. You can specify a custom output file:
```bash
gaia-cli --download-transcript "https://www.youtube.com/watch?v=VIDEO_ID" --output "my_transcript.txt"
```

# Contributing
This is a very new project with a codebase that is under heavy development.  If you decide to contribute, please:
- do so via a pull request.
- write your code in keeping with the same style as the rest of this repo's code.

The best way to contribute is to add a new agent that covers a unique use-case. You can use any of the agents/bots under ./agents folder as a starting point.

## UI Development
If you're interested in contributing to GAIA's user interface, we provide a comprehensive guide for UI development using Qt Designer. This guide covers:
- Setting up the UI development environment
- Using Qt Designer to modify the interface
- Compiling and testing UI changes
- Working with assets and resources

For detailed instructions, please refer to our [UI Development Guide](docs/ui.md).

# Contact
Contact [GAIA Team](mailto:gaia@amd.com) for any questions, feature requests, access or troubleshooting issues.

# License
This project is licensed under the terms of the MIT license. See LICENSE.md for details.