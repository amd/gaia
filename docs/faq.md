# GAIA (Generative AI is Awesome) FAQ

## Installation and Setup

### Can I change the installation mode after installing?
Yes, you can reinstall GAIA and select a different mode. The installer provides an option to remove your existing installation.

### How do I run GAIA in silent/headless mode?
You can run the installer from command-line with parameters for CI/CD or silent installations:
```
gaia-windows-setup.exe /S /MODE=HYBRID
```
Available parameters:
- `/S` - Silent installation (no UI)
- `/MODE=X` - Set installation mode (GENERIC, NPU, or HYBRID)
- `/D=<path>` - Set installation directory (must be last parameter)

### Why are some installation modes disabled?
The installer automatically detects your CPU and only enables compatible modes. NPU and Hybrid modes require an AMD Ryzen AI 300-series processor. If these options are disabled, your system doesn't meet the hardware requirements.

### Does GAIA support Linux or macOS?
Currently, GAIA only supports Windows 11. Support for other platforms may be added in future releases.

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

To replicate the demo, you'll need the Ryzen AI hardware, [Lemonade Server](https://lemonade-server.ai/) for managing your LLMs, and our agent RAG pipeline, which you can access via Jupyter notebooks in the demo. Additionally, you'll need the necessary software stack, including Ryzen AI Software for the. The GAIA interface (GUI) will be available publicly soon.

## How does this demo address data privacy concerns?

The demo emphasizes running LLMs locally, meaning all data remains on your device. This eliminates the need to send sensitive information to the cloud, greatly enhancing data privacy and security while still delivering high-performance AI capabilities.

## What applications or industries could benefit from this setup?

This setup could benefit industries that require high performance and privacy, such as healthcare, finance, and enterprise applications where data privacy is critical. It can also be applied in fields like content creation, customer service automation, and more, where generative AI models are becoming essential.

## Demo Components

The demo is split into two main components:

1. **Web Service**: Powered by the Ryzen AI platform, which handles both the NPU and CPU. It's running a quantized version of Llama 2 with 7 billion parameters. This service communicates via a WebSocket stream to the Gaia app, which has three core elements:
    - An LLM connector that links the NPU service's Web API to the LlamaIndex RAG (Retrieval-Augmented Generation) pipeline.
    - The LlamaIndex RAG pipeline, consisting of a local query engine and vector memory, which processes information and runs on a web server.
    - This web server is also connected via WebSocket to the AI Demo Hub UI, where users can interact with the system.

2. **Agent Interface**: This agent works with the [Lemonade SDK repository](https://github.com/lemonade-sdk/lemonade). It fetches the repo, vectorizes the content, and stores it in a local vector index. On a typical laptop, indexing around 40,000 lines of code takes about 10 seconds. Once indexed, the agent is ready for queries. For example, you can ask, "How do I install dependencies?"

## Query Process

The query is sent to the Gaia app, where it's transformed into an embedding vector. This vector is used to retrieve relevant chunks of the local GitHub repository, which are then passed to the NPU service. You can see the context being embedded into the LLM based on the query. This embedded context is then used to generate an answer, which is streamed back through the Gaia web service. The response is sent from the right side of the system to the Gaia web service on the left, and finally displayed in the UI. Once the process is complete, the user receives the final answer.

And that's the demo. As you can see, the LLM generates a detailed response based on a large document, incorporating all the relevant information from the context provided.

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT