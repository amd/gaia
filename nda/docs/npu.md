# NPU
Thid document outlines how to get setup and running with GAIA using the NPU-only installer and executables.

## Installation Overview

GAIA installer provides complete setup for NPU execution using the ONNX GenAI backend. Each installer includes both CLI (command-line interface) and GUI (graphical user interface):

1. **GAIA_NPU_Installer.exe** - installs the GAIA app for running LLMs on the Ryzen AI NPU optimized for power efficiency.


## Current LLMs Supported
The following is a list of currently supported LLMs on Ryzen AI NPU, more will be added in the future:

| LLM                    | Checkpoint                                                            | Device   | Backend            | Data Type | GAIA Installer                              |
| -----------------------|-----------------------------------------------------------------------|----------|--------------------|-----------|---------------------------------------------|
| Phi 3.5 Mini instruct  | amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix    | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Phi-3 Mini Instruct    | amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix   | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Llama-2 7B Chat        | amd/Llama2-7b-chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix           | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Mistral 7B Instruct    | amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |
| Qwen-1.5 7B Chat       | amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix          | NPU      | oga                | int4      | GAIA_NPU_Installer.exe / GAIA_Installer.exe |

## Installation and running ORT-GenAI
1. ⚠️ NOTE: Do these steps in exactly this order using the same command shell and conda virtual environment
1. Clone GAIA repo
1. Open a powershell prompt and go to the GAIA root: `cd ./gaia`
1. Create and activate a conda environment:
    1. `conda create -n gaiaenv python=3.10`
    1. `conda activate gaiaenv`
1. Install GAIA package and dependencies:
    1. For NPU (not available publicly): `pip install -e .[npu,joker,clip,talk,dev]`
    ⚠️ NOTE: If actively developing, use `-e` switch to enable editable mode and create links to sources instead.
1. (Optional) Set the `OGA_TOKEN` environment variable if building for NPU-only. You will need a token to download the the NPU artifacts. For Hybrid, no token is needed. Contact the [GAIA team](mailto:gaia@amd.com) for support. NPU-only are currently not available publicly.
    1. `$env:OGA_TOKEN=<your_token>`
1. Install NPU dependencies:
    1. For NPU, run: `lemonade-install --ryzenai npu -y --token $env:OGA_TOKEN`
    1. ⚠️ NOTE: Make sure you are in the correct virtual environment when installing dependencies. If not, run `conda activate gaiaenv`.
1. Run `gaia-cli -v` in the terminal to verify the installation. You should see a similar output:
    ```bash
    amd/v0.7.1+cda0f5d5
    ```
    1. Run `gaia` to start the GAIA-UI or `gaia-cli -h` to see CLI options.
1. Report any issues to the GAIA team at `gaia@amd.com` or create an [issue](https://github.com/aigdat/gaia/issues) on the [GAIA GitHub repo](https://github.com/aigdat/gaia.git).

## License

Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
