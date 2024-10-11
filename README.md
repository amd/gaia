#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# GAIA: The GenAI Sandbox

Welcome to the GAIA (Generative AI Is Awesome!) project! This repository serves as a repository of AI PC demos. Primarily, it consists of local LLM chatbot and agent demos running on the RyzenAI platform.

Currently, the following are supported:

| Agent Name | Function                     |
| ---------- | ---------------------------- |
|   Chaty    | Vanilla LLM chatbot          |
|   Joker    | Simple joke generator        |
|   Clip     | YouTube search and Q&A agent |
|   Maven*   | Online research assistant    |
|    Neo*    | Chat with public GitHub repo |
|  Datalin*  | Onnx model visualizer        |
|  Picasso*  | AI art creator               |

\* bot or agent is currently under development.

## Contents:
1. [Getting Started](#getting-started)
1. [Contributing](#contributing)

# Getting Started
To get started, please follow the latest instructions [here](./docs/ort_genai.md). These instructions will setup the Onnx Runtime GenAI (ORT-GenAI) backend targeting the RyzenAI Neural Processing Unit (NPU). For legacy support, you can also use the Pytorch Eager Mode flow using the AMD transformers library described [here](./docs/ryzenai_npu.md).

NOTE: Install ollama from [here](https://ollama.com/download) if you plan to run anything else other than the above.

# Contributing
This is a very new project whose codebase is under heavy development.  If you decide to contribute, please:
- do so via a pull request.
- write your code in keeping with the same style as the rest of this repo's code.

The best way to contribute is to add a new agent that covers a unique use-case. You can use any of the agents/bots under ./agents folder as a starting point.