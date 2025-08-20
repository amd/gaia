# GAIA (Generative AI is Awesome) FAQ

## Installation and Setup

### Can I reinstall GAIA?
Yes, you can reinstall GAIA. The installer provides an option to remove your existing installation.

### How do I run GAIA in silent/headless mode?
You can run the installer from command-line with parameters for CI/CD or silent installations:
```
gaia-windows-setup.exe /S
```
Available parameters:
- `/S` - Silent installation (no UI)
- `/D=<path>` - Set installation directory (must be last parameter)

### What are the system requirements?
GAIA is designed for AMD Ryzen AI systems. For optimal performance, you need an AMD Ryzen AI 300-series processor with NPU support.

### Does GAIA support Linux or macOS?
GAIA fully supports both Windows 11 and Linux (Ubuntu/Debian) with complete UI and CLI functionality. macOS is not currently supported.

## Demo Introduction

Discover the capabilities of Ryzen AI with GAIA, an innovative generative AI application. Our platform seamlessly runs private and local large language models (LLMs) on the Neural Processing Unit (NPU), unlocking powerful potential. Dive into our agent Retrieval-Augmented Generation (RAG) pipeline, where interactive chat, reasoning, planning, and tool use converge. Plus, experience an intuitive and accessible UI that showcases these remarkable features.

## What is GAIA, and how does it integrate with Ryzen AI?
GAIA is our generative AI application that runs local, private LLMs on Ryzen AI's NPU hardware. It's designed to leverage the power of the NPU for faster, more efficient processing, allowing users to keep their data local without relying on cloud infrastructure. This demo showcases how GAIA interacts with the NPU to run models seamlessly. GAIA uses the [Lemonade Server tool](https://lemonade-server.ai/) to load and inference the LLM.

## How does the agent RAG pipeline work in this demo?

The RAG pipeline in our demo combines an LLM with a knowledge base. The agent is capable of retrieving relevant information, reasoning, planning, and using external tools—all within an interactive chat environment. This allows for more accurate and contextually aware responses.

## What kind of LLMs are supported in the demo?

The demo supports a variety of local LLMs, which are optimized to run on Ryzen AI's NPU hardware. This includes popular models like Llama and Phi derivatives, which can be tailored for different use cases like Q&A, summarization, or even more complex reasoning tasks.

## How does the NPU enhance the performance of the LLMs?

The NPU in Ryzen AI is specialized for AI workloads, specifically the (General Matrix Multipliers) GEMMs in the model, offering faster inference times and lower power consumption compared to running LLMs on a traditional CPU or even an iGPU. By offloading the heavy lifting to the NPU, we achieve significant performance gains and reduce the load on the system.

## Can this demo be scaled to larger LLMs or enterprise applications?

Absolutely. While this demo showcases a local, private implementation, the same architecture can scale to larger models and more complex use cases. By using the NPU and optimizing the pipeline, we ensure efficient scaling for both small-scale and enterprise-level deployments.

## What are the main benefits of running LLMs locally on the NPU?

Running LLMs locally offers several benefits: enhanced privacy, as no data needs to leave your machine; reduced latency, since there's no need to communicate with the cloud; and optimized performance with the NPU, leading to faster response times and lower power consumption.

## What are the differences between running this demo on the NPU vs the iGPU?

Running the demo on the NPU provides better performance for AI-specific tasks, as it is optimized for inference workloads. The iGPU can also handle LLMs but will consume more power and might have slower inference times compared to the NPU. The NPU's AI-focused architecture is what makes the difference in efficiency and speed.

## What toolset do I need to replicate this demo?

To replicate the demo, you'll need the Ryzen AI hardware, [Lemonade Server](https://lemonade-server.ai/) for managing your LLMs, and GAIA's agent system. GAIA provides both a command-line interface (CLI) and a graphical user interface (GUI) that are available on both Windows and Linux platforms.

## How does this demo address data privacy concerns?

The demo emphasizes running LLMs locally, meaning all data remains on your device. This eliminates the need to send sensitive information to the cloud, greatly enhancing data privacy and security while still delivering high-performance AI capabilities.

## What applications or industries could benefit from this setup?

This setup could benefit industries that require high performance and privacy, such as healthcare, finance, and enterprise applications where data privacy is critical. It can also be applied in fields like content creation, customer service automation, and more, where generative AI models are becoming essential.

## Demo Components

The demo is split into two main components:

1. **GAIA Backend**: Powered by the Ryzen AI platform through Lemonade Server, which leverages NPU and iGPU capabilities. GAIA supports multiple LLM models including Llama-3.2-3B-Instruct-Hybrid. The system includes:
    - LLM client that connects to Lemonade Server for model execution
    - Agent system for specialized tasks and workflows  
    - WebSocket communication for real-time streaming responses
    - Both CLI and GUI interfaces for user interaction

2. **Agent Interface**: This agent works with the [Lemonade SDK repository](https://github.com/lemonade-sdk/lemonade). It fetches the repo, vectorizes the content, and stores it in a local vector index. On a typical laptop, indexing around 40,000 lines of code takes about 10 seconds. Once indexed, the agent is ready for queries. For example, you can ask, "How do I install dependencies?"

## Query Process

Queries are sent to GAIA, which processes them through the agent system. For RAG-based queries, the input is transformed into embeddings to retrieve relevant content from local repositories or documents. This context is then passed to the LLM via Lemonade Server for processing. The generated response is streamed back through GAIA's interfaces (CLI or GUI) in real-time, providing immediate feedback to the user.

And that's the demo. As you can see, the LLM generates a detailed response based on a large document, incorporating all the relevant information from the context provided.

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT