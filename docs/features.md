# Features

Currently, the following capabilities are available, more will be added in the near future:

| Use-Case Example   | Function                                 | Description                                                     |
| ------------------ | ---------------------------------------- | --------------------------------------------------------------- |
| No Agent           | Test LLM using basic completion          | Direct model interaction for testing and evaluation             |
| Chaty              | Basic LLM chatbot with message history | Interactive conversational interface with context retention     |
| Joker              | Simple RAG joke generator                | Demonstrates retrieval-augmented generation capabilities        |
| Clip               | YouTube search and Q&A agent             | Basic agent for YouTube video transcript Q&A (requires YouTube API key) |

## Supported LLMs

The following is a list of the currently supported LLMs with GAIA using Ryzen AI Hybrid (NPU+iGPU) mode using `gaia-windows-setup.exe`. To request support for a new LLM, please contact the [AMD GAIA team](mailto:gaia@amd.com).

| LLM                    | Checkpoint                                                            | Backend            | Data Type |
| -----------------------|-----------------------------------------------------------------------|--------------------|-----------|
| Phi-3.5 Mini Instruct  | amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | oga                | int4      |
| Phi-3 Mini Instruct    | amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp16-onnx-hybrid        | oga                | int4      |
| Llama-2 7B Chat        | amd/Llama-2-7b-chat-hf-awq-g128-int4-asym-fp16-onnx-hybrid            | oga                | int4      |
| Llama-3.2 1B Instruct  | amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | oga                | int4      |
| Llama-3.2 3B Instruct  | amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | oga                | int4      |
| Qwen 1.5 7B Chat       | amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp16-onnx-hybrid               | oga                | int4      |
| Mistral 7B Instruct    | amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp16-onnx-hybrid      | oga                | int4      |

The following is a list of the currently supported LLMs in the generic version of GAIA (GAIA_Installer.exe). To request support for a new LLM, please contact the [AMD GAIA team](mailto:gaia@amd.com).
| LLM                    | Checkpoint                                                            | Device   | Backend            | Data Type |
| -----------------------|-----------------------------------------------------------------------|----------|--------------------|-----------|

* oga - [Onnx Runtime GenAI](https://github.com/microsoft/onnxruntime-genai)

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT